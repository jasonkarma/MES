"""Action generation for diagnostic checklists and maintenance recommendations."""
import json
import logging
from datetime import datetime
from typing import Dict, Any, List
from openai import OpenAI

from .prompts import (
    ACTION_GENERATION_SYSTEM_PROMPT,
    ACTION_GENERATION_USER_PROMPT
)
from .function_schemas import ACTION_GENERATION_FUNCTION

logger = logging.getLogger(__name__)


class ActionGenerator:
    """Generates diagnostic checklists and maintenance recommendations."""
    
    def __init__(self, api_key: str, model: str = "gpt-4-turbo"):
        """
        Initialize action generator with OpenAI client.
        
        Args:
            api_key: OpenAI API key
            model: Model to use
        """
        self.client = OpenAI(api_key=api_key)
        self.model = model
        
    def generate_actions(
        self,
        classification: Dict[str, Any],
        context: Dict[str, Any],
        rag_context: str = ""
    ) -> Dict[str, Any]:
        """
        Generate diagnostic checklist and maintenance recommendation.
        
        Args:
            classification: Classification result from DefectClassifier
            context: Work order context
            rag_context: Retrieved documentation from RAG system
        
        Returns:
            Dictionary with checklist, severity score, and maintenance decision
        """
        start_time = datetime.now()
        
        # Format the user prompt
        user_prompt = ACTION_GENERATION_USER_PROMPT.format(
            failure_mode=classification.get('failure_mode', 'Unknown'),
            iso_code=classification.get('iso_code', 'Unknown'),
            component=classification.get('component', 'Unknown'),
            location=classification.get('location', 'Unknown'),
            symptom=classification.get('symptom', 'Unknown'),
            severity=classification.get('severity', 'Medium'),
            product_name=context.get('product_name', 'N/A'),
            workcenter_name=context.get('workcenter_name', 'N/A'),
            context_docs=rag_context or "No relevant documentation found"
        )
        
        logger.info(f"Generating actions for {classification['failure_mode']}...")
        
        try:
            # Call OpenAI with function calling
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": ACTION_GENERATION_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt}
                ],
                functions=[ACTION_GENERATION_FUNCTION],
                function_call={"name": "generate_diagnostic_checklist"},
                temperature=0.4,
                max_tokens=600
            )
            
            # Extract function call result
            function_call = response.choices[0].message.function_call
            actions = json.loads(function_call.arguments)
            
            # Add metadata
            processing_time = (datetime.now() - start_time).total_seconds()
            actions['metadata'] = {
                'model': self.model,
                'processing_time_seconds': processing_time,
                'timestamp': datetime.now().isoformat(),
                'tokens_used': response.usage.total_tokens,
                'rag_context_used': bool(rag_context)
            }
            
            logger.info(
                f"✓ Actions generated: {len(actions['checklist_steps'])} steps, "
                f"severity={actions['severity_score']}, "
                f"ticket_required={actions['requires_maintenance_ticket']}"
            )
            
            return actions
            
        except Exception as e:
            logger.error(f"✗ Action generation failed: {e}")
            # Return fallback actions
            return {
                'checklist_steps': [
                    "1. Document the exact issue with photos if possible",
                    "2. Check if the issue is intermittent or consistent",
                    "3. Contact maintenance supervisor for guidance"
                ],
                'severity_score': 50,
                'estimated_downtime_minutes': 30,
                'requires_maintenance_ticket': True,
                'maintenance_priority': 'normal',
                'safety_notes': '',
                'error': str(e),
                'metadata': {
                    'model': self.model,
                    'processing_time_seconds': (datetime.now() - start_time).total_seconds(),
                    'timestamp': datetime.now().isoformat(),
                    'rag_context_used': bool(rag_context)
                }
            }
    
    def format_checklist_for_odoo(self, actions: Dict[str, Any]) -> str:
        """
        Format checklist steps for display in Odoo.
        
        Args:
            actions: Action generation result
        
        Returns:
            Formatted checklist string
        """
        steps = actions.get('checklist_steps', [])
        formatted = "\n".join(steps)
        
        if actions.get('safety_notes'):
            formatted += f"\n\n⚠️ SAFETY: {actions['safety_notes']}"
        
        return formatted
