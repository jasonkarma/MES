# GenAI MES Integration - Project Summary

## Overview

This project implements an intelligent defect classification and triage system that integrates GenAI capabilities into your existing Odoo MES Docker instance. The system will reduce triage latency from 72 hours to near real-time, improve classification accuracy, and automatically generate actionable maintenance recommendations.

---

## Problem Statement

**Current Pain Points:**
- Operators enter unstructured free-text defect descriptions ("shaking," "chatter," "vibrational squeal")
- Manual QA batch processing causes 48-72 hour triage delays
- Average 5.0 hours of machine queue blockage per incident
- 4.6% recurring scrap rate
- ~$18,500 monthly losses from rework, downtime, and material scrap

**Target Improvement:**
- Reduce triage latency: 72 hours → < 5 minutes (99.9% improvement)
- Improve classification accuracy: baseline → > 85%
- Auto-generate maintenance tickets for high-severity issues
- Provide real-time diagnostic checklists to operators

---

## Solution Architecture

### High-Level Components

1. **Odoo API Integration Layer** (Python XML-RPC)
   - Reads quality alerts and work orders
   - Writes back structured classification data
   - Creates maintenance tickets automatically

2. **LLM Classification Engine** (OpenAI GPT-4)
   - Parses unstructured operator text
   - Extracts ISO 14224 failure codes
   - Identifies components, symptoms, and severity
   - Uses function calling for structured output

3. **RAG System** (LangChain + ChromaDB)
   - Vector store of machine SOPs, repair manuals, ISO taxonomy
   - Retrieves relevant documentation based on failure mode
   - Provides context for action generation

4. **Action Proposal Generator**
   - Creates 3-step diagnostic checklists
   - Calculates severity scores (0-100)
   - Auto-creates maintenance tickets for high-severity issues

5. **Edge Case Handler**
   - Detects ambiguous inputs (confidence < 0.60)
   - Validates against ISO taxonomy (hallucination guard)
   - Handles missing data with escalation paths

### Data Flow

```
Operator enters defect text in Odoo
         ↓
Python integration polls quality alerts
         ↓
LLM classifies → ISO 14224 code + structured data
         ↓
RAG retrieves → relevant SOPs + repair history
         ↓
Action generator → diagnostic checklist + severity score
         ↓
Edge case handler → validates + handles anomalies
         ↓
Write back to Odoo → structured data + maintenance ticket
         ↓
Operator sees checklist in real-time (< 30 seconds)
```

---

## Key Features

### 1. Real-Time Semantic Extraction
- Converts "tooling chatter on step 2, insert T3 chipped" into:
  - Failure mode: Tooling Wear (ISO: TW-001)
  - Component: Tool Insert T3
  - Location: Step 2
  - Severity: High
  - Confidence: 0.92

### 2. Multi-Modal RAG Retrieval
- Queries vector store with failure context
- Retrieves top-3 relevant documents:
  - Machine SOPs
  - Historical repair tickets
  - ISO 14224 taxonomy references
- Injects context into action generation

### 3. Automated Action Proposal
- Generates 3-step diagnostic checklist:
  1. "Inspect Tool Insert T3 for edge chipping or wear"
  2. "Verify collet torque is set to 45 Nm (±2 Nm)"
  3. "Check insert cycle count in maintenance log"
- Auto-creates urgent maintenance ticket if severity > 70

### 4. Robust Edge Case Handling
- **Ambiguity**: "machine making noise" → requests clarification
- **Hallucination**: Invalid ISO code → uses fallback + flags for review
- **Missing data**: No component specified → attempts inference or escalates
- **System errors**: API failures → logs and continues processing other records

---

## Technical Stack

| Component | Technology | Rationale |
|-----------|-----------|-----------|
| API Client | Python XML-RPC | Native Odoo integration |
| LLM Provider | OpenAI GPT-4 | Best function calling, reliable |
| Vector Store | ChromaDB | Lightweight, local-first |
| Embeddings | OpenAI text-embedding-3-small | Consistency with LLM |
| Orchestration | LangChain | RAG pipeline management |
| Framework | Python 3.10+ | Rich ecosystem, async support |

---

## Implementation Plan

### Phase-by-Phase Breakdown

**Phase 1: Foundation** (Days 1-2)
- Set up Python project structure
- Connect to Odoo via XML-RPC
- Verify read/write access to quality alerts

