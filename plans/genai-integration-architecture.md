# GenAI Integration Architecture Plan for Odoo MES

## Executive Summary

This plan outlines the implementation of an intelligent defect classification and triage system that integrates GenAI capabilities into your existing Odoo MES Docker instance. The system will reduce triage latency from 72 hours to near real-time, improve classification accuracy, and automatically generate actionable maintenance recommendations.

---

## 1. System Overview

### Problem Being Solved
- **Current State**: Operators enter unstructured free-text defect descriptions ("shaking," "chatter," "vibrational squeal") causing 72-hour QA triage delays
- **Impact**: 5.0 hours machine queue blockage per incident, 4.6% scrap rate, $18,500 monthly losses
- **Solution**: Real-time LLM-powered semantic extraction, ISO 14224 classification, and automated maintenance ticket generation

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     Odoo MES (Docker)                            │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │ Manufacturing│  │   Quality    │  │ Maintenance  │          │
│  │    Orders    │  │    Alerts    │  │   Tickets    │          │
│  └──────┬───────┘  └──────┬───────┘  └──────▲───────┘          │
│         │                 │                  │                   │
│         └─────────────────┴──────────────────┘                   │
│                           │                                       │
│                    XMLRPC/REST API                               │
└───────────────────────────┼───────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│              Python GenAI Integration Layer                      │
│                                                                   │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  1. API Client (odoo-xmlrpc / requests)                  │   │
│  │     - Authenticate with Odoo                             │   │
│  │     - Poll/webhook for new defect reports                │   │
│  │     - Write back structured data                         │   │
│  └─────────────────────────────────────────────────────────┘   │
│                            │                                     │
│  ┌─────────────────────────▼─────────────────────────────────┐ │
│  │  2. Defect Classification Engine                          │ │
│  │     ┌──────────────────────────────────────────────────┐ │ │
│  │     │  LLM Prompt (OpenAI/Anthropic/Local)             │ │ │
│  │     │  - Parse unstructured text                       │ │ │
│  │     │  - Extract symptoms (vibration, chipping, etc.)  │ │ │
│  │     │  - Map to ISO 14224 failure codes                │ │ │
│  │     │  - Extract component/location (step 2, tool T3)  │ │ │
│  │     └──────────────────────────────────────────────────┘ │ │
│  └───────────────────────────────────────────────────────────┘ │
│                            │                                     │
│  ┌─────────────────────────▼─────────────────────────────────┐ │
│  │  3. RAG System (Vector Store + Retrieval)                 │ │
│  │     ┌──────────────┐  ┌──────────────┐                   │ │
│  │     │  Embeddings  │  │  ChromaDB /  │                   │ │
│  │     │   (OpenAI/   │→ │   Pinecone/  │                   │ │
│  │     │  Sentence-   │  │   FAISS      │                   │ │
│  │     │ Transformers)│  └──────────────┘                   │ │
│  │     └──────────────┘          │                           │ │
│  │                              │                             │ │
│  │     Knowledge Base:           │                           │ │
│  │     - Machine SOPs            ▼                           │ │
│  │     - Repair manuals      ┌────────────────┐             │ │
│  │     - Historical tickets  │ Semantic Search│             │ │
│  │     - ISO 14224 taxonomy  │  Top-k Results │             │ │
│  │                           └────────────────┘             │ │
│  └───────────────────────────────────────────────────────────┘ │
│                            │                                     │
│  ┌─────────────────────────▼─────────────────────────────────┐ │
│  │  4. Action Proposal Generator                             │ │
│  │     - Combine: failure code + machine context + RAG docs  │ │
│  │     - Generate 3-step diagnostic checklist               │ │
│  │     - Calculate severity score (0-100)                   │ │
│  │     - Decide: auto-resolve vs. create maintenance ticket │ │
│  └───────────────────────────────────────────────────────────┘ │
│                            │                                     │
│  ┌─────────────────────────▼─────────────────────────────────┐ │
│  │  5. Edge Case Handler                                     │ │
│  │     - Ambiguity detection (confidence < threshold)        │ │
│  │     - Hallucination guards (validate against taxonomy)    │ │
│  │     - Missing data recovery (prompt for specifics)        │ │
│  │     - Human escalation (QA engineer notification)         │ │
│  └───────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. Technical Stack

