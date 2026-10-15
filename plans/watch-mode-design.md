# Design Note — `triage.py --watch` (real polling watcher)

> Status: proposed · Mode: Architect → Code · Zero new dependencies (stdlib only)

## 1. Objective & scope

Today `triage.py` is **one-shot**: `--mo 2 --note "..."` → LLM → post one Chatter note.
`--watch` turns it into a **long-running process** that discovers operator defects by
itself, with no Odoo module, no Odoo source change, and no human in the loop:

```
operator types a note in the MO Chatter  ──►  watcher picks it up  ──►  AI triage reply
                                             (poll mail.message)        (same thread)
```

The six hard parts named in the request map to six concrete mechanisms:

| Requirement | Mechanism | § |
|---|---|---|
| Poll new `mail.message` | `search_read` with a monotonic `id > cursor` domain | 4, 5 |
| Strip their HTML | stdlib `HTMLParser` subclass → plain text | 4.3 |
| Prevent duplicate processing | persisted processed-id window + content hash + **server-side marker check** | 5, 4.2 |
| Ignore AI-generated messages | author-id check + HTML marker check (two independent guards) | 4.2 |
| Handle failures | retry-with-backoff on transient errors, dead-letter JSONL, poison-message policy | 6 |
| Preserve a checkpoint | atomically-replaced JSON state file, flushed after every handled message | 5 |

## 2. Data flow

```
                          ┌────────────────────────────────────────┐
                          │  poll_once()                           │
                          └────────────────────────────────────────┘
 search_read('mail.message',
   domain = [ model = 'mrp.production',
              id    > state.last_message_id,        ← checkpoint (monotonic, global ids)
              message_type in ('comment','email') ] ← excludes Odoo tracking notifications
   order = 'id asc', limit = batch_size)

        │  for each message  (cheap → expensive)
        ▼
 ┌──────────────────────┐   skip reason (logged, counted, cursor still advances)
 │ 1 author == our AI   │──► "self-authored"
 │ 2 body has marker    │──► "ai-marker"
 │ 3 id in state.seen   │──► "duplicate"
 │ 4 text hash in seen  │──► "duplicate-content"
 │ 5 strip → too short  │──► "empty-note"        (attachments, "+1", pure HTML)
 │ 6 hash in seen_recent│──► "duplicate-content"
 │ 7 MO unreadable      │──► "missing-record"    (logged as failure, not skip)
 └──────────┬───────────┘
            ▼
   triage_text(mo, note)   ← same code path as one-shot mode
            ▼
   post_chatter_message(mo_id, body_html, parent_id = source message id)
            ▼
   state.mark(message_id)  → atomic write      ← checkpoint advanced AFTER side effect
```

Ordering matters: the **author/marker guards run before any LLM call**, so a
self-inflicted message can never cost money or create a loop.

## 3. Why the loop is safe (three independent guards)

The AI reply is itself a `mail.message` on the same `mrp.production` record, so it
*will* be re-discovered by the next poll. Three independent guards stop that:

1. **Author guard** — the reply's `author_id` is our own partner id (resolved once at
   startup from `res.users(uid).partner_id`). Any message whose author is us is skipped.
2. **Marker guard** — the reply HTML is wrapped in `<div data-ai-triage="v1">…</div>`,
   carries the visible `🤖 AI Triage — Operational Defect` heading, and ends with the
   plain-text token `[ai-triage:v1]`. Any of them ⇒ skip. This also survives a changed
   AI user / re-keyed credentials.

   ⚠️ **Verified against Odoo 17 during implementation:** Odoo sanitises
   `mail.message.body` and **strips custom HTML attributes**, so `data-ai-triage` does
   *not* survive the DB round-trip. The guard therefore cannot rely on a `data-*`
   attribute alone — the plain-text token and heading are the load-bearing signals, and
   the class/attribute check is a bonus for instances that do not sanitise.
3. **Cursor guard** — the reply id is written to the checkpoint as processed, so it is
   below the cursor on the next poll anyway.

Guard 1 and 2 are deliberately redundant: guard 3 alone would be defeated by a lost or
deleted state file, and guard 2 alone would be defeated by a human copy-pasting an AI
verdict back into the Chatter (which is a real workflow here — we *want* that to be
skipped too, since re-triaging an AI verdict is noise).

## 4. Discovery & filtering (detail)

### 4.1 Domain

```python
[[("model", "=", "mrp.production"),
  ("id", ">", cursor),
  ("message_type", "in", ["comment", "email"])]]
```
`order="id asc"`, `limit=batch`.

- `model='mrp.production'` scopes us to the MES orders (not every Chatter in the DB).
- Sanity-checked against Odoo 17: direct `mail.message.create` with
  `message_type='comment'` works and the row is discovered by this domain.
