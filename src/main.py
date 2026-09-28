"""Main orchestrator for GenAI-powered defect triage system."""
import logging
import time
from datetime import datetime
from typing import Dict, Any, List, Optional

from .config import OPENAI_API_KEY
from .odoo_client import OdooClient
from .llm.classifier import DefectClassifier
from .llm.action_generator import ActionGenerator
from .rag.simple_rag import SimpleRAG
from .edge_cases import EdgeCaseHandler

logger = logging.getLogger(__name__)


class DefectTriageOrchestrator:
    """Main orchestrator that coordinates all components."""
    
    def __init__(self, odoo_client: OdooClient):
        """
        Initialize orchestrator with all components.
        
        Args:
            odoo_client: Connected Odoo client
        """
        self.odoo = odoo_client
        self.classifier = DefectClassifier(OPENAI_API_KEY)
        self.action_generator = ActionGenerator(OPENAI_API_KEY)
        self.rag = SimpleRAG()
        self.edge_handler = EdgeCaseHandler(confidence_threshold=0.6)
        
        logger.info("✓ DefectTriageOrchestrator initialized")
    
    def process_quality_alert(self, alert_id: int) -> Dict[str, Any]:
        """
        Process a single quality alert end-to-end.
        
        Args:
            alert_id: Odoo quality.alert record ID
        
        Returns:
            Processing result with classification, actions, and write status
        """
        start_time = datetime.now()
        logger.info(f"Processing quality alert ID: {alert_id}")
        
        result = {
            'alert_id': alert_id,
            'success': False,
            'classification': None,
            'actions': None,
            'edge_case_decision': None,
            'odoo_updated': False,
            'maintenance_ticket_created': False,
            'processing_time_seconds': 0,
            'errors': []
        }
        
        try:
            # 1. Read alert from Odoo
            alert = self._read_alert(alert_id)
            if not alert:
                result['errors'].append('Failed to read alert from Odoo')
                return result
            
            operator_text = alert.get('description', '')
            if not operator_text:
                result['errors'].append('No description found in alert')
                return result
            
            # 2. Get context from related records
            context = self._get_context(alert)
            
            # 3. Classify defect
            logger.info("Step 1/5: Classifying defect...")
            classification = self.classifier.classify_defect(operator_text, context)
            result['classification'] = classification
            
            # Log classification
            self.classifier.log_classification(classification)
            
            # 4. Check for edge cases
            logger.info("Step 2/5: Checking for edge cases...")
            edge_decision = self.edge_handler.check_classification(classification)
            result['edge_case_decision'] = edge_decision
            
            if edge_decision['action'] == 'escalate':
                logger.warning(f"Escalating to human: {edge_decision['reason']}")
                self._handle_escalation(alert_id, classification, edge_decision, operator_text)
                result['errors'].append(f"Escalated: {edge_decision['reason']}")
                return result
            
            # 5. Retrieve relevant documentation (RAG)
            logger.info("Step 3/5: Retrieving relevant documentation...")
            query = self.rag.build_query(classification)
            rag_context = self.rag.retrieve(query, top_k=3)
            
            # 6. Generate actions
            logger.info("Step 4/5: Generating diagnostic actions...")
            actions = self.action_generator.generate_actions(
                classification, context, rag_context
            )
            result['actions'] = actions
            
            # 7. Write back to Odoo
            logger.info("Step 5/5: Writing results to Odoo...")
            
            # Update quality alert with structured data
            alert_values = self._format_alert_update(classification, actions)
            write_success = self.odoo.safe_write('quality.alert', alert_id, alert_values)
            result['odoo_updated'] = write_success
            
            if not write_success:
                result['errors'].append('Failed to update quality alert in Odoo')
            
            # Create maintenance ticket if needed
            if actions.get('requires_maintenance_ticket', False):
                ticket_id = self._create_maintenance_ticket(alert, classification, actions)
                if ticket_id:
                    result['maintenance_ticket_created'] = True
                    result['maintenance_ticket_id'] = ticket_id
                    logger.info(f"✓ Created maintenance ticket ID: {ticket_id}")
                else:
                    result['errors'].append('Failed to create maintenance ticket')
            
            # Mark as success if no errors
            if not result['errors']:
                result['success'] = True
            
        except Exception as e:
            logger.error(f"✗ Error processing alert {alert_id}: {e}", exc_info=True)
            result['errors'].append(f'Exception: {str(e)}')
        
        finally:
            processing_time = (datetime.now() - start_time).total_seconds()
            result['processing_time_seconds'] = processing_time
            logger.info(f"Processing completed in {processing_time:.2f}s")
        
        return result
    
    def _read_alert(self, alert_id: int) -> Optional[Dict[str, Any]]:
        """Read quality alert from Odoo."""
        try:
            alerts = self.odoo.read(
                'quality.alert',
                [alert_id],
                ['name', 'title', 'description', 'product_id', 'team_id', 'create_date']
            )
            return alerts[0] if alerts else None
        except Exception as e:
            logger.error(f"Failed to read alert {alert_id}: {e}")
            return None
    
    def _get_context(self, alert: Dict[str, Any]) -> Dict[str, Any]:
        """Extract context from alert and related records."""
        context = {
            'work_order_ref': alert.get('name', 'N/A'),
            'product_name': alert.get('product_id', [False, 'Unknown'])[1] if alert.get('product_id') else 'Unknown',
            'workcenter_name': alert.get('team_id', [False, 'Unknown'])[1] if alert.get('team_id') else 'Unknown',
            'report_date': alert.get('create_date', datetime.now().isoformat())
        }
        return context
    
    def _format_alert_update(
        self,
        classification: Dict[str, Any],
        actions: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Format classification and actions for Odoo quality.alert update."""
        
        # Format checklist
        checklist_text = self.action_generator.format_checklist_for_odoo(actions)
        
        # Build note with AI analysis
        note = f"""
=== AI Triage Analysis ===
Timestamp: {datetime.now().isoformat()}

Classification:
- Failure Mode: {classification.get('failure_mode')}
- ISO Code: {classification.get('iso_code')} ({classification.get('iso_description', '')})
- Component: {classification.get('component')}
- Location: {classification.get('location')}
- Symptom: {classification.get('symptom')}
- Severity: {classification.get('severity')}
- Confidence: {classification.get('confidence', 0):.2f}

Reasoning: {classification.get('reasoning', 'N/A')}

Diagnostic Checklist:
{checklist_text}

Severity Score: {actions.get('severity_score')}/100
Estimated Downtime: {actions.get('estimated_downtime_minutes')} minutes
Maintenance Ticket: {'Created' if actions.get('requires_maintenance_ticket') else 'Not required'}
"""
        
        # Note: Odoo quality.alert has limited custom fields by default
        # We're using 'description' field to append our analysis
        # In production, you'd add custom fields via Odoo Studio
        return {
            'description': note  # Append to existing description
        }
    
    def _create_maintenance_ticket(
        self,
        alert: Dict[str, Any],
        classification: Dict[str, Any],
        actions: Dict[str, Any]
    ) -> Optional[int]:
        """Create maintenance request in Odoo."""
        try:
            # Map priority
            priority_map = {
                'low': '1',
                'normal': '2',
                'high': '3',
                'urgent': '3'
            }
            priority = priority_map.get(actions.get('maintenance_priority', 'normal'), '2')
            
            # Build description
            checklist = self.action_generator.format_checklist_for_odoo(actions)
            description = f"""
Auto-generated from Quality Alert: {alert.get('name')}

Failure: {classification.get('failure_mode')} ({classification.get('iso_code')})
Component: {classification.get('component')}
Location: {classification.get('location')}
Severity Score: {actions.get('severity_score')}/100

Diagnostic Checklist:
{checklist}

Estimated Time: {actions.get('estimated_downtime_minutes')} minutes
"""
            
            ticket_values = {
                'name': f"AI-Detected: {classification.get('failure_mode')} - {classification.get('component')}",
                'description': description,
                'priority': priority,
                'maintenance_type': 'corrective',
                # 'equipment_id': equipment_id,  # Would need to map from workcenter
            }
            
            ticket_id = self.odoo.create('maintenance.request', ticket_values)
            return ticket_id
            
        except Exception as e:
            logger.error(f"Failed to create maintenance ticket: {e}")
            return None
    
    def _handle_escalation(
        self,
        alert_id: int,
        classification: Dict[str, Any],
        decision: Dict[str, Any],
        operator_text: str
    ):
        """Handle escalation to human review."""
        escalation = self.edge_handler.create_escalation_record(
            classification, decision, operator_text
        )
        
        # Log escalation
        logger.warning(f"Escalation created for alert {alert_id}: {escalation}")
        
        # In production, this would:
        # - Create a task for QA engineer
        # - Send notification
        # - Add to review queue
        
        # For now, add note to alert
        try:
            note = f"""
=== AI ESCALATION - Human Review Required ===
Reason: {decision['reason']}
Issues: {', '.join(decision['issues'])}

Suggested clarifying questions:
{chr(10).join(f'- {q}' for q in decision.get('suggestions', []))}

Classification attempt: {classification.get('failure_mode')} (confidence: {classification.get('confidence', 0):.2f})
"""
            self.odoo.safe_write('quality.alert', alert_id, {'description': note})
        except Exception as e:
            logger.error(f"Failed to write escalation note: {e}")
    
    def poll_and_process(self, interval_seconds: int = 30, max_iterations: Optional[int] = None):
        """
        Continuously poll for new quality alerts and process them.
        
        Args:
            interval_seconds: Polling interval
            max_iterations: Maximum iterations (None = infinite)
        """
        iteration = 0
        logger.info(f"Starting polling loop (interval: {interval_seconds}s)...")
        
        while True:
            if max_iterations and iteration >= max_iterations:
                logger.info(f"Reached max iterations: {max_iterations}")
                break
            
            iteration += 1
            logger.info(f"\n=== Polling iteration {iteration} ===")
            
            try:
                # Find unprocessed alerts
                # Note: This requires a custom field 'x_ai_processed' in Odoo
                # For prototype, we'll just get recent alerts
                alerts = self.odoo.search_read(
                    'quality.alert',
                    domain=[('create_date', '>', '2026-09-01')],  # Recent alerts
                    fields=['id', 'name', 'create_date'],
                    limit=10
                )
                
                if alerts:
                    logger.info(f"Found {len(alerts)} alerts")
                    for alert in alerts:
                        logger.info(f"Processing alert: {alert['name']}")
                        result = self.process_quality_alert(alert['id'])
                        
                        if result['success']:
                            logger.info(f"✓ Successfully processed alert {alert['id']}")
                        else:
                            logger.warning(f"⚠ Processed with errors: {result['errors']}")
                else:
                    logger.info("No new alerts found")
                
            except Exception as e:
                logger.error(f"Error in polling loop: {e}", exc_info=True)
            
            # Sleep until next iteration
            logger.info(f"Sleeping for {interval_seconds}s...")
            time.sleep(interval_seconds)
