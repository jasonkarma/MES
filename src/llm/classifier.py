"""LLM-based defect classification engine."""
import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional
from openai import OpenAI

from .prompts import (
    CLASSIFICATION_SYSTEM_PROMPT,
    CLASSIFICATION_USER_PROMPT
)
from .function_schemas import (
    DEFECT_CLASSIFICATION_FUNCTION,
    validate_iso_code,
    get_iso_description
)

logger = logging.getLogger(__name__)


class DefectClassifier:
    """Classifies operator defect reports using LLM with function calling."""
    
    def __init__(self, api_key: str, model: str = "gpt-4-0125-preview"):
        """
        Initialize classifier with OpenAI client.
        
        Args:
            api_key: OpenAI API key
            model: Model to use (default: gpt-4-0125-preview for function calling)
        """
        self.client = OpenAI(api_key=api_key)
        self.model = model
        self.prompt_version = "1.0"
        
    def classify_defect(
        self,
        operator_text: str,
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Classify a defect report into structured ISO 14224 format.
        
        Args:
            operator_text: Free-text defect description from operator
            context: Additional context (work_order, product, workcenter, etc.)
        
        Returns:
            Dictionary with classification results and metadata
        """
        start_time = datetime.now()
        
        # Format the user prompt with context
        user_prompt = CLASSIFICATION_USER_PROMPT.format(
            operator_text=operator_text,
            work_order_ref=context.get('work_order_ref', 'N/A'),
            product_name=context.get('product_name', 'N/A'),
            workcenter_name=context.get('workcenter_name', 'N/A'),
            report_date=context.get('report_date', datetime.now().isoformat())
        )
        
        logger.info(f"Classifying defect: '{operator_text[:50]}...'")
        
        try:
            # Call OpenAI with function calling
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": CLASSIFICATION_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt}
                ],
                functions=[DEFECT_CLASSIFICATION_FUNCTION],
                function_call={"name": "classify_defect"},
                temperature=0.3,  # Lower temperature for more consistent classification
                max_tokens=500
            )
            
            # Extract function call result
            function_call = response.choices[0].message.function_call
            classification = json.loads(function_call.arguments)
            
            # Validate ISO code
            if not validate_iso_code(classification['iso_code']):
                logger.warning(f"Invalid ISO code generated: {classification['iso_code']}")
                classification['iso_code_valid'] = False
                classification['iso_code_original'] = classification['iso_code']
                classification['iso_code'] = 'UNCL-999'  # Unclassified fallback
            else:
                classification['iso_code_valid'] = True
                classification['iso_description'] = get_iso_description(classification['iso_code'])
            
            # Add metadata
            processing_time = (datetime.now() - start_time).total_seconds()
            classification['metadata'] = {
                'model': self.model,
                'prompt_version': self.prompt_version,
                'processing_time_seconds': processing_time,
                'timestamp': datetime.now().isoformat(),
                'tokens_used': response.usage.total_tokens,
                'operator_text': operator_text,
                'context': context
            }
            
            logger.info(
                f"✓ Classification complete: {classification['failure_mode']} "
                f"({classification['iso_code']}) confidence={classification['confidence']:.2f}"
            )
            
            return classification
            
        except Exception as e:
            logger.error(f"✗ Classification failed: {e}")
            # Return error classification
            return {
                'failure_mode': 'Classification Error',
                'iso_code': 'ERROR-000',
                'symptom': 'Unknown',
                'component': 'Unknown',
                'location': 'Unknown',
                'severity': 'Medium',
                'confidence': 0.0,
                'reasoning': f'Classification failed due to error: {str(e)}',
                'iso_code_valid': False,
                'error': str(e),
                'metadata': {
                    'model': self.model,
                    'prompt_version': self.prompt_version,
                    'processing_time_seconds': (datetime.now() - start_time).total_seconds(),
                    'timestamp': datetime.now().isoformat(),
                    'operator_text': operator_text,
                    'context': context
                }
            }
    
    def detect_ambiguity(self, classification: Dict[str, Any]) -> bool:
        """
        Detect if classification is ambiguous and needs clarification.
        
        Args:
            classification: Classification result
        
        Returns:
            True if ambiguous (confidence < 0.6)
        """
        return classification.get('confidence', 1.0) < 0.6
    
    def log_classification(self, classification: Dict[str, Any], log_file: str = 'logs/classifications.jsonl'):
        """
        Log classification to JSONL file for analysis.
        
        Args:
            classification: Classification result
            log_file: Path to log file
        """
        try:
            with open(log_file, 'a') as f:
                f.write(json.dumps(classification) + '\n')
            logger.debug(f"Logged classification to {log_file}")
        except Exception as e:
            logger.error(f"Failed to log classification: {e}")