**Phase 2: LLM Classification** (Days 3-4)
- Design classification prompts
- Implement OpenAI function calling
- Achieve >80% accuracy on test set
- Log prompt iterations

**Phase 3: RAG System** (Days 5-6)
- Prepare knowledge base (SOPs, manuals, taxonomy)
- Set up ChromaDB vector store
- Implement semantic retrieval pipeline

**Phase 4: Action Generation** (Day 7)
- Build diagnostic checklist generator
- Implement severity scoring
- Add auto-ticket creation logic

**Phase 5: Edge Cases** (Day 8)
- Implement ambiguity detection
- Add hallucination guards
- Create escalation paths

**Phase 6: Integration** (Days 9-10)
- Connect all components
- Run baseline scenarios
- Collect before/after metrics

**Phase 7: Documentation** (Days 11-12)
- Create setup instructions
- Document architecture
- Compile prompt iteration logs

**Total Duration**: 12-13 days

---

## Assignment Deliverables (Part A)

### 1. Updated GitHub Repo
- [x] Clear setup instructions in README
- [x] requirements.txt with dependencies
- [x] .env.example for configuration
- [x] Seed data (sample defect scenarios)
- [x] Complete source code (src/ directory)
- [x] Tests (tests/ directory)

### 2. Side-by-Side Metrics
- [ ] Baseline metrics from Odoo (triage latency, scrap rate, classification time)
- [ ] After-AI metrics (same scenarios)
- [ ] Comparison table showing improvements
- [ ] Screenshots of Odoo before/after
- [ ] Processing logs demonstrating real-time operation

### 3. Architecture Documentation
- [x] Data flow diagram (Mermaid format)
- [x] Component descriptions
- [x] Odoo model mappings
- [x] Prompt templates with explanations
- [x] Tool/function calling patterns
- [x] Edge case handling strategies

### 4. Prompt + Iteration Log
- [ ] Structured JSONL log (`logs/prompt_iterations.jsonl`)
- [ ] Each entry shows:
  - Prompt version
  - Test input
  - LLM response
  - Accuracy evaluation
  - Changes from previous version
- [ ] Summary of key improvements

---

## Success Criteria

Your implementation will meet assignment requirements if:

✅ **Reads from live system**: Polls Odoo quality alerts via API
✅ **Writes back to system**: Updates Odoo with structured data, creates maintenance tickets
✅ **Uses advanced techniques**: RAG for context, prompt engineering, function calling
✅ **Has side effects**: Actually creates/updates Odoo records (not just chat)
✅ **Uses tool calling**: OpenAI function calling for structured data extraction
✅ **Runs baseline scenarios**: Same test cases as HW1 with quantitative metrics
✅ **Handles edge cases**: Ambiguity, hallucination, missing data with recovery paths
✅ **Logs iterations**: Structured prompt evolution log (if using Claude Code for development)

---

## Key Technical Decisions

### 1. Why OpenAI GPT-4?
- Excellent function calling support (structured output)
- Fast inference (< 3 seconds)
- Reliable for classification tasks
- Alternative: Anthropic Claude (better long context) or local Ollama (free, slower)

### 2. Why ChromaDB?
- Lightweight, no server needed
- Python-native integration
- Perfect for prototype/assignment scope
- Production alternative: Pinecone or Weaviate

### 3. Why Polling vs Webhooks?
- Polling is simpler (no Odoo module modifications)
- 30-second interval is acceptable for prototype
- Webhooks require custom Odoo development
- Can upgrade later if needed

### 4. Why ISO 14224?
- Industry-standard failure taxonomy
- Structured, hierarchical codes
- Maps well to LLM function calling
- Mentioned in your HW1 proposal

---

## Test Scenarios

Use these realistic scenarios from your HW1 baseline:

### Scenario 1: Clear Tooling Failure ✓
```
Input: "Tooling chatter on step 2, tool insert T3 chipped, surface roughness high"
Expected: 
  - Failure: Tooling Wear (TW-001)
  - Severity: High
  - Action: Create urgent maintenance ticket
```

### Scenario 2: Ambiguous Vibration ⚠️
```
Input: "Station 02: Spindle vibrational chatter on step 2"
Expected:
  - Classification attempted but low confidence
  - System requests clarification: "Which spindle component?"
```

