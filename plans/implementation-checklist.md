# GenAI MES Integration - Implementation Checklist

## Pre-Implementation Setup

### Environment Verification
- [ ] Verify Odoo Docker container is running (`docker ps`)
- [ ] Test Odoo web interface access (http://localhost:8069)
- [ ] Note Odoo database name, username, and password
- [ ] Confirm which Odoo modules are installed (Manufacturing, Quality, Maintenance)
- [ ] Check Python version (need 3.10+)

### API Keys & Credentials
- [ ] Obtain OpenAI API key (or choose alternative: Anthropic, Ollama)
- [ ] Store credentials in `.env` file (never commit to Git)
- [ ] Test API key with simple curl/python request

### Project Setup
- [ ] Create virtual environment: `python -m venv venv`
- [ ] Activate: `source venv/bin/activate` (Mac/Linux) or `venv\Scripts\activate` (Windows)
- [ ] Create initial project structure (see below)
- [ ] Initialize Git repository: `git init`
- [ ] Create `.gitignore` (exclude `.env`, `venv/`, `__pycache__/`, `*.pyc`, `logs/*.jsonl`)

---

## Phase 1: Odoo Connection (Target: Day 1)

### 1.1 Create Project Structure
```
mes-genai-integration/
├── src/
│   ├── __init__.py
│   ├── config.py
│   └── odoo_client.py
├── tests/
│   └── test_connection.py
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md
```

### 1.2 Install Dependencies
Create `requirements.txt`:
```
python-dotenv==1.0.0
requests==2.31.0
openai==1.12.0
langchain==0.1.10
langchain-openai==0.0.6
chromadb==0.4.22
pypdf==4.0.1
fastapi==0.109.2
uvicorn==0.27.1
pydantic==2.6.1
```

- [ ] Run: `pip install -r requirements.txt`

### 1.3 Create Configuration
File: `src/config.py`
- [ ] Load environment variables from `.env`
- [ ] Define Odoo connection parameters
- [ ] Define LLM provider settings
- [ ] Add logging configuration

### 1.4 Build Odoo API Client
File: `src/odoo_client.py`
- [ ] Implement authentication method
- [ ] Create generic `search()` method
- [ ] Create generic `read()` method
- [ ] Create generic `write()` method
- [ ] Create generic `create()` method
- [ ] Add error handling and retries

### 1.5 Test Connection
File: `tests/test_connection.py`
- [ ] Test authentication
- [ ] Test reading manufacturing orders
- [ ] Test reading quality alerts
- [ ] Test reading quality checks
- [ ] Test writing to a test record (create dummy quality alert, then delete)
- [ ] Verify all operations succeed

**Deliverable**: Working Odoo API client that can read/write data

---

## Phase 2: LLM Classification (Target: Days 2-3)

### 2.1 Create LLM Module Structure
```
src/llm/
├── __init__.py
├── prompts.py
├── classifier.py
├── function_schemas.py
└── action_generator.py
```

### 2.2 Design Prompts
File: `src/llm/prompts.py`
- [ ] Create `CLASSIFICATION_PROMPT_V1` template
- [ ] Define system message for defect classification
- [ ] Add examples (few-shot learning) of good classifications
- [ ] Document prompt variables (operator_text, work_order, etc.)

### 2.3 Define Function Schemas
File: `src/llm/function_schemas.py`
- [ ] Create `classify_defect` function schema
- [ ] Define parameters: failure_mode, iso_code, symptom, component, location, severity, confidence
- [ ] Add enums for categorical fields
- [ ] Validate against OpenAI function calling format

### 2.4 Implement Classifier
File: `src/llm/classifier.py`
- [ ] Initialize OpenAI client
- [ ] Create `classify_defect(operator_text, context)` function
- [ ] Format prompt with operator input + work order context
- [ ] Call OpenAI API with function calling
- [ ] Parse and validate response
- [ ] Return structured classification result

### 2.5 Test Classification
- [ ] Create test dataset (10 sample defect descriptions)
- [ ] Run classifier on each sample
- [ ] Manually evaluate accuracy
- [ ] Log results with confidence scores
- [ ] Iterate on prompt if accuracy < 80%

### 2.6 Prompt Iteration Logging
File: `logs/prompt_iterations.jsonl`
- [ ] Log each prompt version
- [ ] Log test inputs and outputs
- [ ] Log accuracy evaluation
- [ ] Document changes between versions

**Deliverable**: Working defect classifier with >80% accuracy on test set

---

## Phase 3: RAG System (Target: Days 4-5)

### 3.1 Prepare Knowledge Base
Directory: `data/knowledge_base/`
- [ ] Create or collect machine SOPs (PDFs)
- [ ] Create ISO 14224 taxonomy reference (JSON)
- [ ] Export historical repair tickets from Odoo (CSV)
- [ ] Create sample maintenance procedures (text/PDF)

### 3.2 Build RAG Module
```
src/rag/
├── __init__.py
├── vector_store.py
├── embeddings.py
└── retriever.py
```

### 3.3 Document Processing
File: `src/rag/embeddings.py`
- [ ] Load PDFs with PyPDFLoader
- [ ] Load CSV with CSVLoader
- [ ] Split documents into chunks (500 chars, 50 overlap)
- [ ] Generate embeddings with OpenAI `text-embedding-3-small`

### 3.4 Vector Store Setup
File: `src/rag/vector_store.py`
- [ ] Initialize ChromaDB
- [ ] Create collection for MES documents
- [ ] Add documents with embeddings
- [ ] Persist to disk

### 3.5 Retrieval Implementation
File: `src/rag/retriever.py`
- [ ] Create `retrieve_context(query, k=3)` function
- [ ] Format query from classification result
- [ ] Perform similarity search
- [ ] Return top-k relevant documents

### 3.6 Test Retrieval Quality
- [ ] Query: "tool insert chipping" → expect SOP about inserts
- [ ] Query: "vibration assembly line" → expect vibration troubleshooting
- [ ] Query: "collet torque" → expect torque specifications
- [ ] Evaluate relevance of retrieved documents

**Deliverable**: Working RAG system that retrieves relevant maintenance docs

---

## Phase 4: Action Generation (Target: Day 6)

### 4.1 Build Action Generator
File: `src/llm/action_generator.py`
- [ ] Create `generate_checklist(classification, context_docs)` function
- [ ] Format prompt with failure data + RAG context
- [ ] Call LLM to generate 3-step diagnostic checklist
- [ ] Parse response

### 4.2 Severity Scoring
- [ ] Implement `calculate_severity_score(classification)` function
- [ ] Consider: failure mode, symptom, component criticality
- [ ] Return score 0-100
- [ ] Define thresholds: < 50 = Low, 50-70 = Medium, 70-90 = High, > 90 = Critical

### 4.3 Maintenance Ticket Logic
- [ ] Create `should_create_ticket(severity_score, classification)` function
- [ ] Auto-create ticket if severity > 70
- [ ] Format ticket description with AI insights
- [ ] Add priority based on severity

### 4.4 Test Action Generation
- [ ] Test with high-severity failure (tooling wear) → expect urgent ticket
- [ ] Test with low-severity (blocked line) → expect no ticket
- [ ] Evaluate checklist quality (actionable? specific?)

**Deliverable**: Action generator that produces useful diagnostic checklists

---

## Phase 5: Edge Case Handling (Target: Day 7)

### 5.1 Create Edge Case Module
File: `src/edge_cases.py`
- [ ] Implement `detect_ambiguity(classification)` function
- [ ] Implement `validate_classification(classification)` function
- [ ] Implement `check_completeness(classification)` function
- [ ] Implement `safe_odoo_write(model, record_id, values)` function

### 5.2 Ambiguity Detection
- [ ] Check if confidence < 0.60
- [ ] Generate clarification questions
- [ ] Return questions to operator (or log for manual review)

### 5.3 Hallucination Guards
- [ ] Load valid ISO codes from taxonomy
- [ ] Validate LLM-generated code against whitelist
- [ ] If invalid, use fallback code "UNCL-999"
- [ ] Flag record for human review

### 5.4 Missing Data Recovery
- [ ] Check for required fields: failure_mode, component, location
- [ ] Attempt to infer from work order BOM or context
- [ ] If still missing, escalate to human

### 5.5 Test Edge Cases
- [ ] Test: "machine making noise" (ambiguous) → expect clarification request
- [ ] Test: empty input → expect error handling
- [ ] Test: LLM generates fake ISO code → expect validation failure
- [ ] Test: Odoo API timeout → expect retry logic

**Deliverable**: Robust system that handles ambiguous/invalid inputs gracefully

---

## Phase 6: End-to-End Integration (Target: Days 8-9)

### 6.1 Build Main Orchestrator
File: `src/main.py`
- [ ] Create main processing loop
- [ ] Poll Odoo for new quality alerts (every 30 seconds)
- [ ] For each new alert:
  - [ ] Extract operator text and context
  - [ ] Classify defect
  - [ ] Retrieve RAG context
  - [ ] Generate action checklist
  - [ ] Handle edge cases
  - [ ] Write back to Odoo
  - [ ] Create maintenance ticket if needed
  - [ ] Log all steps

### 6.2 Implement Polling Loop
- [ ] Query: `quality.alert WHERE create_date > last_check AND processed_by_ai = False`
- [ ] Store last check timestamp
- [ ] Process alerts in batches (max 10 at a time)
- [ ] Update `processed_by_ai = True` after processing

### 6.3 Write-Back Implementation
- [ ] Update quality alert fields:
  - `failure_mode`
  - `iso_code`
  - `ai_checklist`
  - `ai_confidence`
  - `processed_by_ai`
  - `ai_processed_date`
- [ ] Create maintenance request if severity > threshold
- [ ] Add note to work order with AI triage timestamp

### 6.4 Run Integration Tests
- [ ] Create test quality alert in Odoo manually
- [ ] Wait for polling loop to detect it
- [ ] Verify classification appears in Odoo
- [ ] Verify maintenance ticket is created
- [ ] Check logs for errors

**Deliverable**: Fully integrated system processing real Odoo defects

---

## Phase 7: Metrics Collection (Target: Day 10)

### 7.1 Collect Baseline Metrics
File: `src/metrics.py`
- [ ] Query Odoo for historical quality alerts (last 3 months)
- [ ] Calculate average triage latency (qa_review_date - create_date)
- [ ] Calculate scrap rate from manufacturing orders
- [ ] Calculate time to maintenance ticket creation
- [ ] Export to CSV: `data/baseline_metrics.csv`

### 7.2 Collect After-AI Metrics
- [ ] Run system on test scenarios
- [ ] Track processing time per defect
- [ ] Track classification confidence scores
- [ ] Track auto-ticket creation rate
- [ ] Track escalation rate (edge cases)
- [ ] Export to CSV: `data/after_ai_metrics.csv`

### 7.3 Create Comparison Table
File: `data/metrics_comparison.md`
- [ ] Side-by-side table with all metrics
- [ ] Calculate percentage improvements
- [ ] Add screenshots showing before/after Odoo screens
- [ ] Document any surprising findings

### 7.4 Collect Qualitative Data
- [ ] Test with 5 sample defect scenarios
- [ ] For each scenario, capture:
  - Input text
  - Classification result
  - Generated checklist
  - RAG context used
  - Whether it would have helped operator
- [ ] Document in `data/qualitative_analysis.md`

**Deliverable**: Comprehensive before/after metrics analysis

---

## Phase 8: Documentation (Target: Days 11-12)

### 8.1 Create README
File: `README.md`
- [ ] Project overview and problem statement
- [ ] Prerequisites (Python, Docker, API keys)
- [ ] Installation instructions
  - Clone repo
  - Create `.env` from `.env.example`
  - Install dependencies
  - Set up Odoo connection
- [ ] Usage instructions
  - Run main script
  - Monitor logs
  - Check Odoo for results
- [ ] Architecture overview (link to detailed doc)
- [ ] Metrics summary (link to comparison table)
- [ ] Known limitations
- [ ] Future improvements

### 8.2 Document Prompts
File: `docs/prompt_engineering.md`
- [ ] Document all prompt templates
- [ ] Explain design decisions
- [ ] Show iteration history
- [ ] Include example inputs/outputs
- [ ] Document function calling schemas

### 8.3 Create Seed Data
File: `data/seed_defects.json`
- [ ] Create 10 realistic defect scenarios
- [ ] Include expected classifications
- [ ] Cover range of severity levels
- [ ] Include edge cases (ambiguous, incomplete)

### 8.4 Add Setup Scripts
File: `scripts/setup_odoo_fields.py`
- [ ] Script to add custom fields to Odoo models:
  - `quality.alert.failure_mode`
  - `quality.alert.iso_code`
  - `quality.alert.ai_checklist`
  - `quality.alert.ai_confidence`
  - `quality.alert.processed_by_ai`
- [ ] Instructions for running script

### 8.5 Create Demo Video/Screenshots
Directory: `docs/screenshots/`
- [ ] Screenshot: Odoo before (unstructured text)
- [ ] Screenshot: Processing logs (AI classification)
- [ ] Screenshot: Odoo after (structured data)
- [ ] Screenshot: Auto-created maintenance ticket
- [ ] Optional: Screen recording of end-to-end flow

**Deliverable**: Complete documentation package

---

## Phase 9: Final Testing & Polish (Target: Day 13)

### 9.1 End-to-End Validation
- [ ] Fresh Odoo database setup
- [ ] Run seed defects through system
- [ ] Verify all data appears correctly in Odoo
- [ ] Check for errors in logs
- [ ] Verify metrics calculations

### 9.2 Code Quality
- [ ] Add docstrings to all functions
- [ ] Add type hints
- [ ] Run linter (pylint or black)
- [ ] Remove debug print statements
- [ ] Clean up commented code

### 9.3 Security Check
- [ ] Verify `.env` is in `.gitignore`
- [ ] Check no API keys in code
- [ ] Review error messages (don't leak sensitive info)
- [ ] Validate Odoo write operations (don't delete critical data)

### 9.4 Performance Testing
- [ ] Test with 50 defects in queue
- [ ] Measure memory usage
- [ ] Check for memory leaks
- [ ] Verify polling doesn't overload Odoo

### 9.5 Create Submission Package
- [ ] Push to GitHub
- [ ] Create release tag
- [ ] Verify all deliverables are present
- [ ] Test clone + setup on fresh machine
- [ ] Write submission summary

**Deliverable**: Production-ready, documented, tested system

---

## Assignment Deliverables Checklist

### Required Deliverables (Part A)

#### 1. Updated GitHub Repo
- [ ] Clear setup instructions in README.md
- [ ] requirements.txt with all dependencies
- [ ] .env.example with required variables
- [ ] Seed data in data/ directory
- [ ] Integration code in src/ directory
- [ ] Tests in tests/ directory
- [ ] All code runs without errors

#### 2. Metrics & Logs
- [ ] Baseline metrics CSV
- [ ] After-AI metrics CSV
- [ ] Side-by-side comparison table
- [ ] Screenshots showing before/after Odoo screens
- [ ] Processing logs demonstrating real-time classification

#### 3. Architecture Documentation
- [ ] Data flow diagram
- [ ] Tool/function calling patterns explained
- [ ] Odoo model mappings documented
- [ ] Prompt templates with explanations
- [ ] RAG pipeline architecture
- [ ] Edge case handling strategies

#### 4. Prompt + Iteration Log
- [ ] logs/prompt_iterations.jsonl with structured entries
- [ ] Each entry shows:
  - Prompt version
  - Test input
  - LLM response
  - Evaluation result
  - Changes from previous version
- [ ] Summary document explaining key improvements

---

## Quality Gates

Before moving to next phase, verify:

**Phase 1 → 2**
- [ ] Can authenticate to Odoo
- [ ] Can read quality alerts
- [ ] Can write test data
- [ ] No authentication errors

**Phase 2 → 3**
- [ ] Classifier achieves >80% accuracy on test set
- [ ] Prompt iterations are logged
- [ ] Function calling works reliably
- [ ] Confidence scores are meaningful

**Phase 3 → 4**
- [ ] RAG retrieves relevant documents
- [ ] Top-3 results are actually useful
- [ ] Vector store persists correctly
- [ ] Retrieval time < 2 seconds

**Phase 4 → 5**
- [ ] Checklists are actionable and specific
- [ ] Severity scoring makes sense
- [ ] Maintenance tickets are formatted correctly
- [ ] No generic/vague recommendations

**Phase 5 → 6**
- [ ] Ambiguous inputs are detected
- [ ] Hallucinations are caught
- [ ] Missing data triggers escalation
- [ ] System doesn't crash on bad input

**Phase 6 → 7**
- [ ] End-to-end flow works without manual intervention
- [ ] Data appears correctly in Odoo
- [ ] Polling loop is stable
- [ ] No data corruption

**Phase 7 → 8**
- [ ] Metrics show clear improvement
- [ ] Before/after comparison is compelling
- [ ] Numbers are accurate and verifiable
- [ ] Screenshots match the data

**Phase 8 → 9**
- [ ] README allows someone to set up from scratch
- [ ] All documentation is clear and complete
- [ ] Seed data runs successfully
- [ ] No "TODO" comments left in docs

---

## Risk Mitigation

### Common Pitfalls & Solutions

**Issue: Can't connect to Odoo**
- Check Docker container is running: `docker ps`
- Verify port mapping: `docker port <container>`
- Test in browser first before API
- Try localhost vs 127.0.0.1

**Issue: LLM accuracy too low**
- Add more examples (few-shot learning)
- Include more context (work center, product details)
- Use stricter function schemas (enums)
- Try different LLM (Claude vs GPT-4)

**Issue: RAG retrieves irrelevant docs**
- Improve query formulation
- Adjust chunk size (try 1000 chars)
- Use metadata filtering (doc type)
- Add more diverse documents to knowledge base

**Issue: System too slow**
- Cache Odoo queries (5-minute TTL)
- Batch process alerts (10 at a time)
- Use faster embeddings (smaller model)
- Consider local LLM for classification only

**Issue: Odoo write-back fails**
- Check field permissions in Odoo
- Verify field types match (e.g., Many2one needs ID)
- Add custom fields if needed (via Odoo Studio)
- Use safe_write wrapper with error handling

---

## Success Metrics

Your implementation is successful if:

- ✅ Reduces triage latency from 48-72 hours to < 5 minutes
- ✅ Achieves >80% classification accuracy
- ✅ Auto-creates maintenance tickets for high-severity defects
- ✅ Handles ambiguous inputs gracefully (no crashes)
- ✅ All code is documented and runs on fresh setup
- ✅ Metrics show measurable improvement
- ✅ Prompt iteration log shows systematic refinement

---

## Timeline Summary

| Phase | Days | Key Deliverable |
|-------|------|-----------------|
| 1. Odoo Connection | 1 | Working API client |
| 2. LLM Classification | 2 | 80%+ accurate classifier |
| 3. RAG System | 2 | Relevant doc retrieval |
| 4. Action Generation | 1 | Useful checklists |
| 5. Edge Cases | 1 | Robust error handling |
| 6. Integration | 2 | End-to-end flow working |
| 7. Metrics | 1 | Before/after comparison |
| 8. Documentation | 2 | Complete docs package |
| 9. Testing & Polish | 1 | Production-ready code |
| **Total** | **13 days** | **Submitted assignment** |

---

## Getting Started

Ready to begin? Start here:

1. Review the [Architecture Plan](./genai-integration-architecture.md)
2. Set up your development environment (Python, venv, API keys)
3. Test your Odoo connection with the connection test script
4. Begin Phase 1: Create project structure
5. Work through each phase, checking off items as you go
6. Update the todo list regularly to track progress

Good luck! 🚀