### Core Components

| Component | Technology Options | Recommendation |
|-----------|-------------------|----------------|
| **Odoo API Client** | `xmlrpc.client` (Python stdlib), `odoorpc`, `requests` | `odoorpc` - higher-level abstraction |
| **LLM Provider** | OpenAI GPT-4, Anthropic Claude, Ollama (local) | Start with OpenAI GPT-4 for function calling, add Ollama fallback |
| **Vector Store** | ChromaDB, FAISS, Pinecone, Weaviate | ChromaDB - lightweight, local-first |
| **Embeddings** | OpenAI `text-embedding-3-small`, Sentence-Transformers | OpenAI embeddings (consistency with LLM) |
| **Web Framework** | FastAPI, Flask | FastAPI - async support, webhook receiver |
| **Logging** | Python `logging` + JSON, LangSmith | Structured JSON logs + optional LangSmith |
| **Orchestration** | LangChain, direct API calls | LangChain for RAG pipeline, direct calls for classification |

### Project Structure

```
mes-genai-integration/
├── src/
│   ├── __init__.py
│   ├── config.py                 # Environment variables, Odoo credentials
│   ├── odoo_client.py            # Odoo API wrapper
│   ├── llm/
│   │   ├── __init__.py
│   │   ├── classifier.py         # Defect classification logic
│   │   ├── action_generator.py  # Diagnostic checklist generation
│   │   ├── prompts.py            # Prompt templates
│   │   └── function_schemas.py  # OpenAI function calling schemas
│   ├── rag/
│   │   ├── __init__.py
│   │   ├── vector_store.py       # ChromaDB setup
│   │   ├── embeddings.py         # Document embedding pipeline
│   │   └── retriever.py          # Semantic search
│   ├── edge_cases.py             # Ambiguity, hallucination, error handling
│   ├── metrics.py                # Performance tracking
│   └── main.py                   # Main orchestration loop
├── data/
│   ├── knowledge_base/
│   │   ├── sop_machine_01.pdf
│   │   ├── iso_14224_taxonomy.json
│   │   └── repair_history.csv
│   └── seed_defects.json         # Test scenarios for baseline
├── tests/
│   ├── test_odoo_client.py
│   ├── test_classifier.py
│   └── test_rag.py
├── logs/
│   └── prompt_iterations.jsonl   # Structured prompt evolution log
├── plans/
│   └── genai-integration-architecture.md  # This document
├── docker-compose.yml            # Optional: Run integration layer in container
├── requirements.txt
├── .env.example
└── README.md
```

---

## 3. Connecting to Your Odoo Docker Instance

### Step 1: Get Odoo Connection Details

Your Odoo is running in Docker. You need:

```bash
# Check running containers
docker ps | grep odoo

# Get Odoo port mapping (usually 8069)
docker port <odoo-container-name>

# Get database name
docker exec -it <odoo-container-name> psql -U odoo -l
```

Typical connection details:
- **URL**: `http://localhost:8069`
- **Database**: `odoo` (or your custom name)
- **Username**: Admin user (usually `admin`)
- **Password**: Set during initial Odoo setup

### Step 2: Test API Connection

Create `test_connection.py`:

```python
import xmlrpc.client

url = "http://localhost:8069"
db = "odoo"
username = "admin"
password = "your_password"

# Authenticate
common = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/common')
uid = common.authenticate(db, username, password, {})
print(f"✓ Authenticated as user ID: {uid}")

# Test API access
models = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/object')

# Read manufacturing orders
mo_ids = models.execute_kw(
    db, uid, password,
    'mrp.production', 'search',
    [[('state', '=', 'progress')]]
)
print(f"✓ Found {len(mo_ids)} in-progress manufacturing orders")

# Read quality checks/alerts
qa_ids = models.execute_kw(
    db, uid, password,
    'quality.check', 'search',
    [[]]
)
print(f"✓ Found {len(qa_ids)} quality checks in system")
```