### Scenario 3: Process Blockage ℹ️
```
Input: "Blocked by upstream line occupancy"
Expected:
  - Failure: Process Deviation (PD-005)
  - Severity: Low
  - Action: Log for planning review (no maintenance ticket)
```

---

## Risk Mitigation

### Common Issues & Solutions

**Issue**: Can't connect to Odoo Docker
- Check container is running: `docker ps`
- Verify port 8069 is accessible
- Try 127.0.0.1 instead of localhost

**Issue**: LLM accuracy too low
- Add more examples (few-shot learning)
- Include work center context
- Use stricter function schemas

**Issue**: RAG retrieves irrelevant docs
- Improve query formulation
- Adjust chunk size
- Add more diverse documents

**Issue**: Odoo write fails
- Check field permissions
- Add custom fields if needed
- Use error handling wrapper

---

## Next Steps

### Before You Start Coding

1. **Review all planning documents**:
   - [`genai-integration-architecture.md`](./genai-integration-architecture.md) - Detailed system design
   - [`implementation-checklist.md`](./implementation-checklist.md) - Phase-by-phase tasks
   - [`quick-start-guide.md`](./quick-start-guide.md) - Setup instructions

2. **Verify prerequisites**:
   - Odoo Docker container running
   - Python 3.10+ installed
   - OpenAI API key obtained
   - Quality/Maintenance apps installed in Odoo

3. **Set up environment**:
   - Follow Quick Start Guide (30 minutes)
   - Run connection test
   - Create sample data in Odoo

4. **Begin implementation**:
   - Start with Phase 1 (Foundation)
   - Work through checklist sequentially
   - Test each component before moving on

### Suggested Workflow

```bash
# Day 1: Setup
- Follow Quick Start Guide
- Verify Odoo connection
- Create project structure

# Days 2-3: Core Classification
- Implement LLM classifier
- Test with sample defects
- Iterate on prompts

# Days 4-6: RAG & Actions
- Build vector store
- Implement retrieval
- Generate checklists

# Days 7-9: Integration
- Connect all pieces
- Handle edge cases
- Run end-to-end tests

# Days 10-12: Metrics & Docs
- Collect baseline metrics
- Run comparison scenarios
- Document everything

# Day 13: Polish & Submit
- Final testing
- Create submission package
- Push to GitHub
```

---

## Documentation Map

```
plans/
├── project-summary.md              ← You are here
├── genai-integration-architecture.md  ← Detailed system design
├── implementation-checklist.md     ← Phase-by-phase tasks
└── quick-start-guide.md           ← 30-minute setup guide

Start here → Quick Start Guide → Implementation Checklist → Code!
```

---

## Expected Outcomes

After completing this implementation, you will have:

1. **Working System**
   - Reads defect reports from Odoo automatically
   - Classifies with >80% accuracy in < 30 seconds
   - Writes structured data back to Odoo
   - Auto-creates maintenance tickets

2. **Measurable Results**
   - Triage latency: 72 hours → < 5 minutes
   - Classification time: 3-5 minutes → < 30 seconds
   - Auto-ticket rate: 0% → ~70% (for high-severity)

3. **Complete Documentation**
   - Architecture diagrams
   - Setup instructions anyone can follow
   - Prompt iteration logs
   - Before/after metrics with screenshots

4. **Assignment Grade**
   - Meets all Part A requirements
   - Demonstrates advanced GenAI techniques
   - Shows real system integration (not just demos)
   - Includes robust edge case handling

---

## Questions & Support

If you get stuck during implementation:

1. Check the relevant planning document
2. Review troubleshooting sections
3. Verify prerequisites are met
4. Check logs for error messages
5. Test components individually

**Key Resources:**
- Odoo API Docs: https://www.odoo.com/documentation/16.0/developer/reference/external_api.html
- OpenAI Function Calling: https://platform.openai.com/docs/guides/function-calling
- LangChain RAG: https://python.langchain.com/docs/use_cases/question_answering/

---

## Ready to Start?

Follow this path:

1. Read [`quick-start-guide.md`](./quick-start-guide.md) → Set up in 30 minutes
2. Read [`genai-integration-architecture.md`](./genai-integration-architecture.md) → Understand system design
3. Follow [`implementation-checklist.md`](./implementation-checklist.md) → Build phase by phase
4. Reference this summary for high-level context

**Good luck with your implementation! 🚀**

---

*Project Summary v1.0 - Created: 2026-09-28*
