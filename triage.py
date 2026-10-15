#!/usr/bin/env python3
"""
triage.py — Standalone MES -> LLM -> Odoo Chatter triage bridge.

What it does (single file, no Odoo module, no Odoo source changes):
    1. Reads credentials/config from .env
    2. Authenticates against Odoo over XML-RPC (xmlrpc/2/common) and builds an
       object proxy (xmlrpc/2/object)
    3. Fetches a target Manufacturing Order (mrp.production) and simulates an
       unstructured operator defect note
    4. Sends that note directly to the OpenAI API and
       forces a structured JSON verdict
    5. Renders the verdict as HTML and writes it back to the order's Chatter by
       creating a mail.message record linked to mrp.production

TWO MODES
    One-shot (default): you supply the note (or accept the simulated default).
    Watch (--watch):    long-running poller. It discovers real operator notes
                        posted in any mrp.production Chatter, strips their HTML,
                        triages them, and replies in the same thread — once per
                        note. It never re-triages its own output, survives
                        crashes via a checkpoint file, and dead-letters notes it
                        cannot process. See plans/watch-mode-design.md.

Usage:
    python triage.py                      # default MO id = 2
    python triage.py --mo 5               # a different Manufacturing Order
    python triage.py --note "..."         # override the raw operator note
    python triage.py --dry-run            # call the LLM, do NOT write to Odoo

    python triage.py --watch              # poll new Chatter notes forever
    python triage.py --watch --interval 5 --max-iterations 12   # bounded demo
    python triage.py --watch --backfill 10                      # replay last 10
    python triage.py --watch --since-id 4800                    # replay from an id
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import logging
import os
import random
import re
import signal
import sys
import threading
import time
import xmlrpc.client
from collections import deque
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Iterable, TypeVar

from dotenv import load_dotenv
from openai import OpenAI

T = TypeVar("T")

# Transient OpenAI failures worth retrying. The class names have been stable in
# the SDK for a long time, but the import is guarded so a future reshuffle can
# never stop triage.py from importing at all.
try:  # pragma: no cover - depends on the installed SDK
    from openai import (
        APIConnectionError,
        APITimeoutError,
        InternalServerError,
        RateLimitError,
    )

    _OPENAI_RETRYABLE: tuple[type[BaseException], ...] = (
        APIConnectionError,
        APITimeoutError,
        InternalServerError,
        RateLimitError,
    )
except Exception:  # pragma: no cover
    _OPENAI_RETRYABLE = ()

# Only *transient* failures are retried. xmlrpc.client.Fault (ACL denied, bad
# field, validation error) is deliberately NOT in this tuple: retrying a
# deterministic rejection just burns time before the same failure.
RETRYABLE_EXCEPTIONS: tuple[type[BaseException], ...] = (
    xmlrpc.client.ProtocolError,
    OSError,          # covers ConnectionError / TimeoutError / socket errors
) + _OPENAI_RETRYABLE

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

DEFAULT_MO_ID = 2
DEFAULT_RAW_NOTE = (
    "Tooling chatter on step 2, tool insert T3 chipped, surface roughness high"
)

# Chatter message subtype, given as an XML ID ("module.name").
#   mail.mt_note    -> internal note (yellow, no follower notification) [default]
#   mail.mt_comment -> regular comment
# NOTE: Odoo 17's mail.message has NO `subtype_xmlid` field (that only exists on
# some wizards). We must resolve the XML ID to a numeric subtype_id ourselves.
DEFAULT_CHATTER_SUBTYPE = "mail.mt_note"

SYSTEM_PROMPT = """You are a senior manufacturing reliability engineer triaging \
defects reported from an Odoo Manufacturing Execution System (shop floor).

You receive short, unstructured, often ungrammatical operator notes. Infer the \
most probable machine/tooling failure and respond with a single JSON object.

Rules:
- "iso_code": a standardized machine failure code, formatted "<PREFIX>-<NNN>". \
Use these prefixes: TW (tooling wear/damage), MECH (mechanical), ELEC (electrical), \
PROC (process deviation), MAT (material defect). Example: "TW-012".
- "severity": exactly one of "LOW", "MEDIUM", "CRITICAL".
  * LOW      = cosmetic / no impact on dimensional or surface quality.
  * MEDIUM   = quality risk, rework likely, production can continue short term.
  * CRITICAL = scrap/safety risk, stop the operation immediately.
- "action_steps": exactly 3 short, physical, checkable actions for the operator. \
Each step must be imperative, shop-floor friendly, mention the concrete \
component/tool (e.g. insert T3) where possible, and include a measurable \
criterion or acceptance limit where it makes sense. Max ~140 characters each.
- Never invent part numbers. If a detail is absent from the note, keep the step generic.
- Output JSON only. No markdown, no prose, no code fences.

Required JSON shape:
{
  "iso_code": "TW-012",
  "severity": "MEDIUM",
  "action_steps": ["...", "...", "..."]
}"""

USER_PROMPT_TEMPLATE = """Manufacturing Order: {mo_name} (id={mo_id})
Product: {product}
Raw operator defect note:
\"\"\"{note}\"\"\"