### Step 3: Identify Relevant Odoo Models

Based on your screenshots, you'll interact with:

| Odoo Model | Purpose | Key Fields |
|------------|---------|-----------|
| `mrp.production` | Manufacturing orders | `name`, `product_id`, `state`, `workcenter_id` |
| `mrp.workorder` | Work center operations | `name`, `workcenter_id`, `state`, `duration_expected` |
| `quality.check` | Quality inspections | `name`, `failure_message`, `team_id`, `control_date` |
| `quality.alert` | Defect reports | `name`, `title`, `description`, `priority`, `product_id` |
| `maintenance.request` | Maintenance tickets | `name`, `equipment_id`, `maintenance_type`, `description` |

**Critical**: Your free-text defect input likely appears in:
- `quality.alert.description` (main text field)
- `quality.check.failure_message` (if using quality control points)
- `mrp.workorder` notes or custom fields

---

## 4. Core Integration Flow

### Scenario: Operator Reports Defect

```
[Operator Action]
   │
   ├─ Enters: "Tooling chatter on step 2, insert T3 chipped"
   │  into Odoo Quality Alert or Work Order note
   │
   ▼
[1. Detection - Python Integration Layer]
   │
   ├─ Poll Odoo API every 30s OR listen to webhook
   ├─ Query: quality.alert WHERE create_date > last_check
   │          AND processed_by_ai = False
   │
   ▼
[2. LLM Classification]
   │
   ├─ Prompt:
   │  """
   │  Extract structured failure data from operator description.
   │  
   │  Operator input: "Tooling chatter on step 2, insert T3 chipped"
   │  Work order: WH/MO/00002 (FURN_9666 Table)
   │  Work center: Assembly Line 1
   │  
   │  Extract:
   │  1. ISO 14224 failure mode (choose from: Mechanical Failure,
   │     Tooling Wear, Electrical Fault, Process Deviation)
   │  2. Symptom category (Vibration, Surface Defect, Dimensional,
   │     Assembly Error)
   │  3. Affected component (e.g., Tool Insert T3)
   │  4. Location (e.g., Step 2, Station 02)
   │  5. Severity (Low/Medium/High/Critical)
   │  """
   │
   ├─ LLM Response (OpenAI function calling):
   │  {
   │    "failure_mode": "Tooling Wear",
   │    "iso_code": "TW-001",
   │    "symptom": "Vibration",
   │    "component": "Tool Insert T3",
   │    "location": "Step 2",
   │    "severity": "High"
   │  }
   │
   ▼
[3. RAG Retrieval]
   │
   ├─ Query vector store:
   │  "Tool insert chipping vibration assembly line"
   │
   ├─ Retrieved docs (top 3):
   │  1. SOP_Assembly_Line_Maintenance.pdf p.12
   │     "T3 inserts rated for 500 cycles, check edge wear..."
   │  2. Repair_Ticket_2024-045: Similar vibration issue
   │     resolved by torque adjustment to 45 Nm
   │  3. ISO_14224_Reference: TW-001 = Cutting tool degradation
   │
   ▼
[4. Action Generation]
   │
   ├─ Prompt with context:
   │  """
   │  Given:
   │  - Failure: Tooling Wear (TW-001)
   │  - Symptom: Vibration at Step 2
   │  - Component: Tool Insert T3
   │  - Historical fix: torque adjustment to 45 Nm
   │  - SOP: T3 rated for 500 cycles
   │  
   │  Generate 3-step diagnostic checklist for operator tablet.
   │  """
   │
   ├─ LLM Response:
   │  {
   │    "checklist": [
   │      "1. Inspect Tool Insert T3 for edge chipping or wear",
   │      "2. Verify collet torque is set to 45 Nm (±2 Nm)",
   │      "3. Check insert cycle count in maintenance log"
   │    ],
   │    "severity_score": 85,
   │    "auto_action": "create_maintenance_ticket",
   │    "estimated_downtime": "15-30 minutes"
   │  }
   │
   ▼
[5. Write Back to Odoo]
   │
   ├─ Update quality.alert:
   │  - failure_mode = "Tooling Wear"
   │  - iso_code = "TW-001"
   │  - ai_checklist = "1. Inspect Tool Insert T3..."
   │  - processed_by_ai = True
   │  - ai_confidence = 0.92
   │
   ├─ Create maintenance.request:
   │  - name = "Urgent: Tool Insert T3 Replacement (Auto-Generated)"
   │  - equipment_id = "Assembly Line 1"
   │  - description = "AI detected tooling wear..."
   │  - priority = "3" (Urgent)
   │
   ├─ Update mrp.workorder:
   │  - Add note: "AI triage completed at 2026-09-28 02:15 UTC"
   │
   ▼
[6. Operator Sees Results]
   │
   └─ Tablet displays diagnostic checklist in real-time
      Maintenance team receives ticket immediately
      Work order shows structured failure data
```

