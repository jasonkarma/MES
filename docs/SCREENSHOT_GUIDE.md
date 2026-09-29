# Screenshot Guide for Assignment Submission

This guide shows exactly what to capture for your Part A assignment deliverables.

## Required Screenshots (8 total)

### 1. Terminal Output - Full Demo Run
**File:** Terminal where you ran `python tests/demo_standalone.py`
**What to capture:**
- Scroll to the top of the output
- Capture from "Starting GenAI Demo..." through all 5 scenarios
- Shows: LLM processing, classifications, confidence scores

**Key elements visible:**
- ✓ Odoo authentication
- Processing 5 test scenarios
- LLM classifications (defect types, ISO codes)
- RAG document retrieval
- Action step generation

---

### 2. Terminal Output - Metrics Comparison
**File:** Same terminal, scroll to bottom
**What to capture:**
- The "BEFORE/AFTER METRICS COMPARISON" section
- Shows quantitative improvements

**Key metrics visible:**
```
BASELINE (Manual Process):
- Average triage time: 4-8 hours
- Total manual processing: 72 hours

WITH AI:
- Average AI processing: <30 seconds
- Total time saved: 71.97 hours
- Improvement: 99.96%
```

---

### 3. Terminal Output - Edge Case Handling
**File:** Same terminal, find Scenario 2
**What to capture:**
- Scenario 2: "strange noise from machine"
- The ambiguity detection and escalation

**Key elements visible:**
```
⚠ Ambiguity detected
Confidence: 0.65 (below threshold 0.70)
→ Escalating to human operator
→ Requesting additional information
```

---

### 4. Code - Prompt Engineering (Classifier)
**File:** Open `src/llm/classifier.py` in VSCode
**What to capture:**
- Lines 1-50 (the prompt design)
- Shows structured prompt with ISO 14224 classification
- Function calling schema

**Key elements visible:**
- DEFECT_CLASSIFICATION_PROMPT
- ISO failure mode taxonomy
- JSON schema for structured output
- Confidence scoring logic

---

### 5. Code - RAG Implementation
**File:** Open `src/rag/simple_rag.py` in VSCode
**What to capture:**
- Lines 1-60 (RAG class and retrieval method)
- Shows semantic document retrieval

**Key elements visible:**
```python
class SimpleRAG:
    def retrieve(self, query: str, top_k: int = 3):
        # Semantic search over maintenance docs
```

---

### 6. Code - Edge Case Handling
**File:** Open `src/edge_cases.py` in VSCode
**What to capture:**
- The full file (it's short)
- Shows confidence thresholds and escalation logic

**Key elements visible:**
```python
def detect_ambiguity(confidence: float) -> bool:
    return confidence < CONFIDENCE_THRESHOLD

def escalate_to_human(defect_description: str):
    # Human-in-the-loop fallback
```

---

### 7. Architecture Diagram
**File:** Open `plans/genai-integration-architecture.md` in VSCode Preview
**What to capture:**
- The ASCII diagram showing data flow
- Shows: Odoo → Python → OpenAI → RAG → Back to Odoo

**Key elements visible:**
```
┌─────────────┐
│ Odoo MES    │
│ (Quality    │
│  Alerts)    │
└──────┬──────┘
       │
       ↓
┌─────────────┐     ┌──────────────┐
│ LLM Engine  │────→│ RAG System   │
│ (OpenAI     │     │ (Maintenance │
│  GPT-4)     │     │  Docs)       │
└─────────────┘     └──────────────┘
```

---

### 8. Test Scenarios & Baseline Data
**File:** Open `data/seed_defects.json` in VSCode
**What to capture:**
- First 2-3 scenarios showing:
  - defect_description (free text input)
  - expected_classification
  - baseline_metrics (manual processing times)

**Key elements visible:**
```json
{
  "scenario_id": 1,
  "defect_description": "Spindle bearing overheating...",
  "expected_classification": "Mechanical - Bearing Failure",
  "baseline_metrics": {
    "manual_triage_time_minutes": 240
  }
}
```

---

## Optional but Impressive

### 9. Odoo Web Interface (Bonus)
**File:** Browser at `http://localhost:8069`
**What to capture:**
- Manufacturing dashboard showing your MES setup
- Proves integration with live system

---

### 10. Prompt Iteration Log
**File:** Open `src/llm/prompts.py` in VSCode
**What to capture:**
- Comments showing prompt refinements
- Version history of prompt changes

**Key elements visible:**
```python
# Version 1: Basic classification (too vague)
# Version 2: Added ISO 14224 taxonomy (improved structure)
# Version 3: Added confidence scoring (current)
```

---

## How to Organize for Submission

Create a folder structure:
```
screenshots/
├── 01-terminal-full-run.png
├── 02-terminal-metrics.png
├── 03-terminal-edge-case.png
├── 04-code-classifier.png
├── 05-code-rag.png
├── 06-code-edge-cases.png
├── 07-architecture-diagram.png
├── 08-test-scenarios.png
└── optional/
    ├── odoo-web-interface.png
    └── prompt-iterations.png
```

---

## What to Highlight in Each Screenshot

Use annotation tools (Preview, Markup, or Snagit) to:
1. **Circle** key metrics (e.g., "99.96% improvement")
2. **Arrow** pointing to edge case handling
3. **Box** around confidence scores
4. **Highlight** ISO 14224 classifications

---

## Assignment Document Structure

In your written submission, reference screenshots like:
```
"The system achieved 99.96% time reduction (see Screenshot 02)..."
"Edge case handling is demonstrated in Screenshot 03..."
"RAG implementation shown in Screenshot 05 retrieves..."
```

---

## Quick Verification Checklist

Before submitting, verify each screenshot shows:
- [ ] AI processing in action (not just code)
- [ ] Quantitative metrics (numbers!)
- [ ] Edge case handling with escalation
- [ ] Integration with Odoo (authentication success)
- [ ] RAG document retrieval
- [ ] Structured prompts with ISO standards
- [ ] Before/after comparison
- [ ] Function calling / structured output

---

## Tips for Clean Screenshots

1. **Terminal:** Use light theme or high contrast
2. **Code:** Enable syntax highlighting
3. **Crop:** Remove personal info from window decorations
4. **Size:** 1920x1080 or higher resolution
5. **Format:** PNG (not JPG) for code clarity
6. **Labels:** Add filename in the screenshot or caption

---

## Need Help?

If your terminal output got cleared:
```bash
cd /Users/karma/Documents/SF\ docs/MES/MES
python tests/demo_standalone.py > output.txt
cat output.txt
```

This saves the output so you can scroll through it again.
