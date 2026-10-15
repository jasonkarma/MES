# SOP — Operator Defect Reporting with AI Triage

**Applies to:** Odoo 17 MES (`mrp.production` / Manufacturing Orders)
**Tool:** `triage.py --watch` (external XML-RPC bridge; no Odoo module required)
**Audience:** Shop-floor operator / QA reviewer (Part 1) and demo operator (Part 2)

---

## The one-line summary

> **You type what you see. The AI replies in the same thread with a failure code, a
> severity, and three checks to run. You stay in Odoo the whole time.**

Nothing about your workflow changes except that a reply appears ~10 seconds later. You do
not open a ticket, you do not classify anything, you do not leave the order.

---

## Division of labour

| You (human) | The AI (automated) |
|---|---|
| Notice a defect at the machine | Watches every MO's Chatter, 24/7, every 10 s |
| Type one or two plain sentences + **Log note** | Reads the note, strips the HTML |
| Read the reply | Matches it to an ISO-style failure code |
| Run the three checks it lists | Sets severity: LOW / MEDIUM / CRITICAL |
| Decide whether to stop the work center | Skips duplicates, its own replies, blank notes |
| Mark the note resolved / add follow-up info | Records the triage in `logs/classifications.jsonl` |

**The AI never stops a machine, never scraps a part, and never closes a note.** It produces
a recommendation; a human acts on it. That boundary is deliberate and is stated in every
reply ("Human verification required").

---

## Part 1 — Operator SOP (the 5 steps you perform in Odoo)

### Step 1 — Open the Manufacturing Order
`Manufacturing` → `Operations` → `Manufacturing Orders` → select the order
(e.g. **WH/MO/00001**). Make sure you are on the order you were actually working on.

### Step 2 — Open the Chatter and click **Log note**
Scroll to the bottom of the order. In the Chatter row, click **Log note** — *not*
`Send message`.

| Button | What it does | Use it? |
|---|---|---|
| **Log note** | Internal note, yellow, stored as `mail.message` type `comment`, no email sent | ✅ **Yes — this is the defect-report button** |
| **Send message** | Also type `comment`, but notifies followers (may email) | ❌ Not for defects |
| Automatic lines ("Waiting → In Progress") | Type `notification` | ❌ The AI ignores these on purpose — they are not defects |

### Step 3 — Write the note like you talk
Type one or two sentences. Lowercase, typos and abbreviations are fine — do **not** try to
write formally and do **not** look up technical terms.

**What raises the quality of the AI answer** (name these three things if you can):

1. **Where** — station / step number (`station 1`, `step 2`)
2. **What** — the concrete part, tool or material (`drawer slides`, `insert T3`, `corner desk left sit`)
3. **How it's failing** — the symptom you can see/hear/feel (`binds halfway`, `chipped`, `squeal`)

Good notes:

```
station 1 drawer black slides are rough, drawer binds halfway and wont close flush
corner desk left sit arrives with chipped edge on the front, 2 of 3 pieces scratched
drill holes on the chair base are off centre, screws wont line up when we assemble
tooling chatter on step 2, insert T3 chipped, surface roughness high
```

Weak notes (the AI will still answer, but generically):

```
machine making noise
broken
bad part
```

### Step 4 — Click **Log**, then wait ~10 seconds
The note appears in the thread. The watcher polls every 10 s, so allow up to ~15 s on a
busy poll. You can keep working; you do not need to refresh repeatedly.

### Step 5 — Read the reply and act on it
A reply arrives **nested under your note** (same thread):

```
🤖 AI Triage — Operational Defect
Reported note: station 1 drawer black slides are rough, drawer binds halfway...
Failure code (ISO): MEC-005
Severity: HIGH
Recommended operator checks:
  1. Inspect drawer slides for debris or misalignment; clearance spec 0.5–1.0 mm
  2. Check slide mounting screws; torque to 3 Nm
  3. Test drawer travel 5 cycles; must close flush without binding
Generated automatically by triage.py using model gpt-4o-mini.
Human verification required before restarting the work center. [ai-triage:v1]
```

**Severity decides your next move:**

| Severity | Your action |
|---|---|
| **LOW** | Log it, keep producing, fix at the next changeover |
| **MEDIUM** | Run the three checks now; rework likely; production may continue short-term |
| **CRITICAL** | **Stop the operation.** Escalate to maintenance/supervisor before restarting |