### Latency Improvement

| Phase | Before (Manual) | After (AI) |
|-------|----------------|------------|
| Defect entry | 0 min | 0 min |
| QA batch wait | 48-72 hours | 0 min |
| Manual classification | 3-5 min | 5-10 sec (LLM call) |
| Maintenance ticket creation | 10-15 min | Automatic |
| **Total to action** | **~50-75 hours** | **~30 seconds** |

---

## 5. Prompt Engineering Strategy

### Classification Prompt (Version 1)

```python
CLASSIFICATION_PROMPT_V1 = """
You are an expert manufacturing quality engineer. Extract structured failure data from operator defect reports.

Input:
- Operator description: {operator_text}
- Work order: {work_order_ref}
- Product: {product_name}
- Work center: {workcenter_name}

Extract the following using ISO 14224 taxonomy:
1. Failure mode (Mechanical, Tooling, Electrical, Process)
2. Symptom category (Vibration, Surface Defect, Dimensional, Assembly Error)
3. Affected component
4. Location/step number
5. Severity (Low/Medium/High/Critical)

Return JSON format.
"""
```

### Prompt Iteration Log Structure

```json
{
  "version": "1.2",
  "timestamp": "2026-09-28T02:15:00Z",
  "prompt": "...",
  "test_case": "Tooling chatter on step 2",
  "llm_response": {...},
  "evaluation": {
    "correct_classification": true,
    "hallucination": false,
    "notes": "Added work center context improved accuracy"
  },
  "changes_from_previous": "Added product name to context"
}
```

### Function Calling Schema (OpenAI)

```python
DEFECT_CLASSIFICATION_FUNCTION = {
    "name": "classify_defect",
    "description": "Extract structured failure data from operator input",
    "parameters": {
        "type": "object",
        "properties": {
            "failure_mode": {
                "type": "string",
                "enum": ["Tooling Wear", "Mechanical Failure", "Electrical Fault", "Process Deviation"]
            },
            "iso_code": {
                "type": "string",
                "description": "ISO 14224 failure code (e.g., TW-001)"
            },
            "symptom": {
                "type": "string",
                "enum": ["Vibration", "Surface Defect", "Dimensional Error", "Assembly Error"]
            },
            "component": {
                "type": "string",
                "description": "Specific part or tool (e.g., Tool Insert T3)"
            },
            "location": {
                "type": "string",
                "description": "Step number or station (e.g., Step 2)"
            },
            "severity": {
                "type": "string",
                "enum": ["Low", "Medium", "High", "Critical"]
            },
            "confidence": {
                "type": "number",
                "description": "Classification confidence 0-1"
            }
        },
        "required": ["failure_mode", "symptom", "severity", "confidence"]
    }
}
```

---

## 6. RAG Implementation

### Knowledge Base Setup

```python
from langchain.document_loaders import PyPDFLoader, CSVLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.embeddings import OpenAIEmbeddings
from langchain.vectorstores import Chroma

# 1. Load documents
sop_loader = PyPDFLoader("data/knowledge_base/sop_machine_01.pdf")
sop_docs = sop_loader.load()

repair_loader = CSVLoader("data/knowledge_base/repair_history.csv")
repair_docs = repair_loader.load()

# 2. Split into chunks
text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=500,
    chunk_overlap=50
)
chunks = text_splitter.split_documents(sop_docs + repair_docs)

# 3. Create embeddings and store
embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
vectorstore = Chroma.from_documents(
    documents=chunks,
    embedding=embeddings,
    persist_directory="./data/chroma_db"
)

# 4. Semantic search
def retrieve_context(failure_mode, symptom, component):
    query = f"{failure_mode} {symptom} {component}"
    results = vectorstore.similarity_search(query, k=3)
    return results
```