- `message_type` is the clean discriminator: **operator-typed notes are `comment`**,
  while Odoo's own tracking/state-change messages are `notification` /
  `user_notification`. This filters workflow noise *without* guessing subtype XML IDs
  (subtype-based filtering is a trap here: our own reply uses `mail.mt_note`, exactly
  the subtype a human internal note uses, so subtype alone cannot separate them).
- `id` is globally monotonic across `mail.message`, so a single integer is a valid,
  crash-safe cursor. Gaps from unrelated models are harmless.

### 4.2 Signal fields

`fields = ["id", "res_id", "body", "author_id", "message_type", "subtype_id", "date", "parent_id"]`
— everything the guards need, one round-trip, no per-message `read`.

### 4.3 HTML → text

`body` is an HTML fragment (`<p>`, `<br/>`, entities, `<span class="o_attachment">`,
occasionally a quoted email reply). A stdlib `html.parser.HTMLParser` subclass:

- `<script>`/`<style>` contents dropped (element-stack based, so nesting is respected);
- Odoo's attachment/signature wrappers (`class` containing `o_attachment`,
  `o-mail-attachment`, `o_signature`, …) are dropped **including their text**, so an
  attachment-only message strips to `""` and is skipped instead of being triaged as the
  filename `report.pdf` — this was caught in live testing, not in the design;
- block tags (`p`, `div`, `br`, `li`, `tr`, `h1..h6`) emit `\n`;
- `html.unescape` for `&nbsp;`/`&amp;`/`&#39;` (operators paste curly quotes);
- whitespace collapsed, blank-line runs squeezed, `.strip()`;
- hard cap at `MAX_NOTE_CHARS = 1500` (tail truncated) to bound prompt cost;
- if the result is `< MIN_NOTE_CHARS (=4)` → the message is "empty" (attachment-only,
  emoji-only, or pure markup) and is skipped **without** an LLM call.

No `beautifulsoup4`/`lxml` dependency — the repo's `requirements.txt` stays untouched.

## 5. Checkpoint design

### 5.1 State file — `logs/watch_state.json` (override: `--state-file`)

```json
{
  "schema": 1,
  "last_message_id": 4821,
  "processed_ids": [4802, 4819, 4821],
  "processed_hashes": ["9d3f…"],
  "triaged": 17, "skipped": 42, "failed": 1,
  "updated_at": "2026-10-15T20:04:11+00:00"
}
```

- `processed_ids` is a **bounded ring** (newest 200) — idempotency for the messages we
  actually acted on; combats non-monotonic re-delivery and manual cursor resets.
- `processed_hashes` is a bounded ring (newest 200) of `sha1(f"{res_id}:{text}")` —
  catches the real-world case of an operator posting the identical note twice, and the
  case of a *second watcher instance* whose cursor started lower.
- Counters are cosmetic/observability only; nothing depends on them.

### 5.2 Durability

Write = `tmp file → flush → fsync → os.replace()` (atomic on POSIX).
The checkpoint is flushed **after each message is fully handled** (side effect done, or
permanently skipped), never batched — a `kill -9` loses at most the in-flight message.

### 5.3 Bootstrap policy (critical: never triage history)

| Situation | Behaviour |
|---|---|
| State file exists and parses | resume exactly at `last_message_id` |
| `--since-id N` given | cursor = `N` (explicit replay/backfill) |
| `--backfill N` given | cursor = `tip - N` (triage the last N messages then continue) |
| No state file (first run) | cursor = **current max id** → only *new* chatter is triaged |
| State file **corrupt** | rename to `watch_state.json.corrupt.<ts>`, log loudly, bootstrap from tip |

Rationale: an empty state file must not mean "process the entire history" — that would
be a surprise five-figure LLM bill.

### 5.4 Single-instance guard

`logs/watch_state.lock` held with `fcntl.flock(LOCK_EX | LOCK_NB)` for the process
lifetime; a second `--watch` exits with a clear message. Without it, two watchers
double-post (the hash guard mostly catches it — this makes it explicit). On platforms
without `fcntl` (Windows), the lock degrades to a no-op warning; the durable guards still hold.

## 6. Failure model

### 6.1 Failure classes

| Class | Examples | Policy |
|---|---|---|
| Transient transport | `xmlrpc.client.ProtocolError`, `ConnectionError`, `TimeoutError`, `OSError` | retry ≤ 3, exponential backoff + jitter (1.5s, 3s, 6s) |
| Transient LLM | `openai.RateLimitError`, `APIConnectionError`, `APITimeoutError`, `InternalServerError` | same retry policy |
| Permanent / deterministic | `xmlrpc.client.Fault` (ACL, bad field), JSON parse failure after repair, 4xx from LLM | no retry → dead-letter + policy below |
| Defect in our code | any unexpected `Exception` | caught at the per-message boundary; loop survives; dead-letter |