### If no reply appears
Work down this list — one of these is almost always the cause:

1. **You posted as the same Odoo user the bridge logs in as.** Odoo's default is a single
   shared account, and the bridge skips notes authored by its own login to avoid triaging
   itself. → Log in as a different user (e.g. `demo`), **or** restart the watcher with
   `python triage.py --watch --allow-self-authored`.
2. **You used `Send message` instead of `Log note`** and it became a mail-type message —
   rare, but re-log as a **Log note**.
3. **The watcher isn't running.** Check with `ps aux | grep 'triage.py --watch'`.
4. **Your note was identical to one already triaged on that order** — it is skipped as
   `duplicate-content`. Add one word that differs.
5. **Attachment-only / blank note** — if you only attached a photo with no text, the note is
   skipped (`empty-note`). Photos are not analysed yet; **always add a sentence of text.**
6. **A failure was recorded** — check the dead-letter log:
   `cat logs/triage_watch_failures.jsonl`

---

## Part 2 — Demo SOP (what *you* do at the keyboard right now)

Today a watcher is already running (`pid 9718`, started 1:49 PM, cursor `id=241`), started
with defaults, so it **skips notes you write as yourself**. Two ways to run the demo:

```
Option A — restart the watcher so it also triages your own notes
  Ctrl-C in the terminal running it, then:
     python triage.py --watch --allow-self-authored

Option B — keep it running and post as a different user
  Log into Odoo as  demo / demo  (Marc Demo), then follow Part 1.
```

### Recovering notes that were already skipped

A watcher that ran with the default and skipped your notes has **already advanced its
cursor past them**, so simply adding the flag will not go back. Two things must happen:
the checkpoint's **content-hash ring** (which would skip your text as `duplicate-content`)
and the **cursor** (which would never re-fetch the note) both have to be stepped back.

Exact recipe — stop, set aside the checkpoint, restart from a `--since-id` that predates
the skipped notes:

```bash
# 1. stop the old watcher (SIGTERM: it finishes the in-flight note and flushes state)
kill -TERM $(pgrep -f 'triage.py --watch' | head -1) ; sleep 2

# 2. set the old checkpoint aside, and drop its lock
mv logs/watch_state.json logs/watch_state.json.bak
rm -f logs/watch_state.json.lock

# 3. restart with the flag, replaying everything from the first skipped note
nohup python triage.py --watch --allow-self-authored --interval 5 \
      --since-id 244 --state-file logs/watch_state.json > logs/watch.log 2>&1 &
```

`--since-id N` sets the cursor to `N`, so every note with `id > N` is re-examined. Notes
that genuinely were triaged are not re-triaged: their text is not in the fresh hash ring,
but their **reply** is rejected by the AI-marker guard, and any note whose conversation the
operator has moved past will simply be visibly answered once. Expect the log to show a run
of `Triaged …` followed by `Skipping … ai-marker` for each reply — that pairing is the
proof the self-loop guard is working.

Then, in order:

1. Open **WH/MO/00001** → Chatter → **Log note**.
2. Paste one of the notes from Step 3 above and click **Log**.
3. Wait ~10 s.
4. **Screenshot 1** — the note before any reply.
5. **Screenshot 2** — the AI reply underneath it (code, severity, three checks).
6. Optional **Screenshot 3** — the terminal line, proving the mechanism:
   `✓ Triaged message 243 -> reply id=244 on WH/MO/00001 (cursor=243 triaged=1 ...)`
7. Optional **Screenshot 4** — the audit trail: `logs/watch_state.json` (checkpoint) and
   `logs/classifications.jsonl` (verdict history).

### Quick self-check after the demo

```bash
ps aux | grep '[t]riage.py --watch'                     # is it alive?
cat logs/watch_state.json                               # cursor + triaged/skipped counters
cat logs/triage_watch_failures.jsonl 2>/dev/null        # anything dead-lettered?
grep -E "Triaged|Skipping" logs/mes_integration.log | tail
```

---

## Part 3 — Why each of the six "hard parts" is handled (for the write-up)

