"""Simple RAG system using in-memory documents (no vector DB for simplicity)."""
import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)


class SimpleRAG:
    """
    Simplified RAG system using keyword matching.
    For production, use ChromaDB/Pinecone with embeddings.
    """
    
    def __init__(self):
        """Initialize with basic knowledge base."""
        self.knowledge_base = self._load_knowledge_base()
        logger.info(f"Loaded {len(self.knowledge_base)} documents into knowledge base")
    
    def _load_knowledge_base(self) -> List[Dict[str, str]]:
        """
        Load maintenance knowledge base.
        In production, this would load from PDFs, CSVs, etc.
        
        Returns:
            List of documents with title and content
        """
        # Sample knowledge base for prototype
        return [
            {
                "id": "sop_tooling_001",
                "title": "Tool Insert Replacement Procedure",
                "content": """
                Tool Insert Replacement (T3 Series):
                - Inspection frequency: Every 500 cycles or when vibration detected
                - Replacement criteria: Edge chipping > 0.1mm, visible wear
                - Torque specification: Collet 45 Nm ±2 Nm
                - Common symptoms: Chatter, surface roughness > Ra 3.2
                - Estimated replacement time: 15-20 minutes
                - Required tools: Torque wrench (20-50 Nm), insert T3 replacement
                """,
                "tags": ["tooling", "vibration", "chatter", "insert", "T3", "surface"]
            },
            {
                "id": "sop_vibration_001",
                "title": "Vibration Troubleshooting Guide",
                "content": """
                Spindle Vibration Diagnosis:
                1. Check tool insert condition (most common cause)
                2. Verify collet torque (spec: 45 Nm ±2 Nm)
                3. Inspect spindle bearings for wear
                4. Check workpiece clamping
                5. Verify feed rate and RPM are within spec
                - Low frequency vibration (< 100 Hz): Usually tooling or clamping
                - High frequency (> 100 Hz): Likely bearing or spindle balance
                """,
                "tags": ["vibration", "spindle", "bearing", "tooling", "chatter"]
            },
            {
                "id": "repair_history_001",
                "title": "Repair Ticket #2024-045 - Assembly Line 1 Vibration",
                "content": """
                Date: 2024-03-15
                Issue: Excessive vibration at Step 2, surface defects on FURN_9666
                Root cause: Tool insert T3 chipped at 520 cycles (20 cycles over spec)
                Resolution: Replaced insert T3, verified torque 45 Nm, tested 10 units OK
                Downtime: 18 minutes
                Cost: $45 (insert) + $90 (labor)
                Prevention: Reduced inspection interval to 450 cycles
                """,
                "tags": ["vibration", "T3", "insert", "assembly", "line 1", "FURN"]
            },
            {
                "id": "iso_tw_001",
                "title": "ISO 14224: TW-001 Cutting Tool Degradation",
                "content": """
                Code: TW-001
                Category: Tooling Wear
                Description: Gradual or sudden degradation of cutting tool performance
                Symptoms: Vibration, chatter, poor surface finish, dimensional errors
                Typical causes:
                - Exceeding rated tool life cycles
                - Improper coolant or lubrication
                - Feed rate or RPM out of specification
                - Material hardness variation
                Immediate actions:
                - Inspect tool for visible damage
                - Measure critical dimensions
                - Replace if wear exceeds tolerance
                """,
                "tags": ["TW-001", "tooling", "ISO", "degradation", "wear"]
            },
            {
                "id": "sop_blockage_001",
                "title": "Production Line Blockage Response",
                "content": """
                Line Blockage Protocol:
                - Type: Process Deviation (PD-005)
                - Priority: Low (unless safety issue)
                - Response time: 10-15 minutes
                Actions:
                1. Verify upstream status in MES
                2. Check WIP buffer levels
                3. Notify production scheduler
                4. Log delay reason and duration
                - Do NOT create maintenance ticket for queue blockage
                - Focus on flow optimization, not equipment repair
                """,
                "tags": ["blockage", "queue", "PD-005", "process", "line", "upstream"]
            }
        ]
    
    def retrieve(self, query: str, top_k: int = 3) -> str:
        """
        Retrieve relevant documents using simple keyword matching.
        
        Args:
            query: Search query (typically failure mode + symptom + component)
            top_k: Number of top documents to return
        
        Returns:
            Concatenated content of top documents
        """
        query_lower = query.lower()
        query_terms = query_lower.split()
        
        # Score documents by keyword matches
        scored_docs = []
        for doc in self.knowledge_base:
            score = 0
            doc_text = (doc['title'] + " " + doc['content'] + " " + " ".join(doc['tags'])).lower()
            
            # Count keyword matches
            for term in query_terms:
                if term in doc_text:
                    score += doc_text.count(term)
            
            # Bonus for exact tag matches
            for tag in doc['tags']:
                if tag in query_terms:
                    score += 5
            
            if score > 0:
                scored_docs.append((score, doc))
        
        # Sort by score and take top k
        scored_docs.sort(key=lambda x: x[0], reverse=True)
        top_docs = [doc for score, doc in scored_docs[:top_k]]
        
        if not top_docs:
            logger.warning(f"No relevant documents found for query: {query}")
            return ""
        
        # Format retrieved context
        context = "\n\n---\n\n".join([
            f"**{doc['title']}**\n{doc['content']}"
            for doc in top_docs
        ])
        
        logger.info(f"Retrieved {len(top_docs)} documents for query: '{query[:50]}...'")
        return context
    
    def build_query(self, classification: Dict[str, Any]) -> str:
        """
        Build search query from classification result.
        
        Args:
            classification: Classification result
        
        Returns:
            Search query string
        """
        query_parts = [
            classification.get('failure_mode', ''),
            classification.get('symptom', ''),
            classification.get('component', ''),
            classification.get('iso_code', '')
        ]
        
        query = " ".join([part for part in query_parts if part])
        return query