The per-message handler is wrapped so that **no single bad message can kill the loop**.

### 6.2 Poison-message policy

Default `--on-error skip`: record the message in the dead-letter log, **advance the
cursor past it**, continue. Rationale: the queue is chatter, not an ordered ledger; one
un-processable note must not block head-of-line forever (classic queue-poisoning).
`--on-error halt`: do not advance, exit non-zero — for debugging.

### 6.3 Dead-letter log — `logs/triage_watch_failures.jsonl`

One JSON object per line:
`{ts, message_id, res_id, mo_name, stage, attempts, error, note_excerpt}`.
Append-only, never rotated by us; it is the audit trail for "why did this defect get no
verdict?".

### 6.4 Poll-level backoff & liveness

- consecutive poll failures `n` → sleep `min(interval * 2**n, 300s)`.
- when a poll returns work, the next sleep is short (`interval/5`, min 0.5s) so a burst
  drains quickly instead of waiting a full interval.
- heartbeat INFO log every `12 * interval` (≈2 min) so an idle watcher is visibly alive.
- `SIGINT`/`SIGTERM` → finish the in-flight message, flush state, exit 0. Sleep is
  implemented with `threading.Event.wait()` so signals interrupt it immediately.

### 6.5 Dry run

`--dry-run` in watch mode logs the HTML that *would* be posted and **does not advance the
checkpoint past any message it inspects** (so nothing is silently swallowed). To avoid
re-calling the LLM for the same message every poll, an in-memory `_dry_run_seen` set
dedupes within the session.

One precision, since it is easy to misread: the *bootstrap* step still persists a cursor
(the current tip on a first run). That is what makes a `--dry-run` useful — the subsequent
real run resumes from there and picks up the messages the dry run only inspected, without
replaying history. Verified live.

## 7. CLI & configuration surface

```
--watch                 run the polling watcher instead of one-shot mode
--interval SECONDS      seconds between polls                      (default 10)
--batch-size N          max messages per poll                      (default 20)
--since-id N            start the cursor at this mail.message id
--backfill N            start the cursor N messages before the tip
--state-file PATH       checkpoint path                  (default logs/watch_state.json)
--failures-file PATH    dead-letter path     (default logs/triage_watch_failures.jsonl)
--max-iterations N      stop after N polls (testing/screenshots)
--on-error {skip,halt}  poison-message policy                      (default skip)
--message-types A,B     message_type allow-list      (default comment,email)
```
`--mo`/`--note` keep their one-shot meaning; passing `--note` with `--watch` warns and
is ignored (watch mode derives notes from Odoo). `--dry-run`, `-v` apply to both.

New `.env` keys (all optional, CLI wins): `WATCH_INTERVAL`, `WATCH_BATCH_SIZE`,
`WATCH_STATE_FILE`, `WATCH_FAILURES_FILE`, `WATCH_ON_ERROR`, `WATCH_MESSAGE_TYPES`.

## 8. Code layout (all inside `triage.py`, keeping the "one file to run" property)

| Section | Symbol (as implemented) | Notes |
|---|---|---|
| 1 | `html_to_text(html) -> str`, `_HTMLTextExtractor(HTMLParser)`, `note_fingerprint(res_id, note)` | §4.3 |
| 2 | `WatchState` | `load/save/advance/mark/already_handled/hash_seen/summary`; atomic replace, corrupt-file quarantine |
| 3 | `retry_call(fn, attempts, base_delay, label)`, `RETRYABLE_EXCEPTIONS` | §6.1, jittered backoff |
| 4 | `SingleInstanceLock` | `fcntl.flock` context manager |
| 5 | `Watcher` | `bootstrap()`, `poll_once()`, `_skip_reason(msg, note, fp)`, `handle(msg)`, `_dead_letter(...)`, `run()`, `_sleep()`, `_install_signal_handlers()` |
| 6 | `TRIAGE_MARKER`, `AI_TRIAGE_TOKEN`, `AI_TRIAGE_HEADING`, `is_ai_message(body)` | marker constants + predicate |
| 7 | `build_parser()`, `_parse_message_types()`, `run_watch()`, `run_once()`, `main()` | `main()` dispatches on `--watch` |
| 8 | `OdooBridge.author_partner_id / read_mo_or_none / max_message_id / fetch_messages` | watch-specific bridge helpers |

Small refactors to the existing one-shot path (no behaviour change):

- `render_html(...)` gains `res_id`/`source_id` and wraps output in the marker div;
- `post_chatter_message(..., parent_id=None)` threads the reply under the source note;
- `build_objects()` and `load_settings()` extended with the watch keys;
- `triage_note()` unchanged (shared by both modes).