| Requirement | How it works | Why it matters operationally |
|---|---|---|
| **Poll new `mail.message`** | `search_read` filtered to `model = mrp.production`, `id > cursor`, `message_type in (comment, email)` | Discovers defects with no button press; ignores Odoo's own state-change lines |
| **Strip HTML** | stdlib `html.parser`: tags → line breaks, entities decoded, attachment/signature blocks dropped, 1500-char cap | The LLM sees your words, not `<p>` markup or the filename `report.pdf` |
| **Prevent duplicates** | Durable checkpoint: last processed id + rings of processed ids and `sha1(order + text)` hashes | Refreshing the page, restarting the tool, or double-clicking **Log** does not produce a second answer |
| **Ignore AI messages** | Three guards: author id, plain-text marker `[ai-triage:v1]`, and the cursor | The AI cannot answer itself in an infinite loop |
| **Handle failures** | Transient errors retry with backoff; permanent ones go to `logs/triage_watch_failures.jsonl`; the loop keeps running | One unreadable note never blocks the queue or crashes the tool |
| **Preserve a checkpoint** | `logs/watch_state.json`, written atomically after every handled note | Power loss or `kill -9` loses at most the note in flight; nothing is silently lost or re-triaged |

**Explicitly out of scope today:** photo/vision analysis, stopping a machine, creating
maintenance tickets, closing notes, or routing outside Odoo. Operators and supervisors
retain all authority.

---

## Part 4 — Troubleshooting quick table

| Symptom | Cause | Fix |
|---|---|---|
| Note sits there, no reply | Posted as the bridge's own user | `--allow-self-authored`, or post as a different user |
| `Another triage.py --watch is already running` | Single-instance lock held | Stop the other process, or use a second `--state-file` |
| Note skipped as `duplicate-content` | Same text already triaged on that order | Change a word |
| Note skipped as `empty-note` | Under 4 characters of text, or attachment-only | Add a sentence of text |
| Reply looks generic / code `UNCL-999` | Your note lacked station, component or symptom | Add the who/what/how |
| Answer appeared twice | Should not happen — check the two notes are not identical | Report it; the hash guard is designed to prevent this |
| Watcher gone after a terminal restart | It is a foreground process | Re-run `python triage.py --watch` (it resumes from the checkpoint) |

---

## Known limitations (read this before you write it up)

These are real and worth stating honestly rather than letting a reviewer find them:

1. **The ISO code is model-generated, not validated.** There is no fixed taxonomy check in
   `triage.py`, so the same symptom can come back with a different code on a re-run —
   observed live: the hydraulic-leak note returned `MECH-003` on one run and `MECH-001` on
   another, and two unrelated symptoms both landed on `MECH-001`. The prefix (`MECH`, `TW`,
   `PROC`, `MAT`, `ELEC`) is stable and meaningful; the `-NNN` suffix is not a real
   catalogue. If you need determinism, the code must be validated against a taxonomy list
   (or pinned to a fixed enum) before anything downstream relies on it.
2. **Severity is stable and is the useful signal.** The `LOW/MEDIUM/CRITICAL` decision
   matched expectation on every live test, which is why the SOP's "stop / continue" rule
   is keyed on severity, not on the code.
3. **Photos are ignored.** An attachment-only note strips to empty text and is skipped as
   `empty-note`. There is no vision step.
4. **Only `mrp.production` is watched.** Defects noted on other models (work orders,
   quality alerts) are out of scope.
5. **Replies are internal notes.** They are visible in the Chatter but do not email
   followers, do not create a maintenance ticket, and do not change the MO state.
6. **Multi-instance is lock-protected, not distributed.** `fcntl.flock` is POSIX-only and
   host-local; two watchers on two hosts pointed at the same Odoo would both act. The
   content-hash guard makes that mostly harmless (the second one's LLM call is wasted but
   it will post a second reply only if its checkpoint has not seen the text).

---

## Appendix — CLI reference for the operator/demo

```bash
python triage.py --watch                      # run continuously (defaults: 10 s poll)
python triage.py --watch --allow-self-authored   # also triage notes YOU write
python triage.py --watch --interval 5         # faster polling for demos
python triage.py --watch --dry-run            # show the reply, post nothing, consume nothing
python triage.py --watch --backfill 10        # replay the last 10 chatter notes
python triage.py --watch --on-error halt -v   # stop on the first unprocessable note
python tests/test_watch.py                    # 127 offline checks (no Odoo, no API key)
```

Full design rationale: [`plans/watch-mode-design.md`](../plans/watch-mode-design.md).
Verification log and live results are recorded there in §10.
