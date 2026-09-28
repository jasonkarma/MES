"""Edge case handling for ambiguous inputs, hallucinations, and errors."""
import logging
from typing import Dict, Any, List, Optional

from .llm.function_schemas import validate_iso_code

logger = logging.getLogger(__name__)


class EdgeCaseHandler:
    """Handles ambiguous inputs, hallucinations, missing data, and errors."""
    
    def __init__(self, confidence_threshold: float = 0.6):
        """
        Initialize edge case handler.
        
        Args:
            confidence_threshold: Minimum confidence for auto-processing (default 0.6)
        """
        self.confidence_threshold = confidence_threshold
    
    def check_classification(self, classification: Dict[str, Any]) -> Dict[str, Any]:
        """
        Check classification for edge cases and return handling decision.
        
        Args:
            classification: Classification result from DefectClassifier
        
        Returns:
            Dictionary with decision and any required actions
        """
        decision = {
            'action': 'proceed',  # proceed, escalate, request_clarification
            'reason': '',
            'issues': [],
            'suggestions': []
        }
        
        # Check for errors
        if classification.get('error'):
            decision['action'] = 'escalate'
            decision['reason'] = f"Classification error: {classification['error']}"
            decision['issues'].append('classification_error')
            logger.warning(f"Classification error detected: {classification['error']}")
            return decision
        
        # Check confidence threshold
        confidence = classification.get('confidence', 1.0)
        if confidence < self.confidence_threshold:
            decision['action'] = 'request_clarification'
            decision['reason'] = f'Low confidence: {confidence:.2f} < {self.confidence_threshold}'
            decision['issues'].append('low_confidence')
            decision['suggestions'] = self._generate_clarification_questions(classification)
            logger.warning(f"Low confidence classification: {confidence:.2f}")
        
        # Check ISO code validity
        iso_code = classification.get('iso_code', '')
        if not validate_iso_code(iso_code) and iso_code != 'UNCL-999':
            decision['issues'].append('invalid_iso_code')
            decision['reason'] += f' Invalid ISO code: {iso_code}.'
            logger.warning(f"Invalid ISO code detected: {iso_code}")
        
        # Check for missing critical fields
        missing_fields = self._check_completeness(classification)
        if missing_fields:
            decision['issues'].append('missing_fields')
            decision['reason'] += f' Missing fields: {", ".join(missing_fields)}.'
            logger.warning(f"Missing critical fields: {missing_fields}")
            
            # Escalate if too many fields missing
            if len(missing_fields) > 2:
                decision['action'] = 'escalate'
                decision['reason'] += ' Too many missing fields for auto-processing.'
        
        # Check for generic/vague classifications
        if self._is_too_generic(classification):
            decision['issues'].append('generic_classification')
            decision['reason'] += ' Classification is too generic.'
            logger.warning("Generic classification detected")
        
        return decision
    
    def _check_completeness(self, classification: Dict[str, Any]) -> List[str]:
        """
        Check for missing critical fields.
        
        Args:
            classification: Classification result
        
        Returns:
            List of missing field names
        """
        required_fields = ['failure_mode', 'symptom', 'severity']
        important_fields = ['component', 'location']
        
        missing = []
        
        # Check required fields
        for field in required_fields:
            if not classification.get(field) or classification.get(field) == 'Unknown':
                missing.append(field)
        
        # Check important fields
        for field in important_fields:
            if not classification.get(field) or classification.get(field) == 'Unknown':
                missing.append(field)
        
        return missing
    
    def _is_too_generic(self, classification: Dict[str, Any]) -> bool:
        """
        Check if classification is too generic to be useful.
        
        Args:
            classification: Classification result
        
        Returns:
            True if too generic
        """
        generic_terms = ['unknown', 'general', 'various', 'multiple', 'unspecified']
        
        component = classification.get('component', '').lower()
        location = classification.get('location', '').lower()
        
        # Check if component or location contains generic terms
        for term in generic_terms:
            if term in component or term in location:
                return True
        
        return False
    
    def _generate_clarification_questions(self, classification: Dict[str, Any]) -> List[str]:
        """
        Generate specific questions to clarify ambiguous classification.
        
        Args:
            classification: Classification result
        
        Returns:
            List of clarification questions
        """
        questions = []
        
        symptom = classification.get('symptom', '')
        component = classification.get('component', 'Unknown')
        location = classification.get('location', 'Unknown')
        
        # Question about symptom details
        if symptom == 'Vibration':
            questions.append("What type of vibration? (low frequency rumble, high frequency chatter, intermittent knocking)")
        elif symptom == 'Noise':
            questions.append("What type of noise? (clicking, grinding, whistling, squealing)")
        elif symptom == 'Surface Defect':
            questions.append("What type of surface defect? (scratches, roughness, discoloration, pitting)")
        
        # Question about location if missing
        if component == 'Unknown' or 'unknown' in component.lower():
            questions.append("Which specific component is affected? (tool, bearing, motor, sensor, etc.)")
        
        # Question about location if missing
        if location == 'Unknown' or 'unknown' in location.lower():
            questions.append("At which station or step did this occur? (Station number or process step)")
        
        # Question about timing
        questions.append("Did the issue start suddenly or gradually over time?")
        
        return questions[:3]  # Return top 3 questions
    
    def create_escalation_record(
        self,
        classification: Dict[str, Any],
        decision: Dict[str, Any],
        operator_text: str
    ) -> Dict[str, Any]:
        """
        Create record for human review escalation.
        
        Args:
            classification: Classification result
            decision: Edge case decision
            operator_text: Original operator input
        
        Returns:
            Escalation record
        """
        return {
            'escalation_type': 'human_review_required',
            'original_text': operator_text,
            'classification_attempt': classification,
            'issues': decision['issues'],
            'reason': decision['reason'],
            'suggested_questions': decision.get('suggestions', []),
            'timestamp': classification.get('metadata', {}).get('timestamp'),
            'priority': 'high' if 'classification_error' in decision['issues'] else 'normal'
        }
    
    def safe_write_wrapper(self, write_function, *args, **kwargs) -> bool:
        """
        Wrapper for safe Odoo writes with error handling.
        
        Args:
            write_function: Odoo write function to call
            *args, **kwargs: Arguments to pass to write function
        
        Returns:
            True if successful, False otherwise
        """
        try:
            result = write_function(*args, **kwargs)
            logger.info("✓ Odoo write successful")
            return result
        except Exception as e:
            logger.error(f"✗ Odoo write failed: {e}")
            logger.error(f"  Args: {args}, Kwargs: {kwargs}")
            # Log to file for investigation but don't crash
            return False