Return the triage JSON object now."""

ALLOWED_SEVERITY = ("LOW", "MEDIUM", "CRITICAL")

SEVERITY_COLOR = {
    "LOW": "#2e7d32",       # green
    "MEDIUM": "#e65100",    # orange
    "CRITICAL": "#c62828",  # red
}

# --- Watch mode (see plans/watch-mode-design.md) -----------------------------
#
# The model we watch the Chatter of. Operator notes are discovered as
# mail.message rows whose `model` is this value.
WATCH_MODEL = "mrp.production"

DEFAULT_WATCH_INTERVAL = 10.0        # seconds between polls
DEFAULT_WATCH_BATCH_SIZE = 20        # max mail.message rows per poll
DEFAULT_STATE_FILE = "logs/watch_state.json"
DEFAULT_FAILURES_FILE = "logs/triage_watch_failures.jsonl"
DEFAULT_ON_ERROR = "skip"            # skip | halt   (poison-message policy)
DEFAULT_MESSAGE_TYPES = ("comment", "email")

# Skip Chatter notes authored by the *same partner we authenticate as*.
# Default True: our own replies are authored by us, and ignoring them by author
# is free. Odoo's usual single-admin setup breaks this assumption (a human
# typing in the Chatter IS that user), so --allow-self-authored disables it —
# the AI-marker guard still prevents an AI-triaging-AI loop, this is only the
# belt-and-braces layer.
DEFAULT_SKIP_SELF_AUTHORED = True

DEDUPE_RING_SIZE = 200               # ids + content hashes kept in the checkpoint
MIN_NOTE_CHARS = 4                   # shorter than this = attachment/emoji-only note
MAX_NOTE_CHARS = 1500                # prompt-cost cap on the stripped note

RETRY_ATTEMPTS = 3
RETRY_BASE_DELAY = 1.5               # seconds; doubles per attempt, plus jitter
MAX_BACKOFF_SECONDS = 300.0          # poll-level backoff ceiling
HEARTBEAT_SECONDS = 120.0            # "still alive" log line while idle

# Written into the HTML of every AI reply. The watcher combines these with the
# author id to recognise (and ignore) its own output — see is_ai_message().
#
# NOTE: Odoo sanitises mail.message.body on the way in and *removes custom HTML
# attributes*, so `data-ai-triage` does NOT survive a round-trip through the
# database. It is kept for instances that do not sanitise, but the guard cannot
# rely on it: the plain-text token below does survive, which is why the reply
# carries a visible, greppable token as well.
TRIAGE_MARKER = "data-ai-triage"
AI_TRIAGE_TOKEN = "[ai-triage:v1]"
AI_TRIAGE_HEADING = "🤖 AI Triage"

log = logging.getLogger("triage")


# --------------------------------------------------------------------------- #
# Step 1a — configuration
# --------------------------------------------------------------------------- #

def load_settings() -> dict[str, str]:
    """Load and validate configuration from .env / environment."""
    load_dotenv()

    # Accept both ODOO_USER (assignment spec) and ODOO_USERNAME (existing repo).
    settings = {
        "odoo_url": os.getenv("ODOO_URL", "http://localhost:8069").rstrip("/"),
        "odoo_db": os.getenv("ODOO_DB", "mes_lab"),
        "odoo_user": os.getenv("ODOO_USER") or os.getenv("ODOO_USERNAME") or "admin",
        "odoo_password": os.getenv("ODOO_PASSWORD", "admin"),
        "openai_api_key": os.getenv("OPENAI_API_KEY", ""),
        "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        "chatter_subtype": os.getenv("CHATTER_SUBTYPE", DEFAULT_CHATTER_SUBTYPE),
        # --- watch mode (CLI flags take precedence over these) ---------------
        "watch_interval": os.getenv("WATCH_INTERVAL", str(DEFAULT_WATCH_INTERVAL)),
        "watch_batch_size": os.getenv("WATCH_BATCH_SIZE", str(DEFAULT_WATCH_BATCH_SIZE)),
        "watch_state_file": os.getenv("WATCH_STATE_FILE", DEFAULT_STATE_FILE),
        "watch_failures_file": os.getenv("WATCH_FAILURES_FILE", DEFAULT_FAILURES_FILE),
        "watch_on_error": os.getenv("WATCH_ON_ERROR", DEFAULT_ON_ERROR),
        "watch_skip_self_authored": os.getenv(
            "WATCH_SKIP_SELF_AUTHORED",
            "true" if DEFAULT_SKIP_SELF_AUTHORED else "false",
        ),
        "watch_message_types": os.getenv(
            "WATCH_MESSAGE_TYPES", ",".join(DEFAULT_MESSAGE_TYPES)
        ),
    }

    missing = [k for k in ("openai_api_key",) if not settings[k]]
    if missing:
        raise SystemExit(
            "Missing configuration: "
            + ", ".join(m.upper() for m in missing)
            + ". Fill them in your .env file (see .env.example)."
        )
    return settings


# --------------------------------------------------------------------------- #
# Step 1b — Odoo XML-RPC bridge
# --------------------------------------------------------------------------- #

class OdooBridge:
    """Minimal XML-RPC client for Odoo (external, no addon required)."""

    def __init__(self, url: str, db: str, user: str, password: str) -> None:
        self.url = url
        self.db = db
        self.user = user
        self.password = password

        # Two distinct XML-RPC endpoints are used by Odoo:
        #   /xmlrpc/2/common  -> authentication + version, no DB context needed
        #   /xmlrpc/2/object  -> all model operations (search_read, create, ...)
        self.common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common", allow_none=True)
        self.models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object", allow_none=True)
        self.uid: int | None = None

    def authenticate(self) -> int:
        """Return the Odoo user id, or raise on bad credentials."""
        try:
            version = self.common.version()
        except Exception as exc:  # connection refused, wrong URL, etc.
            raise SystemExit(
                f"Cannot reach Odoo at {self.url} ({exc}). Is Docker Compose up on port 8069?"
            ) from exc

        self.uid = self.common.authenticate(self.db, self.user, self.password, {})
        if not self.uid:
            raise SystemExit(
                f"Odoo authentication failed for db={self.db!r} user={self.user!r}. "
                "Check your credentials (see scripts/reset_odoo_password.sh)."
            )
        log.info("Odoo %s | authenticated as uid=%s", version.get("server_version"), self.uid)
        return self.uid

    def execute_kw(self, model: str, method: str, args: list, kwargs: dict | None = None) -> Any:
        """Thin wrapper around the Odoo `execute_kw` JSON/XML-RPC entry point."""
        if self.uid is None:
            raise RuntimeError("Not authenticated — call authenticate() first.")
        return self.models.execute_kw(
            self.db, self.uid, self.password, model, method, args, kwargs or {}
        )

    # --- convenience helpers -------------------------------------------------

    def read_mo(self, mo_id: int) -> dict[str, Any]:
        """Fetch one Manufacturing Order, failing with a helpful message."""
        records = self.execute_kw(
            "mrp.production",
            "read",
            [[mo_id]],
            {"fields": ["name", "state", "product_id", "product_qty", "date_start"]},
        )
        if not records:
            # Could also be an access-rights block rather than a missing record.
            raise SystemExit(
                f"mrp.production id={mo_id} not found (or not readable) on {self.url}. "
                "Try `python triage.py --mo <id>` with an id that exists in your DB."
            )
        return records[0]

    def resolve_xmlid(self, xmlid: str) -> int | None:
        """
        Turn an XML ID such as 'mail.mt_note' into the numeric record id.

        mail.message.subtype records are data, not code: 'mail.mt_note' is only
        an XML ID, and the ORM field `subtype_id` wants the integer 2. Returns
        None if the XML ID is unknown (the message is then posted without a
        subtype instead of failing).
        """
        module, _, name = xmlid.partition(".")
        rows = self.execute_kw(
            "ir.model.data",
            "search_read",
            [[["module", "=", module], ["name", "=", name]]],
            {"fields": ["res_id", "model"], "limit": 1},
        )
        if not rows:
            log.warning("XML ID %r not found — posting without a subtype.", xmlid)
            return None
        return rows[0]["res_id"]

    def post_chatter_message(self, mo_id: int, body_html: str,
                             subtype_xmlid: str = DEFAULT_CHATTER_SUBTYPE,
                             parent_id: int | None = None) -> int:
        """
        Write the triage result into the record's Chatter.

        This creates a `mail.message` row linked to the mrp.production record.
        `parent_id` threads the reply underneath the operator note it answers
        (used by --watch). Returns the new mail.message id.
        """
        author_id = self.author_partner_id()

        values = {
            "model": "mrp.production",   # which model the message hangs off
            "res_id": mo_id,             # which record id
            "message_type": "comment",   # 'comment' = user message; 'notification' = system
            "body": body_html,           # HTML fragment
            "author_id": author_id,      # who the Chatter shows as the author
            "partner_ids": [(6, 0, [])], # (6,0,[]) = replace recipients with none -> no email
        }
        if parent_id:
            values["parent_id"] = parent_id
        subtype_id = self.resolve_xmlid(subtype_xmlid)
        if subtype_id:
            values["subtype_id"] = subtype_id

        try:
            message_id = self.execute_kw("mail.message", "create", [values])
        except xmlrpc.client.Fault as fault:
            # Some hardened instances deny direct writes on mail.message (ACL).
            # Fall back to the official, ACL-aware helper on the record itself,
            # which creates the mail.message for us server-side.
            log.warning("mail.message.create refused (%s). Falling back to message_post().",
                        str(fault).splitlines()[-1][:160])
            kwargs: dict[str, Any] = {
                "body": body_html,
                "message_type": "comment",
                "subtype_xmlid": subtype_xmlid,
            }
            if parent_id:
                kwargs["parent_id"] = parent_id
            message_id = self.execute_kw("mrp.production", "message_post", [[mo_id]], kwargs)
        log.info("Chatter updated: mail.message id=%s on mrp.production(%s)", message_id, mo_id)
        return message_id

    # --- watch-mode helpers --------------------------------------------------

    def author_partner_id(self) -> int | None:
        """
        Partner id of the authenticated user, memoised.

        author_id on mail.message is a *partner* id, not the res.users id
        returned by authenticate(). The watcher needs it to recognise its own
        messages, so resolve once and cache.
        """
        if getattr(self, "_partner_id", None) is None:
            rows = self.execute_kw("res.users", "read", [[self.uid]], {"fields": ["partner_id"]})
            self._partner_id = (
                rows[0]["partner_id"][0] if rows and rows[0]["partner_id"] else None
            )
        return self._partner_id

    def read_mo_or_none(self, mo_id: int) -> dict[str, Any] | None:
        """Like read_mo() but returns None instead of exiting — for the watcher."""
        rows = self.execute_kw(
            "mrp.production",
            "read",
            [[mo_id]],
            {"fields": ["name", "state", "product_id", "product_qty", "date_start"]},
        )
        return rows[0] if rows else None

    def max_message_id(self, model: str = WATCH_MODEL) -> int:
        """Highest mail.message id on `model` — used to bootstrap the cursor."""
        rows = self.execute_kw(
            "mail.message",
            "search_read",
            [[["model", "=", model]]],
            {"fields": ["id"], "order": "id desc", "limit": 1},
        )
        return rows[0]["id"] if rows else 0

    def fetch_messages(self, model: str, cursor: int, limit: int,
                       message_types: Iterable[str]) -> list[dict[str, Any]]:
        """
        New messages on `model` with id strictly greater than `cursor`.

        `message_type` is the discriminator between operator input and Odoo's own
        workflow noise: operator notes are 'comment'/'email', while tracking and
        state-change messages are 'notification'/'user_notification'.
        """
        return self.execute_kw(
            "mail.message",
            "search_read",
            [[["model", "=", model],
              ["id", ">", cursor],
              ["message_type", "in", list(message_types)]]],
            {
                "fields": ["id", "res_id", "body", "author_id", "message_type",
                           "subtype_id", "date", "parent_id"],
                "order": "id asc",
                "limit": limit,
            },
        )


# --------------------------------------------------------------------------- #
# Step 3 — LLM structured-output call
# --------------------------------------------------------------------------- #

def _extract_json(text: str) -> dict[str, Any]:
    """Parse a JSON object, tolerating ```json fences or stray prose."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


def _normalise(data: dict[str, Any]) -> dict[str, Any]:
    """Coerce/normalise the model output so a downstream write never explodes."""
    iso_code = str(data.get("iso_code") or "UNCL-999").strip().upper()

    severity = str(data.get("severity") or "MEDIUM").strip().upper()
    if severity not in ALLOWED_SEVERITY:
        severity = "MEDIUM"

    raw_steps = data.get("action_steps") or []
    if isinstance(raw_steps, str):  # model returned one big string
        raw_steps = [s.strip(" -•\t") for s in raw_steps.splitlines() if s.strip()]
    steps = [str(s).strip() for s in raw_steps if str(s).strip()][:3]
    while len(steps) < 3:  # guarantee exactly 3, as required by the schema
        steps.append("Record the observed condition and halt the operation pending maintenance review.")

    return {"iso_code": iso_code, "severity": severity, "action_steps": steps}


def triage_note(client: OpenAI, model: str, mo: dict[str, Any], note: str) -> dict[str, Any]:
    """Send the raw note to the LLM and return the normalised verdict dict."""
    product = mo.get("product_id")
    product_name = product[1] if isinstance(product, (list, tuple)) and len(product) > 1 else "N/A"

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": USER_PROMPT_TEMPLATE.format(
                mo_name=mo.get("name", "N/A"),
                mo_id=mo.get("id", "N/A"),
                product=product_name,
                note=note,
            ),
        },
    ]

    log.info("Calling OpenAI model %r", model)
    try:
        # json_object is the most widely supported structured-output mode on
        # OpenAI-compatible routers (json_schema is stricter but not universal).
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=600,
        )
    except Exception as exc:
        log.warning("Structured-output request rejected (%s). Retrying without response_format.", exc)
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.2,
            max_tokens=600,
        )

    raw = response.choices[0].message.content or ""
    log.debug("Raw model output: %s", raw)
    return _normalise(_extract_json(raw))


# --------------------------------------------------------------------------- #
# Step 4 — HTML rendering for the Chatter
# --------------------------------------------------------------------------- #

def render_html(verdict: dict[str, Any], note: str, model: str,
                source_message_id: int | None = None) -> str:
    """
    Build the HTML fragment stored in mail.message.body.

    The whole fragment is wrapped in a div carrying TRIAGE_MARKER. That marker
    (plus the AI heading) is how --watch recognises its own output and refuses
    to triage it again — see is_ai_message().
    """
    e = html.escape  # never trust model output inside HTML
    sev = verdict["severity"]
    colour = SEVERITY_COLOR.get(sev, "#555555")

    steps_html = "".join(f"<li>{e(step)}</li>" for step in verdict["action_steps"])

    source = (
        f"<p><small>In reply to Chatter message id={source_message_id}</small></p>"
        if source_message_id else ""
    )

    return (
        f'<div {TRIAGE_MARKER}="v1">'
        f"<p><b>{e(AI_TRIAGE_HEADING)} — Operational Defect</b></p>"
        f"<p><b>Reported note:</b> <i>{e(note)}</i></p>"
        f"<p><b>Failure code (ISO):</b> <code>{e(verdict['iso_code'])}</code><br/>"
        f"<b>Severity:</b> <span style=\"color:{colour};font-weight:bold;\">{e(sev)}</span></p>"
        f"<p><b>Recommended operator checks:</b></p>"
        f"<ul>{steps_html}</ul>"
        f"<p><small>Generated automatically by triage.py using model "
        f"<code>{e(model)}</code>. Human verification required before "
        f"restarting the work center. {AI_TRIAGE_TOKEN}</small></p>"
        f"{source}"
        "</div>"
    )


def is_ai_message(body: str) -> bool:
    """
    True if this body was produced by us.

    Deliberately independent of the author-id guard: it also catches a human
    copy-pasting an AI verdict back into a note (which we must not re-triage),
    and it keeps working if the AI user's partner id ever changes.

    Only *plain text* markers are trusted — Odoo's HTML sanitiser strips custom
    attributes, so a `data-*` marker alone would be silently lost.
    """
    if not body:
        return False
    lowered = body.lower()
    return (
        AI_TRIAGE_TOKEN in lowered
        or TRIAGE_MARKER in body
        or AI_TRIAGE_HEADING in body
        or ("ai triage" in lowered and "operational defect" in lowered)
    )


# --------------------------------------------------------------------------- #
# Watch mode, part 1 — HTML -> plain text
# --------------------------------------------------------------------------- #

class _HTMLTextExtractor(HTMLParser):
    """
    Minimal HTML -> text converter (stdlib only, no beautifulsoup/lxml).

    Chatter bodies are fragments: <p>, <br/>, <span class="o_attachment">,
    entities (&nbsp;, &#39;), and quoted-reply wrappers. We only need clean
    prose for the LLM prompt, so block-level tags become newlines and inline
    tags disappear.
    """

    # Opening/closing these implies a line break in the extracted text.
    BLOCK_TAGS = {
        "p", "div", "br", "li", "ul", "ol", "tr", "td", "th", "table",
        "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "hr",
    }
    # Their contents are code/config, never operator prose.
    DROP_TAGS = {"script", "style", "head", "title"}
    # Odoo renders attachments and its own boilerplate inside these classes.
    # An attachment-only message must collapse to "" so the watcher skips it
    # instead of paying for an LLM call on the filename "report.pdf".
    DROP_CLASS_HINTS = (
        "o_attachment", "o-mail-attachment", "o_attachment_delete",
        "o_mail_snippet_general", "o_signature",
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        # One entry per open element: True means "suppress text inside".
        self._stack: list[bool] = []

    def _should_drop(self, tag: str,
                     attrs: list[tuple[str, str | None]]) -> bool:
        if tag in self.DROP_TAGS:
            return True
        classes = " ".join(
            v or "" for k, v in attrs if k in ("class", "id")
        ).lower()
        return any(hint in classes for hint in self.DROP_CLASS_HINTS)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        dropping = self._should_drop(tag, attrs)
        self._stack.append(dropping)
        if not dropping and tag in self.BLOCK_TAGS:
            self._parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if not self._should_drop(tag, attrs) and tag in self.BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        dropping = self._stack.pop() if self._stack else False
        if not dropping and tag in self.BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not any(self._stack):
            self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


def html_to_text(body: str | None) -> str:
    """
    Strip HTML from a Chatter body and return a clean, prompt-ready note.

    Guarantees: entities decoded, whitespace collapsed, blank-line runs
    squeezed, leading/trailing space removed, length capped at MAX_NOTE_CHARS
    (tail truncated) to bound the token cost of an unexpectedly huge quote.
    """
    if not body:
        return ""

    parser = _HTMLTextExtractor()
    try:
        parser.feed(body)
        parser.close()
        raw = parser.text()
    except Exception as exc:  # malformed markup must never kill the watcher
        log.debug("HTML parse failed (%s); falling back to regex strip.", exc)
        raw = re.sub(r"<[^>]+>", "\n", body)

    # A message with no tags at all still needs entity decoding.
    raw = html.unescape(raw)
    raw = raw.replace("\xa0", " ")
    raw = re.sub(r"[ \t\r\f\v]+", " ", raw)          # collapse horizontal space
    raw = re.sub(r"\n\s*\n\s*(\n\s*)*", "\n\n", raw)  # collapse blank-line runs
    lines = [line.strip() for line in raw.split("\n")]
    note = "\n".join(lines).strip()

    if len(note) > MAX_NOTE_CHARS:
        log.debug("Note truncated from %d to %d chars.", len(note), MAX_NOTE_CHARS)
        note = note[:MAX_NOTE_CHARS].rstrip() + " …[truncated]"
    return note


def note_fingerprint(res_id: int, note: str) -> str:
    """
    Content hash used for duplicate detection.

    Keyed on (record id, normalised text) so the *same* note on a different MO
    is still triaged, while an operator double-posting the identical text on
    the same MO is not.
    """
    digest = hashlib.sha1(f"{res_id}:{note}".encode("utf-8", "replace"))
    return digest.hexdigest()[:16]


# --------------------------------------------------------------------------- #
# Watch mode, part 2 — durable checkpoint
# --------------------------------------------------------------------------- #

class WatchState:
    """
    Crash-safe processing checkpoint for --watch.

    Design notes (see plans/watch-mode-design.md §5):
      * `last_message_id` is the only thing correctness depends on. mail.message
        ids are globally monotonic, so a single integer is a valid cursor.
      * `processed_ids` (bounded ring) makes the watcher idempotent even if the
        cursor is reset by hand or a message reappears.
      * `processed_hashes` (bounded ring) catches identical notes re-posted by
        an operator and duplicate work from a second watcher instance.
      * Saving is atomic (tmp + fsync + os.replace); a partial write can never
        corrupt the checkpoint.
    """

    SCHEMA = 1

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.last_message_id: int = 0
        self.processed_ids: deque[int] = deque(maxlen=DEDUPE_RING_SIZE)
        self.processed_hashes: deque[str] = deque(maxlen=DEDUPE_RING_SIZE)
        self.triaged = 0
        self.skipped = 0
        self.failed = 0
        self.updated_at: str | None = None

    # --- load / save ---------------------------------------------------------

    @classmethod
    def load(cls, path: str | Path) -> tuple["WatchState", bool]:
        """
        Load the checkpoint.

        Returns (state, existed). A corrupt file is quarantined rather than
        trusted — silently resuming from a garbage cursor could replay the
        entire Chatter history through the LLM.
        """
        state = cls(path)
        p = Path(path)
        if not p.exists():
            return state, False

        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if int(data.get("schema", 0)) != cls.SCHEMA:
                raise ValueError(f"unsupported schema {data.get('schema')!r}")
            state.last_message_id = int(data.get("last_message_id", 0))
            state.processed_ids = deque(
                (int(i) for i in data.get("processed_ids", [])), maxlen=DEDUPE_RING_SIZE
            )
            state.processed_hashes = deque(
                (str(h) for h in data.get("processed_hashes", [])), maxlen=DEDUPE_RING_SIZE
            )
            state.triaged = int(data.get("triaged", 0))
            state.skipped = int(data.get("skipped", 0))
            state.failed = int(data.get("failed", 0))
            state.updated_at = data.get("updated_at")
            return state, True
        except Exception as exc:
            quarantine = p.with_suffix(p.suffix + f".corrupt.{int(time.time())}")
            try:
                p.replace(quarantine)
            except OSError:
                pass
            log.error(
                "Checkpoint %s is unreadable (%s). Moved to %s and restarting from "
                "the current tip — historical chatter will NOT be replayed.",
                p, exc, quarantine,
            )
            return state, False

    def save(self) -> None:
        """Atomically persist the checkpoint (tmp -> fsync -> os.replace)."""
        self.updated_at = datetime_now_iso()
        payload = {
            "schema": self.SCHEMA,
            "last_message_id": self.last_message_id,
            "processed_ids": list(self.processed_ids),
            "processed_hashes": list(self.processed_hashes),
            "triaged": self.triaged,
            "skipped": self.skipped,
            "failed": self.failed,
            "updated_at": self.updated_at,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)  # atomic on POSIX

    # --- mutation ------------------------------------------------------------

    def advance(self, message_id: int) -> None:
        """Move the cursor forward only — never backwards."""
        if message_id > self.last_message_id:
            self.last_message_id = message_id

    def mark(self, message_id: int, fingerprint: str | None = None) -> None:
        """Record a handled message (side effect done, or permanently skipped)."""
        self.advance(message_id)
        self.processed_ids.append(message_id)
        if fingerprint:
            self.processed_hashes.append(fingerprint)

    def already_handled(self, message_id: int) -> bool:
        return message_id in self.processed_ids

    def hash_seen(self, fingerprint: str) -> bool:
        return fingerprint in self.processed_hashes

    def summary(self) -> str:
        return (
            f"cursor={self.last_message_id} triaged={self.triaged} "
            f"skipped={self.skipped} failed={self.failed}"
        )


def datetime_now_iso() -> str:
    """UTC timestamp, ISO 8601."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# Watch mode, part 3 — retries and single-instance locking
# --------------------------------------------------------------------------- #

def retry_call(
    fn: Callable[[], T],
    *,
    attempts: int = RETRY_ATTEMPTS,
    base_delay: float = RETRY_BASE_DELAY,
    label: str = "operation",
) -> T:
    """
    Call fn(), retrying only *transient* transport/LLM failures.

    Permanent failures (xmlrpc Fault = ACL/validation, bad JSON) are re-raised
    immediately: retrying a deterministic rejection only delays the same error.
    Backoff is exponential with full jitter so parallel deployments do not
    synchronise their retries.
    """
    last_exc: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            if not isinstance(exc, RETRYABLE_EXCEPTIONS):
                raise
            last_exc = exc
            if attempt == attempts:
                break
            delay = base_delay * (2 ** (attempt - 1))
            delay += random.uniform(0, base_delay)
            log.warning(
                "%s failed (attempt %d/%d, %s). Retrying in %.1fs.",
                label, attempt, attempts, type(exc).__name__, delay,
            )
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc


class SingleInstanceLock:
    """
    Advisory lock so two watchers cannot double-post.

    POSIX-only (fcntl). On platforms without fcntl this degrades to a logged
    no-op — the durable dedupe guards still prevent duplicate triage, just less
    efficiently (both processes would call the LLM before the hash check).
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._fh = None

    def __enter__(self) -> "SingleInstanceLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            import fcntl  # noqa: PLC0415 — POSIX only, imported defensively
        except ImportError:
            log.warning("fcntl unavailable: single-instance lock disabled.")
            return self
        try:
            # Never truncate: hold the lock on a stable inode.
            self._fh = open(self.path, "a+", encoding="utf-8")
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._fh.seek(0)
            self._fh.truncate()
            self._fh.write(f"pid={os.getpid()}\n")
            self._fh.flush()
        except OSError as exc:
            if self._fh:
                self._fh.close()
                self._fh = None
            raise SystemExit(
                f"Another triage.py --watch is already running (lock {self.path}: {exc}). "
                "Stop it first, or use --state-file with a different path."
            ) from exc
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._fh:
            try:
                import fcntl  # noqa: PLC0415

                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            self._fh.close()
            self._fh = None


# --------------------------------------------------------------------------- #
# Watch mode, part 4 — the watcher
# --------------------------------------------------------------------------- #

class Watcher:
    """
    Poll new Chatter messages on mrp.production and triage each one exactly once.

    Lifecycle:
        bootstrap()  -> decide the starting cursor (§5.3)
        poll_once()  -> fetch, filter, triage, reply, checkpoint
        run()        -> loop until SIGINT/SIGTERM or --max-iterations
    """

    def __init__(
        self,
        odoo: OdooBridge,
        client: OpenAI,
        settings: dict[str, str],
        *,
        interval: float = DEFAULT_WATCH_INTERVAL,
        batch_size: int = DEFAULT_WATCH_BATCH_SIZE,
        state_file: str = DEFAULT_STATE_FILE,
        failures_file: str = DEFAULT_FAILURES_FILE,
        on_error: str = DEFAULT_ON_ERROR,
        message_types: Iterable[str] = DEFAULT_MESSAGE_TYPES,
        skip_self_authored: bool = DEFAULT_SKIP_SELF_AUTHORED,
        dry_run: bool = False,
        since_id: int | None = None,
        backfill: int | None = None,
        max_iterations: int | None = None,
    ) -> None:
        self.odoo = odoo
        self.client = client
        self.settings = settings
        self.model = settings["model"]
        self.subtype = settings["chatter_subtype"]

        self.interval = max(0.5, float(interval))
        self.batch_size = max(1, int(batch_size))
        self.failures_file = Path(failures_file)
        self.on_error = on_error
        self.message_types = tuple(message_types) or DEFAULT_MESSAGE_TYPES
        self.skip_self_authored = skip_self_authored
        self.dry_run = dry_run
        self.since_id = since_id
        self.backfill = backfill
        self.max_iterations = max_iterations

        self.state, self.state_existed = WatchState.load(state_file)
        self._partner_id: int | None = None
        self._warned_self_authored = False
        self._stop = threading.Event()
        self._dry_run_seen: set[int] = set()
        self._polls = 0
        self._last_work = time.monotonic()

        log.info(
            "Watch config: interval=%.1fs batch=%d types=%s on_error=%s "
            "skip_self_authored=%s state=%s",
            self.interval, self.batch_size, ",".join(self.message_types),
            self.on_error, self.skip_self_authored, state_file,
        )
        if not self.skip_self_authored:
            log.warning(
                "--allow-self-authored is ON: notes written by this same Odoo user "
                "WILL be triaged. Our own replies are still ignored via the "
                "'%s' marker.", AI_TRIAGE_TOKEN,
            )

    # --- bootstrap -----------------------------------------------------------

    def bootstrap(self) -> int:
        """
        Decide where the cursor starts (§5.3).

        Default (no state file) is the *current tip*: an empty checkpoint must
        never mean "triage the entire chatter history".
        """
        if self.since_id is not None:
            self.state.advance(self.since_id)
            log.info("Cursor set from --since-id: %s", self.state.last_message_id)
        elif self.backfill:
            tip = retry_call(self.odoo.max_message_id, label="max_message_id")
            start = max(0, tip - int(self.backfill))
            self.state.advance(start)
            log.info("Backfill: tip=%s, starting cursor=%s", tip, self.state.last_message_id)
        elif not self.state_existed:
            tip = retry_call(self.odoo.max_message_id, label="max_message_id")
            self.state.advance(tip)
            log.info(
                "First run: no checkpoint, so the cursor starts at the current tip "
                "(id=%s). Only messages posted from now on will be triaged.", tip,
            )

        # Author partner id, for the self-authored guard.
        try:
            self._partner_id = retry_call(
                self.odoo.author_partner_id, label="author_partner_id"
            )
        except Exception as exc:
            log.warning("Could not resolve our partner id (%s); relying on the "
                        "HTML marker guard alone.", exc)

        self.state.save()
        return self.state.last_message_id

    # --- per-message guards --------------------------------------------------

    def _skip_reason(self, msg: dict[str, Any], note: str,
                     fingerprint: str) -> str | None:
        """
        Why this message must not be triaged, or None if it should be.

        Ordered cheapest -> most expensive, and every guard runs *before* any
        LLM call, so a self-inflicted message can never cost money.
        """
        if self.skip_self_authored and self._partner_id is not None and msg.get("author_id"):
            if msg["author_id"][0] == self._partner_id:
                # Explain the most common surprise exactly once per process.
                if not self._warned_self_authored:
                    self._warned_self_authored = True
                    log.warning(
                        "Skipping note id=%s because it was written by the same Odoo "
                        "user we authenticate as (partner id=%s). If a human posts "
                        "notes as this user, re-run with --allow-self-authored.",
                        msg["id"], self._partner_id,
                    )
                return "self-authored"

        if is_ai_message(msg.get("body") or ""):
            return "ai-marker"

        if self.state.already_handled(msg["id"]):
            return "duplicate-id"

        if not note or len(note) < MIN_NOTE_CHARS:
            return "empty-note"

        if self.state.hash_seen(fingerprint):
            return "duplicate-content"

        return None

    # --- failure handling ----------------------------------------------------

    def _dead_letter(self, msg: dict[str, Any], stage: str, exc: BaseException,
                     note: str = "", attempts: int = 1) -> None:
        """Append one audit line for a message we could not process."""
        record = {
            "ts": datetime_now_iso(),
            "message_id": msg.get("id"),
            "res_id": msg.get("res_id"),
            "stage": stage,
            "attempts": attempts,
            "error": f"{type(exc).__name__}: {exc}"[:500],
            "note_excerpt": (note or "")[:200],
        }
        try:
            self.failures_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.failures_file, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
        except OSError as write_exc:
            log.error("Could not append to %s: %s (record: %s)",
                      self.failures_file, write_exc, record)
        log.error("Dead-lettered message id=%s (stage=%s): %s",
                  msg.get("id"), stage, record["error"])

    # --- one message ---------------------------------------------------------

    def handle(self, msg: dict[str, Any]) -> bool:
        """
        Triage a single message end-to-end and checkpoint it.

        Returns True if a verdict was posted, False if it was skipped. Raises
        only for --on-error halt; otherwise failures are dead-lettered here and
        the loop continues (poison-message isolation).
        """
        message_id = msg["id"]
        note = html_to_text(msg.get("body"))
        fingerprint = note_fingerprint(msg.get("res_id") or 0, note)

        reason = self._skip_reason(msg, note, fingerprint)
        if reason:
            log.info("Skipping mail.message id=%s on mrp.production(%s): %s",
                     message_id, msg.get("res_id"), reason)
            self.state.skipped += 1
            self.state.mark(message_id, fingerprint if reason != "empty-note" else None)
            self.state.save()
            return False

        if self.dry_run:
            # Do not advance the checkpoint in dry-run (nothing was written), so
            # a real run still picks the message up. Dedupe within the session to
            # avoid calling the LLM for the same message on every poll.
            if message_id in self._dry_run_seen:
                return False
            self._dry_run_seen.add(message_id)
            mo = self.odoo.read_mo_or_none(msg["res_id"])
            if not mo:
                log.warning("MO %s no longer exists — skipping dry-run triage.", msg["res_id"])
                return False
            verdict = retry_call(
                lambda: triage_note(self.client, self.model, mo, note),
                label=f"triage(message {message_id})",
            )
            log.info("DRY RUN — would post to mrp.production(%s), in reply to "
                     "message %s:\n%s",
                     msg["res_id"], message_id,
                     render_html(verdict, note, self.model, message_id))
            return False

        try:
            mo = retry_call(
                lambda: self.odoo.read_mo_or_none(msg["res_id"]),
                label=f"read MO {msg['res_id']}",
            )
            if not mo:
                raise LookupError(
                    f"mrp.production id={msg['res_id']} not found or not readable"
                )

            log.info("Triaging message %s on %s: %s",
                     message_id, mo.get("name"), note.replace("\n", " ")[:120])

            verdict = retry_call(
                lambda: triage_note(self.client, self.model, mo, note),
                label=f"triage(message {message_id})",
            )
            print(json.dumps(verdict, indent=2))

            body_html = render_html(verdict, note, self.model, message_id)
            reply_id = retry_call(
                lambda: self.odoo.post_chatter_message(
                    msg["res_id"], body_html, self.subtype, parent_id=message_id
                ),
                label=f"post reply for message {message_id}",
            )

            self.state.triaged += 1
            self.state.mark(message_id, fingerprint)
            # Deliberately do NOT advance the cursor to `reply_id`. The reply is
            # re-fetched by the next poll and rejected by the author/marker
            # guards for free (no LLM call). Jumping the cursor to the reply's
            # id is unsafe: replies are interleaved with operator notes in the
            # global mail.message sequence, so a note created while we were
            # posting could be skipped permanently.
            self.state.save()

            log.info("✓ Triaged message %s -> reply id=%s on %s (%s)",
                     message_id, reply_id, mo.get("name"), self.state.summary())
            return True

        except Exception as exc:
            self.state.failed += 1
            attempts = RETRY_ATTEMPTS if isinstance(exc, RETRYABLE_EXCEPTIONS) else 1
            self._dead_letter(msg, "triage", exc, note, attempts)
            if self.on_error == "halt":
                self.state.save()
                raise
            # Poison-message policy: advance past it so one bad note cannot block
            # the queue forever. The dead-letter file is the audit trail.
            self.state.mark(message_id, fingerprint)
            self.state.save()
            return False

    # NOTE: `_skip_reason` is total for well-formed rows, but a malformed one
    # (e.g. a missing "id") raises KeyError. poll_once() dead-letters and skips
    # those rather than letting them livelock the watcher on every poll.

    # --- polling -------------------------------------------------------------

    def poll_once(self) -> int:
        """Fetch and handle the next batch. Returns how many messages were handled."""
        messages = retry_call(
            lambda: self.odoo.fetch_messages(
                WATCH_MODEL, self.state.last_message_id, self.batch_size, self.message_types
            ),
            label="fetch_messages",
        )
        if not messages:
            return 0

        log.info("Poll: %d new message(s) after id=%s",
                 len(messages), self.state.last_message_id)
        handled = 0
        for index, msg in enumerate(messages):
            if self._stop.is_set():
                log.info("Stop requested — pausing before message %s.", msg.get("id"))
                break
            try:
                self.handle(msg)
            except Exception as exc:
                # handle() only raises for --on-error halt; anything reaching here
                # when on_error == "skip" is a bug in our own code. Dead-letter it
                # and ADVANCE the cursor regardless, otherwise the same message
                # would fail on every poll and livelock the watcher.
                if self.on_error == "halt":
                    raise
                log.exception("Unhandled error on message %s; continuing.", msg.get("id"))
                self._dead_letter(msg, "unexpected", exc)
                # `msg` may be malformed (that could be the very reason handle()
                # raised), so fall back to the highest well-formed id seen so far
                # — otherwise a row without an "id" would livelock the poll.
                advance_to = msg.get("id")
                if not isinstance(advance_to, int):
                    advance_to = max(
                        (m["id"] for m in messages[:index + 1]
                         if isinstance(m.get("id"), int)),
                        default=None,
                    )
                if advance_to is not None:
                    self.state.mark(advance_to)
                    self.state.save()
            handled += 1
        return handled

    def _sleep(self, seconds: float) -> None:
        """Interruptible sleep — SIGINT/SIGTERM wakes us instantly."""
        self._stop.wait(timeout=seconds)

    def _install_signal_handlers(self) -> None:
        def _handler(signum: int, _frame: object) -> None:
            log.info("Signal %s received — finishing up and exiting.", signum)
            self._stop.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, _handler)
            except (ValueError, OSError):  # not on the main thread / unsupported
                pass

    def run(self) -> int:
        """Main poll loop. Returns a process exit code."""
        self._install_signal_handlers()
        self.bootstrap()

        log.info("Watching %s chatter (Ctrl-C to stop). %s", WATCH_MODEL, self.state.summary())
        failures = 0

        while not self._stop.is_set():
            if self.max_iterations and self._polls >= self.max_iterations:
                log.info("Reached --max-iterations=%s; stopping.", self.max_iterations)
                break
            self._polls += 1

            try:
                handled = self.poll_once()
                failures = 0
            except Exception as exc:
                failures += 1
                backoff = min(self.interval * (2 ** (failures - 1)), MAX_BACKOFF_SECONDS)
                log.error("Poll %d failed (%s: %s). Backing off %.1fs.",
                          self._polls, type(exc).__name__, exc, backoff)
                if failures >= 5:
                    log.error("5 consecutive poll failures — check Odoo at %s.",
                              self.odoo.url)
                self._sleep(backoff)
                continue

            if handled:
                self._last_work = time.monotonic()
                self._sleep(max(0.5, self.interval / 5))  # drain bursts quickly
                continue

            if time.monotonic() - self._last_work >= HEARTBEAT_SECONDS:
                log.info("Heartbeat — idle, %s", self.state.summary())
                self._last_work = time.monotonic()

            self._sleep(self.interval)

        if self._stop.is_set():
            try:
                self.state.save()
            except Exception as exc:
                log.error("Final checkpoint save failed: %s", exc)
            log.info("Watcher stopped. %s", self.state.summary())
            return 0
        self.state.save()
        return 0


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #

def build_objects(settings: dict[str, str]) -> tuple[OdooBridge, OpenAI]:
    """Step 1: authenticate with Odoo, initialise the LLM client."""
    odoo = OdooBridge(
        settings["odoo_url"],
        settings["odoo_db"],
        settings["odoo_user"],
        settings["odoo_password"],
    )
    odoo.authenticate()

    # No base_url is supplied: the SDK calls OpenAI's official API directly.
    client = OpenAI(
        api_key=settings["openai_api_key"],
        timeout=60.0,
    )
    return odoo, client


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Odoo MES -> LLM -> Chatter triage bridge",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # --- one-shot mode -------------------------------------------------------
    parser.add_argument("--mo", type=int, default=DEFAULT_MO_ID,
                        help="mrp.production id to triage (one-shot mode)")
    parser.add_argument("--note", default=DEFAULT_RAW_NOTE,
                        help="raw operator defect note (one-shot mode)")

    # --- shared ---------------------------------------------------------------
    parser.add_argument("--dry-run", action="store_true",
                        help="run the LLM but do not write back to Odoo")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")

    # --- watch mode -----------------------------------------------------------
    watch = parser.add_argument_group("watch mode")
    watch.add_argument("--watch", action="store_true",
                       help="poll new mrp.production chatter and triage it continuously")
    watch.add_argument("--interval", type=float, default=None,
                       help="seconds between polls")
    watch.add_argument("--batch-size", type=int, default=None,
                       help="max messages handled per poll")
    watch.add_argument("--since-id", type=int, default=None,
                       help="start the cursor at this mail.message id (implies replay)")
    watch.add_argument("--backfill", type=int, default=None,
                       help="replay the last N chatter messages, then continue live")
    watch.add_argument("--state-file", default=None,
                       help="checkpoint file (enables several independent watchers)")
    watch.add_argument("--failures-file", default=None,
                       help="dead-letter JSONL file")
    watch.add_argument("--max-iterations", type=int, default=None,
                       help="stop after N polls (testing / demos)")
    watch.add_argument("--on-error", choices=("skip", "halt"), default=None,
                       help="'skip' dead-letters an unprocessable note and moves on; "
                            "'halt' stops the watcher so you can debug it")
    watch.add_argument("--message-types", default=None,
                       help="comma-separated mail.message.message_type allow-list")
    watch.add_argument("--allow-self-authored", action="store_true", default=None,
                       help="also triage notes written by the same Odoo user we log in "
                            "as (single-admin setups); our own AI replies are still "
                            "ignored via the HTML marker")
    watch.add_argument("--skip-self-authored", dest="allow_self_authored",
                       action="store_false",
                       help="force the default: ignore notes authored by our own user")
    return parser


def _parse_message_types(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return DEFAULT_MESSAGE_TYPES
    types = tuple(t.strip() for t in raw.split(",") if t.strip())
    return types or DEFAULT_MESSAGE_TYPES


def run_watch(args: argparse.Namespace, settings: dict[str, str],
              odoo: OdooBridge, client: OpenAI) -> int:
    """--watch entry point: build, lock and run the Watcher."""
    # CLI > .env > built-in default.
    interval = args.interval if args.interval is not None else float(settings["watch_interval"])
    batch_size = (args.batch_size if args.batch_size is not None
                  else int(settings["watch_batch_size"]))
    state_file = args.state_file or settings["watch_state_file"]
    failures_file = args.failures_file or settings["watch_failures_file"]
    on_error = args.on_error or settings["watch_on_error"]
    message_types = _parse_message_types(args.message_types or settings["watch_message_types"])
    if args.allow_self_authored is None:
        skip_self_authored = settings["watch_skip_self_authored"].strip().lower() not in (
            "0", "false", "no", "off",
        )
    else:
        skip_self_authored = not args.allow_self_authored

    lock_path = Path(state_file).with_suffix(Path(state_file).suffix + ".lock")

    watcher = Watcher(
        odoo, client, settings,
        interval=interval,
        batch_size=batch_size,
        state_file=state_file,
        failures_file=failures_file,
        on_error=on_error,
        message_types=message_types,
        skip_self_authored=skip_self_authored,
        dry_run=args.dry_run,
        since_id=args.since_id,
        backfill=args.backfill,
        max_iterations=args.max_iterations,
    )

    with SingleInstanceLock(lock_path):
        try:
            return watcher.run()
        except Exception as exc:
            log.error("Watcher crashed: %s", exc, exc_info=True)
            return 1


def run_once(args: argparse.Namespace, settings: dict[str, str],
             odoo: OdooBridge, client: OpenAI) -> int:
    """Default one-shot entry point: triage a single (possibly simulated) note."""
    # --- Step 2: fetch the target MO + the (simulated) raw note ------------- #
    mo = odoo.read_mo(args.mo)
    log.info("Target MO: %s (state=%s)", mo.get("name"), mo.get("state"))
    log.info("Raw operator note: %s", args.note)

    # --- Step 3: structured LLM triage ------------------------------------- #
    verdict = triage_note(client, settings["model"], mo, args.note)
    print("\n--- Structured verdict -------------------------------------------")
    print(json.dumps(verdict, indent=2))
    print("------------------------------------------------------------------\n")

    # --- Step 4: write back to the Odoo Chatter ---------------------------- #
    body_html = render_html(verdict, args.note, settings["model"])
    if args.dry_run:
        log.info("Dry run: skipping Odoo writeback. HTML that would be posted:\n%s", body_html)
        return 0

    message_id = odoo.post_chatter_message(args.mo, body_html, settings["chatter_subtype"])
    print(
        f"✓ Triage note posted to {mo.get('name')} — open "
        f"{settings['odoo_url']}/odoo/manufacturing → the order → Chatter. "
        f"(mail.message id={message_id})"
    )
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.watch and args.note != DEFAULT_RAW_NOTE:
        log.warning("--note is ignored in watch mode; notes are read from Odoo chatter.")

    settings = load_settings()
    odoo, client = build_objects(settings)

    if args.watch:
        return run_watch(args, settings, odoo, client)
    return run_once(args, settings, odoo, client)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