## 9. Edge cases

| Case | Handling |
|---|---|
| Operator posts 5 notes in a row | one poll, ordered `id asc`, each replied to in-thread |
| Operator edits a note | `mail.message` rows are immutable; an edit is a *new* body via a wizard → treated as a new message (documented; not deduped against the original) |
| MO deleted / archived between note and triage | MO read fails → dead-letter, skip, cursor advances |
| Note contains a reply-to email with quoted history | HTML strip + 1500-char cap |
| LLM returns prose/markdown | existing `_extract_json` + `_normalise` still guarantee a valid verdict |
| Odoo restarts mid-watch | transient `ProtocolError` → retry; auth is re-established lazily on failure |
| State file deleted by hand | bootstrap-from-tip (no historical replay); hash ring is gone with it — documented |
| Clock skew / DST | nothing time-based drives correctness; only `id` does |
| > batch_size backlog | next poll's `id > cursor` picks up the remainder |

## 10. Test plan (new: `tests/test_watch.py`, offline — no Odoo, no API key)

**Status: implemented and passing — 121 checks.**
`python tests/test_watch.py` (no pytest, no network, no API key).

1. `html_to_text`: tags, `<br>`, entities, script/style, `class="o_attachment"` wrappers,
   attribute-free AI bodies, empty/None → `""`, plus the 1500-char cap and fingerprint keys.
2. `WatchState`: round-trip; ring pruning; atomic save leaves no `.tmp`; corrupt file
   quarantined and bootstraps; `advance()` is monotonic.
3. `_skip_reason` matrix over synthetic message dicts: self-author, marker body,
   duplicate id, duplicate hash, empty note, valid note → `None`, same text on another MO → `None`.
4. Bootstrap table from §5.3 with a fake bridge, including the corrupt-checkpoint case.
5. `retry_call`: succeeds on the 2nd attempt; raises after N; never retries a `Fault`.
6. Fake-`OdooBridge` integration: seed 6 messages (2 valid, 1 AI-marked, 1 empty,
   1 duplicate-content, 1 self-authored) → exactly 2 LLM calls, 2 threaded replies,
   nothing re-triaged on a second poll or after a restart; a failing note lands in the
   dead-letter file while the loop continues; `--on-error halt` re-raises without
   advancing; `--dry-run` neither posts nor consumes; the poison-MO case is isolated.
7. `SingleInstanceLock`: creates + annotates the lock, refuses a second holder, releases on exit.
8. CLI: every flag parses, `--message-types` splitting/fallback, `.env` keys present.

**Manual E2E — run live against Odoo 17, results confirmed:**
- Started with `python triage.py --watch --interval 2 --max-iterations 20`; first run adopted
  the current tip (id=231), proving no historical replay.
- Injected 4 real Chatter notes authored by a *different* partner (simulating an operator):
  1 unique note → **triaged**, 1 exact duplicate → `duplicate-content`, 1 attachment-only →
  `empty-note`, 1 unique note → **triaged**. Exactly 2 LLM calls, 2 threaded replies.
- Every AI reply was re-fetched on the following poll and rejected as `self-authored`
  (and would also be caught by the marker guard).
- SIGTERM at the end of run 1 exited with `Watcher stopped.`; run 2 resumed from the
  checkpoint (`cursor=237`) and produced **zero** further LLM calls or replies.
- Two defects were found only by this live run and are now fixed + covered by tests:
  (a) Odoo strips custom HTML attributes on `mail.message.body`; (b) attachment wrapper
  text was being triaged as if it were an operator note.

> **Caution when reproducing:** posting a note *as the same admin user the watcher
> authenticates as* makes it `self-authored` and therefore skipped. To exercise the
> happy path, author the note as a different Odoo user/partner (as the E2E harness does).

## 11. Rollout / file-by-file change list — ✅ complete

1. `triage.py` — §8 sections added, `--watch` wired, three small refactors applied.
2. `.env.example` — six `WATCH_*` keys with comments.
3. `tests/test_watch.py` — new offline test module (§10), 121 checks passing.
4. `README.md` — "Continuous watch mode" subsection, `mail.message` model row,
   troubleshooting entries (lock file, self-authored skip, state reset).
5. `requirements.txt` — **no new dependencies**; a comment documents that `fcntl` is POSIX-only.
6. `.gitignore` — checkpoint, lock, quarantine and dead-letter files excluded.
7. `plans/watch-mode-design.md` — this document, corrected against live Odoo 17 behaviour.

## 12. Out of scope (explicit)

Webhooks/`base_automation` triggers, triaging models other than `mrp.production`,
attachment/vision parsing, multi-worker sharding, retrying dead-letters automatically,
and a web UI. Each is a natural follow-up; none is needed for a working `--watch`.