### Context Injection Pattern

```python
# After classification
classified_data = classify_defect(operator_text)

# Retrieve relevant docs
context_docs = retrieve_context(
    classified_data["failure_mode"],
    classified_data["symptom"],
    classified_data["component"]
)

# Build enriched prompt for action generation
enriched_prompt = f"""
Failure: {classified_data["failure_mode"]}
Component: {classified_data["component"]}

Relevant documentation:
{context_docs[0].page_content}
{context_docs[1].page_content}

Generate 3-step diagnostic checklist...
"""
```

---

## 7. Edge Case Handling

### 1. Ambiguous Input

**Example**: "machine making noise"

```python
def detect_ambiguity(classification_result):
    if classification_result["confidence"] < 0.6:
        # Prompt operator for clarification
        clarification_questions = [
            "What type of noise? (clicking, grinding, whistling)",
            "Which station or step?",
            "When did it start?"
        ]
        # Send back to operator OR escalate to QA
        return {
            "action": "request_clarification",
            "questions": clarification_questions
        }
    return {"action": "proceed"}
```

### 2. Hallucination Detection

**Example**: LLM invents a non-existent ISO code

```python
VALID_ISO_CODES = ["TW-001", "MF-002", "EF-003", ...] # Load from taxonomy

def validate_classification(result):
    if result["iso_code"] not in VALID_ISO_CODES:
        # Fallback to generic code or flag for human review
        return {
            "action": "escalate",
            "reason": "Invalid ISO code generated",
            "fallback_code": "UNCL-999"
        }
    return {"action": "proceed"}
```

### 3. Missing Critical Data

**Example**: No component mentioned

```python
def check_completeness(classification):
    required_fields = ["failure_mode", "symptom", "component", "location"]
    missing = [f for f in required_fields if not classification.get(f)]
    
    if missing:
        # Use RAG to infer OR escalate
        if "component" in missing and classification["location"]:
            # Try to infer from work order BOM
            component = infer_from_bom(work_order, classification["location"])
        else:
            return {"action": "escalate", "missing_fields": missing}
    return {"action": "proceed"}
```

### 4. System Error Recovery

```python
def safe_odoo_write(model, record_id, values):
    try:
        models.execute_kw(db, uid, password, model, 'write', [[record_id], values])
        log_success(model, record_id, values)
    except Exception as e:
        # Don't block on write failure - log and notify
        log_error(f"Failed to write to Odoo: {e}")
        send_alert_to_admin(model, record_id, values, error=e)
        # Continue processing other records
```

---

## 8. Metrics Collection

### Baseline Metrics (HW1 - Manual Process)

Collect these from existing Odoo data:

```python
# 1. Average triage latency
SELECT 
    AVG(EXTRACT(EPOCH FROM (qa_review_date - create_date)) / 3600) as avg_hours
FROM quality_alert
WHERE qa_review_date IS NOT NULL
-- Expected: 48-72 hours

# 2. Classification accuracy (if you have ground truth)
# Manual: QA engineer re-classifies a sample of 20 defects
# Compare their classification vs. original categorization

# 3. Scrap rate
SELECT 
    COUNT(*) FILTER (WHERE state = 'scrap') * 100.0 / COUNT(*)
FROM mrp_production
WHERE create_date > '2026-08-01'
-- Baseline: 4.6%

# 4. Time to maintenance ticket creation
SELECT AVG(time_to_ticket_minutes) FROM (
    SELECT 
        EXTRACT(EPOCH FROM (m.create_date - q.create_date)) / 60 as time_to_ticket_minutes
    FROM quality_alert q
    JOIN maintenance_request m ON m.origin = q.name
)
-- Expected: several hours to days
```

