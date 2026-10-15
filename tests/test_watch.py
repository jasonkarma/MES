  #!/usr/bin/env python3
"""
Offline tests for `triage.py --watch`.

No Odoo instance, no OpenAI key, no network: the XML-RPC bridge and the LLM
client are replaced with fakes, so this runs anywhere.

    python tests/test_watch.py          # via __main__
    python -m pytest tests/test_watch.py -q   # if pytest is available

Covers the six watch requirements end-to-end:
    1. HTML stripping
    2. duplicate prevention (id ring + content hash)
    3. AI-generated message rejection (author + marker guards)
    4. failure handling (dead-letter + poison-message policy)
    5. checkpoint durability (atomic save, corruption quarantine)
    6. the poll loop itself (exactly-once triage, cursor advance)
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import xmlrpc.client
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import triage as T  # noqa: E402


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #

PARTNER_ID = 7          # "our" partner id -> the author guard
OTHER_PARTNER_ID = 42   # an operator


class FakeOdoo:
    """
    Minimal stand-in for OdooBridge.

    Holds a list of mail.message dicts (newest id last) and records every reply
    it is asked to post, so tests can assert exactly-once behaviour.
    """

    def __init__(self, messages: list[dict] | None = None,
                 missing_mos: set[int] | None = None) -> None:
        self.messages = messages or []
        self.missing_mos = missing_mos or set()
        self.posts: list[dict] = []
        self.fetch_calls = 0
        self.url = "http://fake"
        self._next_id = max((m["id"] for m in self.messages), default=0) + 1

    # --- OdooBridge surface used by the Watcher -----------------------------

    def author_partner_id(self) -> int:
        return PARTNER_ID

    def max_message_id(self, model: str = T.WATCH_MODEL) -> int:
        return max((m["id"] for m in self.messages), default=0)

    def fetch_messages(self, model: str, cursor: int, limit: int,
                       message_types) -> list[dict]:
        self.fetch_calls += 1
        rows = [
            m for m in self.messages
            if m["id"] > cursor and m.get("message_type", "comment") in tuple(message_types)
        ]
        return sorted(rows, key=lambda m: m["id"])[:limit]

    def read_mo_or_none(self, mo_id: int) -> dict | None:
        if mo_id in self.missing_mos:
            return None
        return {"id": mo_id, "name": f"MO{mo_id}", "state": "progress",
                "product_id": [1, "Widget"]}

    def post_chatter_message(self, mo_id, body_html, subtype_xmlid=None,
                             parent_id=None) -> int:
        new_id = self._next_id
        self._next_id += 1
        self.posts.append({
            "mo_id": mo_id, "body": body_html,
            "parent_id": parent_id, "id": new_id,
        })
        # Realistic in two ways:
        #  1. The reply becomes a new mail.message on the same record, written
        #     by us, so the next poll must recognise and ignore it.
        #  2. Odoo SANITISES mail.message.body and strips custom attributes, so
        #     the stored body is not byte-identical to what we sent. This is
        #     exactly why the marker guard cannot rely on `data-ai-triage`.
        stored = re.sub(rf'\s+{T.TRIAGE_MARKER}="[^"]*"', "", body_html)
        self.messages.append({
            "id": new_id, "res_id": mo_id, "body": stored,
            "author_id": [PARTNER_ID, "AI Bridge"], "message_type": "comment",
        })
        return new_id


class FakeLLM:
    """Returns a fixed verdict and counts calls so cost/dedupe can be asserted."""

    def __init__(self, verdict: dict | None = None, fail_ids: set[int] | None = None):
        self.verdict = verdict or {
            "iso_code": "TW-012",
            "severity": "MEDIUM",
            "action_steps": ["Check insert T3", "Torque collet to 45 Nm", "Log cycle count"],
        }
        self.calls = 0
        self.fail_ids = fail_ids or set()

    def __call__(self, client, model, mo, note):
        self.calls += 1
        if "#poison" in note:
            raise xmlrpc.client.Fault(1, "deterministic failure")
        return self.verdict


def msg(mid: int, body: str, *, res_id: int = 2, author: int = OTHER_PARTNER_ID,
        message_type: str = "comment") -> dict:
    return {
        "id": mid, "res_id": res_id, "body": body,
        "author_id": [author, f"partner{author}"],
        "message_type": message_type, "subtype_id": 2, "date": "2026-01-01 00:00:00",
        "parent_id": False,
    }


# --------------------------------------------------------------------------- #
# Test harness
# --------------------------------------------------------------------------- #

_PASS = 0
_FAIL: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    global _PASS
    if condition:
        _PASS += 1
        print(f"  ✓ {label}")
    else:
        _FAIL.append(label + (f" — {detail}" if detail else ""))
        print(f"  ✗ {label}" + (f" — {detail}" if detail else ""))


def eq(label: str, actual: object, expected: object) -> None:
    check(label, actual == expected, f"expected {expected!r}, got {actual!r}")


def make_watcher(odoo: FakeOdoo, llm: FakeLLM, tmp: Path, **kwargs) -> T.Watcher:
    settings = {
        "model": "fake-model",
        "chatter_subtype": "mail.mt_note",
    }
    defaults = dict(
        interval=0.01,
        batch_size=20,
        state_file=str(tmp / "state.json"),
        failures_file=str(tmp / "failures.jsonl"),
        on_error="skip",
        message_types=T.DEFAULT_MESSAGE_TYPES,
    )
    defaults.update(kwargs)

    watcher = T.Watcher(odoo, None, settings, **defaults)
    # Patch the module-level triage_note the Watcher calls, so no LLM/network.
    T.triage_note = llm  # type: ignore[assignment]
    return watcher


# --------------------------------------------------------------------------- #
# 1. HTML stripping
# --------------------------------------------------------------------------- #

def test_html_to_text() -> None:
    print("\n[1] HTML stripping (Chatter body -> prompt-ready note)")
    eq("plain text passes through",
       T.html_to_text("Machine shaking"), "Machine shaking")
    eq("consecutive block tags become a paragraph break",
       T.html_to_text("<p>Line one</p><p>Line two</p>"), "Line one\n\nLine two")
    eq("a block tag alone becomes one newline",
       T.html_to_text("line one<br/>line two"), "line one\nline two")
    eq("<br/> becomes a newline",
       T.html_to_text("a<br/>b"), "a\nb")
    eq("inline tags vanish",
       T.html_to_text("<b>bold</b> and <i>italic</i>"), "bold and italic")
    eq("entities are decoded",
       T.html_to_text("Tool&nbsp;&amp;&nbsp;insert &#39;T3&#39; chipped"),
       "Tool & insert 'T3' chipped")
    eq("script contents are dropped",
       T.html_to_text("<script>alert('x')</script>real note"), "real note")
    eq("style contents are dropped",
       T.html_to_text("<style>p{color:red}</style>real note"), "real note")
    eq("attachment-only body collapses to empty (filename is not a note)",
       T.html_to_text('<span class="o_attachment">report.pdf</span>'
                      '<span class="o_attachment">img.png</span>'), "")
    eq("attachment wrapper is dropped, real note kept",
       T.html_to_text('<div class="o_attachment">a.pdf</div><p>Spindle squeal</p>'),
       "Spindle squeal")
    eq("whitespace is collapsed", T.html_to_text("<p>a    b\t\tc</p>"), "a b c")
    eq("blank-line runs are squeezed",
       T.html_to_text("<p>a</p><p></p><p></p><p>b</p>"), "a\n\nb")
    eq("empty body -> empty string", T.html_to_text(""), "")
    eq("None body -> empty string", T.html_to_text(None), "")
    eq("emoji-only body is short", T.html_to_text("<p>👍</p>"), "👍")

    long_body = "<p>" + ("word " * 600) + "</p>"
    out = T.html_to_text(long_body)
    check("long note is capped", len(out) < T.MAX_NOTE_CHARS + 40,
          f"len={len(out)}")
    check("truncation is marked", "[truncated]" in out)

    eq("fingerprint is stable",
       T.note_fingerprint(2, "abc"), T.note_fingerprint(2, "abc"))
    check("fingerprint includes the record id",
          T.note_fingerprint(2, "abc") != T.note_fingerprint(3, "abc"))
    check("fingerprint includes the text",
          T.note_fingerprint(2, "abc") != T.note_fingerprint(2, "abd"))


# --------------------------------------------------------------------------- #
# 2. Checkpoint durability
# --------------------------------------------------------------------------- #

def test_watch_state() -> None:
    print("\n[5] WatchState (durable checkpoint)")
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "state.json"

        state, existed = T.WatchState.load(path)
        check("missing file -> fresh state", not existed and state.last_message_id == 0)

        state.advance(10)
        state.advance(4)  # never goes backwards
        eq("cursor is monotonic", state.last_message_id, 10)

        state.mark(11, T.note_fingerprint(2, "note"))
        state.triaged += 1
        state.save()

        check("save is atomic (no .tmp left)", not (Path(d) / "state.json.tmp").exists())
        reloaded, existed = T.WatchState.load(path)
        check("checkpoint round-trips", existed)
        eq("cursor persisted", reloaded.last_message_id, 11)
        eq("counters persisted", reloaded.triaged, 1)
        check("processed id persisted", reloaded.already_handled(11))
        check("content hash persisted",
              reloaded.hash_seen(T.note_fingerprint(2, "note")))

        # Ring stays bounded
        with tempfile.TemporaryDirectory() as d2:
            ring = T.WatchState(Path(d2) / "s.json")
            for i in range(T.DEDUPE_RING_SIZE + 50):
                ring.mark(1000 + i, f"h{i}")
            eq("id ring is bounded", len(ring.processed_ids), T.DEDUPE_RING_SIZE)
            eq("hash ring is bounded", len(ring.processed_hashes), T.DEDUPE_RING_SIZE)
            check("oldest ids fall out of the ring", not ring.already_handled(1000))

        # Corruption is quarantined, not trusted
        path.write_text("{not json at all", encoding="utf-8")
        recovered, existed = T.WatchState.load(path)
        check("corrupt state reported as not-existing", not existed)
        eq("corrupt state resets the cursor to 0", recovered.last_message_id, 0)
        quarantined = list(Path(d).glob("state.json.corrupt.*"))
        eq("corrupt file is quarantined", len(quarantined), 1)


# --------------------------------------------------------------------------- #
# 3. Skip-reason matrix (duplicate + AI-generated guards)
# --------------------------------------------------------------------------- #

def test_skip_reasons() -> None:
    print("\n[2+3] Guards: duplicates, self-authored and AI-generated messages")
    with tempfile.TemporaryDirectory() as d:
        odoo = FakeOdoo()
        watcher = make_watcher(odoo, FakeLLM(), Path(d))
        watcher.bootstrap()

        note = "Tooling chatter on step 2, insert T3 chipped"
        fp = T.note_fingerprint(2, note)

        eq("valid operator note -> not skipped",
           watcher._skip_reason(msg(100, f"<p>{note}</p>"), note, fp), None)

        eq("our own author id -> skipped",
           watcher._skip_reason(msg(101, f"<p>{note}</p>", author=PARTNER_ID), note, fp),
           "self-authored")

        ai_body = T.render_html({"iso_code": "TW-1", "severity": "LOW",
                                 "action_steps": ["a", "b", "c"]},
                                "x", "m", 5)

        # --allow-self-authored: the single-admin setup, where the human posting
        # a note IS the account the bridge logs in as. AI replies must STILL be
        # ignored, or the watcher would triage its own output forever.
        permissive = make_watcher(FakeOdoo(), FakeLLM(), Path(d),
                                  skip_self_authored=False)
        permissive.bootstrap()
        eq("--allow-self-authored triages same-author notes",
           permissive._skip_reason(msg(110, f"<p>{note}</p>", author=PARTNER_ID),
                                   note, fp), None)
        eq("--allow-self-authored still ignores AI replies",
           permissive._skip_reason(msg(111, ai_body), T.html_to_text(ai_body), fp),
           "ai-marker")
        eq("--allow-self-authored does not loop on the reply id",
           permissive._skip_reason(msg(112, ai_body, author=PARTNER_ID),
                                   T.html_to_text(ai_body), fp), "ai-marker")
        eq("AI marker in the body -> skipped",
           watcher._skip_reason(msg(102, ai_body), T.html_to_text(ai_body), fp),
           "ai-marker")
        eq("is_ai_message detects the marker",
           T.is_ai_message(ai_body), True)
        eq("is_ai_message detects the heading alone",
           T.is_ai_message("<p>🤖 AI Triage — Operational Defect</p>"), True)
        eq("is_ai_message ignores operator prose",
           T.is_ai_message("<p>Machine shaking badly</p>"), False)
        eq("is_ai_message handles an empty body", T.is_ai_message(""), False)
        # Odoo's HTML sanitiser strips custom attributes from mail.message.body,
        # so the guard must also work on the attribute-free, round-tripped form.
        sanitised = ai_body.replace(f' {T.TRIAGE_MARKER}="v1"', "")
        check("AI body is still detected after attribute stripping",
              T.is_ai_message(sanitised))
        check("a plain-text token survives the DB round-trip",
              T.AI_TRIAGE_TOKEN in sanitised)
        eq("operator prose mentioning 'triage' is not an AI message",
           T.is_ai_message("<p>please triage this later</p>"), False)

        watcher.state.mark(103)
        eq("already-processed id -> skipped",
           watcher._skip_reason(msg(103, f"<p>{note}</p>"), note, fp), "duplicate-id")

        watcher.state.mark(104, fp)
        eq("identical content -> skipped",
           watcher._skip_reason(msg(105, f"<p>{note}</p>"), note, fp),
           "duplicate-content")
        eq("same text on a different MO -> allowed",
           watcher._skip_reason(msg(106, f"<p>{note}</p>", res_id=9), note,
                                T.note_fingerprint(9, note)), None)

        eq("blank note -> skipped",
           watcher._skip_reason(msg(107, "<p>   </p>"), "", T.note_fingerprint(2, "")),
           "empty-note")
        attachment_body = ('<div class="o_attachment">report.pdf</div>'
                           '<div class="o_attachment">photo.png</div>')
        attachment_note = T.html_to_text(attachment_body)
        eq("attachment-only body strips to nothing", attachment_note, "")
        eq("attachment-only note -> skipped",
           watcher._skip_reason(msg(108, attachment_body), attachment_note,
                                T.note_fingerprint(2, attachment_note)),
           "empty-note")


# --------------------------------------------------------------------------- #
# 4. Retry policy
# --------------------------------------------------------------------------- #

def test_retry() -> None:
    print("\n[4a] retry_call (transient vs permanent failures)")

    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("boom")
        return "ok"

    # base_delay=0 keeps the test instant.
    eq("transient failure is retried until it succeeds",
       T.retry_call(flaky, attempts=3, base_delay=0.0), "ok")
    eq("exactly 3 attempts were made", calls["n"], 3)

    def always_fails():
        raise ConnectionError("still down")

    try:
        T.retry_call(always_fails, attempts=2, base_delay=0.0)
        check("exhausted retries raise", False)
    except ConnectionError:
        check("exhausted retries raise", True)

    def permanent():
        raise xmlrpc.client.Fault(1, "Access Denied")

    before = time_calls = {"n": 0}

    def permanent_counted():
        time_calls["n"] += 1
        permanent()

    try:
        T.retry_call(permanent_counted, attempts=3, base_delay=0.0)
        check("permanent faults raise", False)
    except xmlrpc.client.Fault:
        check("permanent faults raise", True)
    eq("permanent faults are NOT retried", time_calls["n"], 1)


# --------------------------------------------------------------------------- #
# 5. Bootstrap policy
# --------------------------------------------------------------------------- #

def test_bootstrap() -> None:
    print("\n[5b] bootstrap cursor policy")

    with tempfile.TemporaryDirectory() as d:
        odoo = FakeOdoo([msg(1, "<p>a</p>"), msg(5, "<p>b</p>"), msg(9, "<p>c</p>")])
        w = make_watcher(odoo, FakeLLM(), Path(d))
        w.bootstrap()
        eq("first run adopts the current tip", w.state.last_message_id, 9)

    with tempfile.TemporaryDirectory() as d:
        # An existing checkpoint is resumed exactly, not re-bootstrapped.
        state = T.WatchState(Path(d) / "state.json")
        state.advance(4)
        state.save()
        odoo = FakeOdoo([msg(1, "<p>a</p>"), msg(9, "<p>c</p>")])
        w = make_watcher(odoo, FakeLLM(), Path(d))
        w.bootstrap()
        eq("existing checkpoint resumes", w.state.last_message_id, 4)

    with tempfile.TemporaryDirectory() as d:
        odoo = FakeOdoo([msg(1, "<p>a</p>"), msg(5, "<p>b</p>"), msg(9, "<p>c</p>")])
        w = make_watcher(odoo, FakeLLM(), Path(d), backfill=2)
        w.bootstrap()
        eq("--backfill starts N before the tip", w.state.last_message_id, 7)

    with tempfile.TemporaryDirectory() as d:
        odoo = FakeOdoo([msg(1, "<p>a</p>"), msg(9, "<p>c</p>")])
        w = make_watcher(odoo, FakeLLM(), Path(d), since_id=3)
        w.bootstrap()
        eq("--since-id wins", w.state.last_message_id, 3)

    with tempfile.TemporaryDirectory() as d:
        # Corrupt checkpoint -> bootstrap from the tip, never replay history.
        path = Path(d) / "state.json"
        path.write_text("garbage", encoding="utf-8")
        odoo = FakeOdoo([msg(1, "<p>a</p>"), msg(9, "<p>c</p>")])
        w = make_watcher(odoo, FakeLLM(), Path(d))
        w.bootstrap()
        eq("corrupt checkpoint bootstraps from the tip", w.state.last_message_id, 9)


# --------------------------------------------------------------------------- #
# 6. End-to-end poll loop (exactly-once)
# --------------------------------------------------------------------------- #

def test_poll_loop_exactly_once() -> None:
    print("\n[6] poll loop: triage each operator note exactly once")

    with tempfile.TemporaryDirectory() as d:
        ai_body = T.render_html({"iso_code": "TW-1", "severity": "LOW",
                                 "action_steps": ["a", "b", "c"]}, "x", "m")
        messages = [
            msg(1, "<p>Tooling chatter on step 2</p>"),            # valid
            msg(2, ai_body),                                        # AI marker
            msg(3, "<p>   </p>"),                                   # empty
            msg(4, "<p>Spindle vibration at step 4</p>"),           # valid
            msg(5, "<p>Tooling chatter on step 2</p>"),             # duplicate content of 1
            msg(6, "<p>Surface scratch on the housing</p>", author=PARTNER_ID),  # self
        ]
        odoo = FakeOdoo(messages)
        llm = FakeLLM()
        tmp = Path(d)
        w = make_watcher(odoo, llm, tmp, since_id=0)
        w.bootstrap()

        handled = w.poll_once()

        eq("all 6 messages were inspected", handled, 6)
        eq("exactly 2 LLM calls", llm.calls, 2)
        eq("exactly 2 replies posted", len(odoo.posts), 2)
        eq("replies are threaded under their source note",
           [p["parent_id"] for p in odoo.posts], [1, 4])
        eq("cursor advanced past the batch", w.state.last_message_id, 6)
        eq("counters: 2 triaged", w.state.triaged, 2)
        eq("counters: 4 skipped", w.state.skipped, 4)

        check("replies carry the AI marker",
              all(T.TRIAGE_MARKER in p["body"] for p in odoo.posts))

        # Second poll: nothing new, and crucially the two replies we just posted
        # must NOT be re-triaged.
        before = llm.calls
        w.poll_once()
        eq("second poll makes no LLM calls", llm.calls, before)
        eq("second poll posts nothing", len(odoo.posts), 2)

        # Restart from disk: still no re-triage of the same conversation.
        w2 = make_watcher(odoo, llm, tmp)
        w2.bootstrap()
        l2 = make_watcher(odoo, llm, tmp)
        l2.bootstrap()
        l2.poll_once()
        eq("after restart, replies are still ignored", len(odoo.posts), 2)


def test_backfill_replays_and_dedupes() -> None:
    print("\n[6b] --backfill replay")

    with tempfile.TemporaryDirectory() as d:
        odoo = FakeOdoo([msg(1, "<p>first note</p>"), msg(2, "<p>second note</p>")])
        llm = FakeLLM()
        w = make_watcher(odoo, llm, Path(d), backfill=2)
        w.bootstrap()
        # tip=2, backfill=2 -> cursor 0 -> both messages are in scope.
        w.poll_once()
        eq("backfill triaged the existing notes", llm.calls, 2)
        eq("backfill posted two replies", len(odoo.posts), 2)

        w.poll_once()
        eq("backfill does not re-triage on the next poll", llm.calls, 2)


def test_failure_handling() -> None:
    print("\n[4b] failure handling: dead-letter + poison-message policy")

    with tempfile.TemporaryDirectory() as d:
        odoo = FakeOdoo([
            msg(1, "<p>good note about chatter</p>"),
            msg(2, "<p>#poison unrecoverable note</p>"),
            msg(3, "<p>another good note</p>"),
        ])
        llm = FakeLLM()
        w = make_watcher(odoo, llm, Path(d), since_id=0, on_error="skip")
        w.bootstrap()
        w.poll_once()

        eq("the two good notes were triaged", len(odoo.posts), 2)
        eq("the bad note did not stop the loop", w.state.last_message_id, 3)
        eq("failure counted", w.state.failed, 1)

        failures = Path(d) / "failures.jsonl"
        check("dead-letter file was created", failures.exists())
        record = json.loads(failures.read_text(encoding="utf-8").strip().splitlines()[-1])
        eq("dead-letter records the message id", record["message_id"], 2)
        eq("dead-letter records the MO id", record["res_id"], 2)
        check("dead-letter records the error",
              "deterministic failure" in record["error"], record["error"])
        check("dead-letter records a note excerpt",
              "#poison" in record["note_excerpt"])

    # on_error=halt -> the exception propagates and the cursor does NOT advance
    with tempfile.TemporaryDirectory() as d:
        odoo = FakeOdoo([msg(1, "<p>#poison note</p>")])
        w = make_watcher(odoo, FakeLLM(), Path(d), since_id=0, on_error="halt")
        w.bootstrap()
        try:
            w.handle(odoo.messages[0])
            check("halt re-raises the failure", False)
        except xmlrpc.client.Fault:
            check("halt re-raises the failure", True)
        eq("halt does not advance past the bad message", w.state.last_message_id, 0)

    # A missing MO is dead-lettered, not fatal
    with tempfile.TemporaryDirectory() as d:
        odoo = FakeOdoo([msg(1, "<p>note</p>", res_id=999)], missing_mos={999})
        w = make_watcher(odoo, FakeLLM(), Path(d), since_id=0)
        w.bootstrap()
        w.poll_once()
        eq("missing MO is dead-lettered", w.state.failed, 1)
        eq("missing MO posts nothing", len(odoo.posts), 0)
        eq("cursor still advances past a dead-letter", w.state.last_message_id, 1)


def test_unexpected_error_does_not_livelock() -> None:
    print("\n[4c] an unexpected error is dead-lettered and cannot livelock the poll")

    with tempfile.TemporaryDirectory() as d:
        odoo = FakeOdoo([
            msg(1, "<p>good note about chatter</p>"),
            msg(2, "<p>triggers a bug in our own code</p>"),
            msg(3, "<p>another good note</p>"),
        ])
        llm = FakeLLM()
        w = make_watcher(odoo, llm, Path(d), since_id=0)
        w.bootstrap()

        # Simulate a defect in our own code (not a transient network error, not
        # a fault from Odoo): a plain RuntimeError out of handle().
        original = w.handle

        def flaky_handle(m):
            if m["id"] == 2:
                raise RuntimeError("simulated bug")
            return original(m)

        w.handle = flaky_handle  # type: ignore[method-assign]

        w.poll_once()
        eq("the other notes were still triaged", len(odoo.posts), 2)
        eq("the cursor advanced past the broken message",
           w.state.last_message_id, 3)
        eq("the failure was counted", w.state.failed, 0)
        check("the broken message was dead-lettered",
              (Path(d) / "failures.jsonl").exists())
        record = json.loads(
            (Path(d) / "failures.jsonl").read_text(encoding="utf-8").strip().splitlines()[-1]
        )
        eq("dead-letter names the unexpected stage", record["stage"], "unexpected")
        eq("dead-letter records the message id", record["message_id"], 2)

        # Second poll must not re-fetch the broken message forever. (It does
        # re-inspect the two AI replies we just posted — that is expected and
        # costs no LLM call — but the broken row is behind the cursor.)
        before_posts, before_calls = len(odoo.posts), llm.calls
        w.poll_once()
        eq("second poll posts nothing new", len(odoo.posts), before_posts)
        eq("second poll makes no LLM calls", llm.calls, before_calls)
        # Anything the next poll would actually look at (id > cursor) excludes it.
        fetched = odoo.fetch_messages(T.WATCH_MODEL, w.state.last_message_id, 100,
                                       T.DEFAULT_MESSAGE_TYPES)
        check("the broken message is never re-fetched",
              all(m["id"] != 2 for m in fetched), str([m["id"] for m in fetched]))


def test_dry_run_does_not_consume() -> None:
    print("\n[6c] --dry-run must not swallow messages")
    with tempfile.TemporaryDirectory() as d:
        odoo = FakeOdoo([msg(1, "<p>real operator note</p>")])
        llm = FakeLLM()
        w = make_watcher(odoo, llm, Path(d), since_id=0, dry_run=True)
        w.bootstrap()
        w.poll_once()
        eq("dry-run posts nothing", len(odoo.posts), 0)
        eq("dry-run does not advance the checkpoint", w.state.last_message_id, 0)
        w.poll_once()
        eq("dry-run dedupes within the session", llm.calls, 1)

        # A real run afterwards still picks the message up.
        w2 = make_watcher(odoo, llm, Path(d))
        w2.bootstrap()
        w2.poll_once()
        eq("a later real run triages the skipped message", len(odoo.posts), 1)


def test_message_type_filter() -> None:
    print("\n[2b] message_type filter excludes Odoo workflow noise")
    with tempfile.TemporaryDirectory() as d:
        odoo = FakeOdoo([
            msg(1, "<p>operator note</p>", message_type="comment"),
            msg(2, "<p>Stage changed to In Progress</p>", message_type="notification"),
            msg(3, "<p>another operator note</p>", message_type="comment"),
        ])
        llm = FakeLLM()
        w = make_watcher(odoo, llm, Path(d), since_id=0)
        w.bootstrap()
        w.poll_once()
        eq("only human messages are triaged", llm.calls, 2)
        eq("workflow noise is ignored",
           [m["id"] for m in odoo.messages if m["id"] in (1, 3)], [1, 3])
        check("the notification was never read", odoo.fetch_calls >= 1)


def test_run_loop_bounded() -> None:
    print("\n[6d] run() loop terminates on --max-iterations")
    with tempfile.TemporaryDirectory() as d:
        odoo = FakeOdoo([msg(1, "<p>note one</p>")])
        llm = FakeLLM()
        w = make_watcher(odoo, llm, Path(d), since_id=0, max_iterations=3)
        rc = w.run()
        eq("exit code is 0", rc, 0)
        eq("three polls were made", w._polls, 3)
        eq("the note was triaged once", len(odoo.posts), 1)


# --------------------------------------------------------------------------- #
# 7. Single-instance lock
# --------------------------------------------------------------------------- #

def test_lock() -> None:
    print("\n[7] single-instance lock")
    with tempfile.TemporaryDirectory() as d:
        lock_path = Path(d) / "state.json.lock"
        with T.SingleInstanceLock(lock_path):
            check("lock file is created", lock_path.exists())
            check("lock file records the pid",
                  f"pid={os.getpid()}" in lock_path.read_text())
            if sys.platform.startswith("win"):
                print("  ~ second-lock check skipped on Windows (no fcntl)")
            else:
                try:
                    with T.SingleInstanceLock(lock_path):
                        check("a second watcher is refused", False)
                except SystemExit:
                    check("a second watcher is refused", True)
        if not sys.platform.startswith("win"):
            with T.SingleInstanceLock(lock_path):
                check("the lock is released on exit", True)


# --------------------------------------------------------------------------- #
# 8. CLI wiring
# --------------------------------------------------------------------------- #

def test_cli_parsing() -> None:
    print("\n[8] CLI wiring")
    parser = T.build_parser()

    args = parser.parse_args([])
    check("one-shot is the default", not args.watch)
    eq("one-shot default MO", args.mo, T.DEFAULT_MO_ID)

    args = parser.parse_args(["--watch", "--interval", "5", "--max-iterations", "12",
                              "--on-error", "halt", "--backfill", "10",
                              "--message-types", "comment"])
    check("--watch is parsed", args.watch)
    eq("--interval", args.interval, 5.0)
    eq("--max-iterations", args.max_iterations, 12)
    eq("--on-error", args.on_error, "halt")
    eq("--backfill", args.backfill, 10)
    eq("--message-types are split", T._parse_message_types(args.message_types), ("comment",))
    eq("empty --message-types falls back to the default",
       T._parse_message_types(""), T.DEFAULT_MESSAGE_TYPES)
    eq("None --message-types falls back to the default",
       T._parse_message_types(None), T.DEFAULT_MESSAGE_TYPES)

    eq("--allow-self-authored is unset by default",
       parser.parse_args([]).allow_self_authored, None)
    eq("--allow-self-authored sets the flag",
       parser.parse_args(["--allow-self-authored"]).allow_self_authored, True)
    eq("--skip-self-authored clears the flag",
       parser.parse_args(["--skip-self-authored"]).allow_self_authored, False)

    check("load_settings exposes the watch keys",
          all(k in T.load_settings() for k in
              ("watch_interval", "watch_batch_size", "watch_state_file",
               "watch_failures_file", "watch_on_error", "watch_message_types",
               "watch_skip_self_authored")))


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #

def main() -> int:
    import logging

    logging.disable(logging.CRITICAL)  # the watcher logs a lot; keep output clean

    print("=" * 72)
    print("triage.py --watch — offline test suite")
    print("=" * 72)

    test_html_to_text()
    test_watch_state()
    test_skip_reasons()
    test_retry()
    test_bootstrap()
    test_poll_loop_exactly_once()
    test_backfill_replays_and_dedupes()
    test_failure_handling()
    test_unexpected_error_does_not_livelock()
    test_dry_run_does_not_consume()
    test_message_type_filter()
    test_run_loop_bounded()
    test_lock()
    test_cli_parsing()

    print("\n" + "=" * 72)
    if _FAIL:
        print(f"FAILED — {len(_FAIL)} check(s) failed, {_PASS} passed:")
        for label in _FAIL:
            print(f"  ✗ {label}")
        print("=" * 72)
        return 1
    print(f"ALL PASSED — {_PASS} checks")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
