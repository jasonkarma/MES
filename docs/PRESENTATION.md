# GenAI MES Defect Triage — Final Presentation

**Jason Chou** · Odoo 17 MES (Docker) · Python + OpenAI `gpt-4o-mini` · October 2026

> Slide-by-slide talking points. Full detail: [`WATCH_MODE_SOP.md`](WATCH_MODE_SOP.md) ·
> [`../plans/watch-mode-design.md`](../plans/watch-mode-design.md)

---

## 1 · The problem

- Operators report defects in **free text**: *"shaking"*, *"chatter"*, *"something is off"*
- Triage is manual and batched → **48–72 h** delay per defect
- Machine waits for a decision → **5.0 h** queue blockage per incident
- **4.6%** recurring scrap ≈ **$18,500/month** lost

**Goal:** classify and respond where the operator already is — the Odoo Chatter — in seconds.

---

## 2 · What we built

```
Odoo 17 MES (Docker)  ──XML-RPC──▶  Python GenAI layer  ──▶  OpenAI gpt-4o-mini
mrp.production · quality.alert       triage.py               │
                                     src/ (classifier, RAG,   │
                                     action gen, edge cases)  │
                                            ▲_________________│
                                     verdict posted back to the
                                     same Chatter thread
```

- **No Odoo module, no Odoo source change, no new dependency.**
- Two modes in one file: **one-shot** (single note) and **`--watch`** (unattended continuous triage).

---

## 3 · Demo — one sentence in, triage out

**Operator logs a note on WH/MO/00001:**

> `Hydraulic pressure dropping on clamp station, hissing noise near the seal`

**~5 s later, nested under that note in the Chatter:**

> 🤖 **AI Triage — Operational Defect**
> Failure code (ISO): `MECH-001` · **Severity: CRITICAL**
> 1. Stop operation immediately at clamp station.
> 2. Inspect hydraulic seal for damage or wear.
> 3. Check hydraulic pressure gauge; ensure it reads above 150 psi.
> *Human verification required before restarting the work center.*

The operator never left Odoo, never filled a form, never picked a code.

---

## 4 · The six hard parts (and how each is solved)

| # | Requirement | Solution |
|---|---|---|
| 1 | Find new defects | Poll `mail.message`: `model=mrp.production`, `id > cursor`, `message_type in (comment, email)` — human notes only |
| 2 | Read messy HTML | stdlib `html.parser`: tags → line breaks, entities decoded, attachment/signature blocks dropped, 1500-char cap |
| 3 | Never answer twice | Durable checkpoint: last id + rings of processed ids and `sha1(order + text)` hashes |
| 4 | Never answer itself | Three guards — author id, plain-text marker `[ai-triage:v1]`, cursor. All run **before** the LLM call |
| 5 | Survive failures | Transient → retry with jittered backoff. Deterministic → dead-letter JSONL. Loop never dies |
| 6 | Survive crashes | `watch_state.json`, atomic write (`tmp → fsync → os.replace`) after every note |

---

## 5 · Results (measured live, not estimated)

| Metric | Manual baseline | With AI |
|---|---|---|
| Triage latency | 48–72 h | **< 10 s** |
| Operator effort | Classify + code + write steps | **Type one sentence** |
| Output | Inconsistent | Code + severity + 3 measurable checks |
| Answers per note | — | **Exactly 1**, verified across restarts |
| Duplicate replies | — | **0** |
| Unhandled notes | — | **0** (`failed: 0`, no dead-letters) |

**Test evidence**

- **127 offline automated checks** (`tests/test_watch.py`) — no Odoo, no API key, no network.
- **Live run:** 4 notes from 2 operators → 2 triaged, 1 duplicate skipped, 1 attachment-only skipped. Exactly **2 LLM calls**.
- **Restart:** same checkpoint, watcher restarted → **0** extra LLM calls, **0** duplicate replies.
- **Self-loop:** every AI reply re-fetched next poll → rejected as `ai-marker`.

---

## 6 · What the live run taught us

Three bugs that only a real Odoo exposed — each fixed **and** covered by a test:

1. **Odoo strips custom HTML attributes** from `mail.message.body` → our `data-*` marker didn't survive. Guard now uses plain-text signals.
2. **Attachment-only notes were triaged as the filename** (`report.pdf` → a confident, useless verdict). Attachment blocks are now dropped entirely.
3. **A malformed row could livelock the poll.** Now dead-lettered with the cursor advanced.

> **Honest limitation:** the ISO **prefix** (`MECH`, `TW`, `PROC`, `MAT`, `ELEC`) is stable, but
> the `-NNN` suffix is **model-generated and not validated against a taxonomy** — the same
> symptom can return `MECH-001` or `MECH-003`. **Severity** was correct on every live test, so
> the SOP keys its stop/continue rule on severity. Out of scope: photo analysis, auto-stopping
> machines, auto-tickets, models other than `mrp.production`.

---

## 7 · Safety and trust

- **Humans keep authority.** The AI recommends; it never stops a machine, scraps a part, closes a note, or creates a ticket.
- **Severity → action:** LOW = log & continue · MEDIUM = run the 3 checks · **CRITICAL = stop & escalate.**
- **Auditable:** verdicts in `logs/classifications.jsonl`, failures in a dead-letter file, cursor in `watch_state.json`.
- **Secrets in `.env` only**; Odoo RBAC enforced; single-instance lock prevents double-posting.

---

## 8 · Deliverables

| Artifact | Purpose |
|---|---|
| `triage.py` (1,459 lines) | One-shot **and** `--watch` triage bridge — one file to run |
| `tests/test_watch.py` | 127 offline checks (fake Odoo + fake LLM) |
| `plans/watch-mode-design.md` | Design, trade-offs, failure model, verification log |
| `docs/WATCH_MODE_SOP.md` | Operator runbook + troubleshooting |
| `docs/ARCHITECTURE_NOTE.md`, `README.md` | Architecture, prompts, setup, metrics |
| `src/` | Classifier · action generator · RAG · edge-case handler · orchestrator |

---

## 9 · Closing

> A defect used to take **days** to classify. Now the operator types one sentence in the tool
> they already have open, and gets a severity, a standard failure code, and three concrete
> checks back in **under ten seconds** — once per note, crash-safe, unattended, and always
> reviewed by a human before the machine restarts.