### After-AI Metrics

Same queries but filter for `processed_by_ai = True`:

```python
metrics = {
    "triage_latency_seconds": [],  # Should be < 60 seconds
    "classification_confidence": [],  # Track LLM confidence scores
    "auto_ticket_rate": 0,  # % of defects that auto-created tickets
    "escalation_rate": 0,  # % that needed human review
    "operator_satisfaction": 0,  # Survey: did checklist help?
}
```

### Side-by-Side Comparison Table

| Metric | Before (Manual) | After (AI) | Improvement |
|--------|----------------|------------|-------------|
| Avg triage latency | 68 hours | 25 seconds | 99.99% ↓ |
| Classification time | 3-5 minutes | 8 seconds | 97% ↓ |
| Scrap rate | 4.6% | TBD | Target: < 2% |
| Maintenance ticket creation | Manual (1-2 days) | Automatic (< 1 min) | N/A |
| Queue blockage time | 5.0 hours per incident | TBD | Target: < 30 min |
| Classification accuracy | 60% (baseline) | TBD | Target: > 85% |

---

## 9. Implementation Phases

### Phase 1: Foundation (Days 1-2)
- Set up Python project structure
- Connect to Odoo API and verify read/write access
- Create test script that reads quality alerts and work orders
- Set up logging infrastructure

### Phase 2: LLM Classification (Days 3-4)
- Implement basic classification prompt
- Test with sample defect descriptions
- Iterate on prompt based on accuracy
- Add function calling for structured output
- Log all prompt versions and results

### Phase 3: RAG System (Days 5-6)
- Collect/create knowledge base documents (SOPs, ISO taxonomy, sample repair tickets)
- Set up ChromaDB and embed documents
- Implement retrieval pipeline
- Test context quality with sample queries

### Phase 4: Action Generation (Day 7)
- Build diagnostic checklist generator
- Implement severity scoring
- Add maintenance ticket auto-creation logic
- Test end-to-end flow

### Phase 5: Edge Cases (Day 8)
- Implement ambiguity detection
- Add hallucination guards
- Create escalation paths
- Test with intentionally bad inputs

### Phase 6: Integration & Testing (Days 9-10)
- Connect all components
- Run baseline scenarios from HW1
- Collect before/after metrics
- Document any issues or limitations

### Phase 7: Documentation (Days 11-12)
- Create setup instructions
- Document architecture and data flow
- Compile prompt iteration logs
- Generate metrics comparison tables
- Write final report

---

## 10. Deliverables Checklist

### Part A Deliverables

- [ ] **Updated GitHub Repo**
  - [ ] `README.md` with setup instructions
  - [ ] `requirements.txt` with all dependencies
  - [ ] `docker-compose.yml` (optional) for integration layer
  - [ ] `.env.example` with required environment variables
  - [ ] Seed data (`data/seed_defects.json`)
  - [ ] Source code in `src/` directory

- [ ] **Side-by-Side Metrics**
  - [ ] Baseline metrics CSV/table from Odoo
  - [ ] After-AI metrics CSV/table
  - [ ] Comparison analysis
  - [ ] Screenshots of Odoo before/after
  - [ ] Logs showing AI processing times

- [ ] **Architecture Documentation**
  - [ ] This document (data flow, components, decisions)
  - [ ] Mermaid/diagram of system architecture
  - [ ] Odoo model mappings
  - [ ] Prompt templates with explanations

- [ ] **Prompt + Iteration Log**
  - [ ] `logs/prompt_iterations.jsonl` with structured entries
  - [ ] Summary of key prompt improvements
  - [ ] Test cases and LLM responses
  - [ ] Accuracy evaluation per version

---

## 11. Key Technical Decisions

### LLM Provider Selection

**Recommended: OpenAI GPT-4**
- Pros: Excellent function calling, fast, reliable
- Cons: Cost (~$0.03 per classification with RAG context)
- Alternative: Anthropic Claude (better at long context, similar cost)
- Local option: Ollama with Llama 3 (free, slower, needs GPU)

### Vector Store Selection

