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

Usage:
    python triage.py                      # default MO id = 2
    python triage.py --mo 5               # a different Manufacturing Order
    python triage.py --note "..."         # override the raw operator note
    python triage.py --dry-run            # call the LLM, do NOT write to Odoo
"""

from __future__ import annotations

import argparse
import html
import json
import logging
import os
import re
import sys
import xmlrpc.client
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

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
                             subtype_xmlid: str = DEFAULT_CHATTER_SUBTYPE) -> int:
        """
        Write the triage result into the record's Chatter.

        This creates a `mail.message` row linked to the mrp.production record.
        Returns the new mail.message id.
        """
        # author_id is the *partner* id, not the res.users id returned by
        # authenticate(). Resolve it so the note shows a sensible author.
        author = self.execute_kw("res.users", "read", [[self.uid]], {"fields": ["partner_id"]})
        author_id = author[0]["partner_id"][0] if author and author[0]["partner_id"] else None

        values = {
            "model": "mrp.production",   # which model the message hangs off
            "res_id": mo_id,             # which record id
            "message_type": "comment",   # 'comment' = user message; 'notification' = system
            "body": body_html,           # HTML fragment
            "author_id": author_id,      # who the Chatter shows as the author
            "partner_ids": [(6, 0, [])], # (6,0,[]) = replace recipients with none -> no email
        }
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
            message_id = self.execute_kw(
                "mrp.production",
                "message_post",
                [[mo_id]],
                {
                    "body": body_html,
                    "message_type": "comment",
                    "subtype_xmlid": subtype_xmlid,
                },
            )
        log.info("Chatter updated: mail.message id=%s on mrp.production(%s)", message_id, mo_id)
        return message_id


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

def render_html(verdict: dict[str, Any], note: str, model: str) -> str:
    """Build the HTML fragment stored in mail.message.body."""
    e = html.escape  # never trust model output inside HTML
    sev = verdict["severity"]
    colour = SEVERITY_COLOR.get(sev, "#555555")

    steps_html = "".join(f"<li>{e(step)}</li>" for step in verdict["action_steps"])

    return (
        f"<p><b>🤖 AI Triage — Operational Defect</b></p>"
        f"<p><b>Reported note:</b> <i>{e(note)}</i></p>"
        f"<p><b>Failure code (ISO):</b> <code>{e(verdict['iso_code'])}</code><br/>"
        f"<b>Severity:</b> <span style=\"color:{colour};font-weight:bold;\">{e(sev)}</span></p>"
        f"<p><b>Recommended operator checks:</b></p>"
        f"<ul>{steps_html}</ul>"
        f"<p><small>Generated automatically by triage.py using model "
        f"<code>{e(model)}</code>. Human verification required before "
        f"restarting the work center.</small></p>"
    )


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Odoo MES -> LLM -> Chatter triage bridge")
    parser.add_argument("--mo", type=int, default=DEFAULT_MO_ID,
                        help=f"mrp.production id to triage (default: {DEFAULT_MO_ID})")
    parser.add_argument("--note", default=DEFAULT_RAW_NOTE,
                        help="raw operator defect note (default: simulated note)")
    parser.add_argument("--dry-run", action="store_true",
                        help="run the LLM call but do not write back to Odoo")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    settings = load_settings()
    odoo, client = build_objects(settings)

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


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