**Recommended: ChromaDB**
- Pros: Lightweight, local-first, Python-native
- Cons: Not production-scale (for that, use Pinecone/Weaviate)
- Note: For HW2, local is fine; can swap later

### Odoo Integration Pattern

**Recommended: Polling + Immediate Write-back**
- Poll quality alerts every 30 seconds
- Process new defects immediately
- Write back structured data + create tickets
- Alternative: Webhook (requires Odoo config or custom module)

### Edge Case Philosophy

**Confidence Threshold Approach**
- confidence ≥ 0.85 → Auto-process
- 0.60 ≤ confidence < 0.85 → Auto-process but flag for review
- confidence < 0.60 → Escalate to human

---

## 12. Sample Test Scenarios

Use these scenarios from your HW1 baseline:

### Scenario 1: Clear Tooling Failure
```json
{
  "work_order": "WH/MO/00002",
  "product": "FURN_9666 Table",
  "operator_input": "Tooling chatter on step 2, tool insert T3 chipped, surface roughness high",
  "expected_classification": {
    "failure_mode": "Tooling Wear",
    "iso_code": "TW-001",
    "component": "Tool Insert T3",
    "location": "Step 2",
    "severity": "High"
  },
  "expected_action": "Create maintenance ticket, provide checklist"
}
```

### Scenario 2: Ambiguous Vibration
```json
{
  "work_order": "WH/MO/00001",
  "product": "FURN_8522 Table Top",
  "operator_input": "Station 02: Spindle vibrational chatter on step 2",
  "expected_classification": {
    "failure_mode": "Mechanical Failure",
    "severity": "Medium",
    "ambiguity": "Low confidence - missing component detail"
  },
  "expected_action": "Request clarification: which spindle component?"
}
```

### Scenario 3: Blocked Line (Process Issue)
```json
{
  "work_order": "WH/MO/00003",
  "product": "FURN_7800 Desk Combo",
  "operator_input": "Blocked by upstream line occupancy",
  "expected_classification": {
    "failure_mode": "Process Deviation",
    "iso_code": "PD-005",
    "severity": "Low"
  },
  "expected_action": "No maintenance ticket needed, log for planning review"
}
```

---

## 13. Next Steps

### Questions to Resolve Before Coding

1. **LLM Provider**: Do you have API keys for OpenAI/Anthropic, or should we use local Ollama?
2. **Knowledge Base**: Do you have actual machine SOPs/manuals, or should we create synthetic ones?
3. **Baseline Data**: Can you export a CSV of quality alerts from your Odoo instance for metrics?
4. **Deployment**: Will this run on your laptop, or do you need Docker deployment?

### Recommended Workflow

1. **Review this plan** - confirm architecture makes sense
2. **Set up environment** - Python 3.10+, install dependencies
3. **Test Odoo connection** - run the connection test script
4. **Start with Phase 1** - build basic API client
5. **Iterate** - implement one phase at a time, test thoroughly
6. **Collect metrics** - compare before/after throughout

---

## 14. Success Criteria

Your implementation will be successful if:

- ✅ Reads defect reports from Odoo API automatically
- ✅ Classifies unstructured text into ISO 14224 codes with >80% accuracy
- ✅ Reduces triage latency from 72 hours to < 5 minutes
- ✅ Auto-generates maintenance tickets for high-severity issues
- ✅ Handles at least one realistic edge case (ambiguity, missing data, etc.)
- ✅ Writes structured data back to Odoo
- ✅ Provides quantitative before/after metrics
- ✅ Includes structured prompt iteration logs
- ✅ Has clear setup instructions in README

---

## Contact & Support Resources

**Odoo API Documentation**: https://www.odoo.com/documentation/16.0/developer/reference/external_api.html
**OpenAI Function Calling**: https://platform.openai.com/docs/guides/function-calling
**LangChain RAG Tutorial**: https://python.langchain.com/docs/use_cases/question_answering/
**ISO 14224 Reference**: (Industry standard for failure classification)

---

*Document Version: 1.0*  
*Created: 2026-09-28*  
*Next Review: After Phase 1 completion*
