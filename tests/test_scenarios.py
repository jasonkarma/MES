"""Run a single test scenario through the complete system."""
import sys
import json
import logging
from pathlib import Path
from datetime import datetime

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import ODOO_URL, ODOO_DB, ODOO_USERNAME, ODOO_PASSWORD
from src.odoo_client import OdooClient
from src.main import DefectTriageOrchestrator

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def load_test_scenarios():
    """Load test scenarios from seed data."""
    with open('data/seed_defects.json', 'r') as f:
        data = json.load(f)
    return data['test_scenarios']


def run_single_scenario(orchestrator: DefectTriageOrchestrator, scenario: dict):
    """
    Run a single test scenario.
    
    Args:
        orchestrator: DefectTriageOrchestrator instance
        scenario: Test scenario dictionary
    """
    print("\n" + "="*70)
    print(f"TEST SCENARIO: {scenario['name']}")
    print("="*70)
    print(f"\nOperator Input: \"{scenario['operator_input']}\"")
    print(f"Context: {scenario['context']}")
    
    # Create test quality alert in Odoo
    try:
        alert_id = orchestrator.odoo.create('quality.alert', {
            'title': f"TEST: {scenario['name']}",
            'description': scenario['operator_input']
        })
        print(f"\n✓ Created test alert ID: {alert_id}")
        
        # Process the alert
        print("\nProcessing...")
        result = orchestrator.process_quality_alert(alert_id)
        
        # Display results
        print("\n" + "-"*70)
        print("RESULTS:")
        print("-"*70)
        
        if result['success']:
            print("✓ Processing: SUCCESS")
        else:
            print(f"⚠ Processing: COMPLETED WITH ERRORS")
            print(f"  Errors: {result['errors']}")
        
        print(f"\nProcessing Time: {result['processing_time_seconds']:.2f}s")
        
        if result['classification']:
            cls = result['classification']
            print(f"\nClassification:")
            print(f"  Failure Mode: {cls.get('failure_mode')}")
            print(f"  ISO Code: {cls.get('iso_code')}")
            print(f"  Symptom: {cls.get('symptom')}")
            print(f"  Component: {cls.get('component')}")
            print(f"  Location: {cls.get('location')}")
            print(f"  Severity: {cls.get('severity')}")
            print(f"  Confidence: {cls.get('confidence', 0):.2f}")
            print(f"  Reasoning: {cls.get('reasoning', 'N/A')[:100]}...")
        
        if result['actions']:
            act = result['actions']
            print(f"\nActions:")
            print(f"  Severity Score: {act.get('severity_score')}/100")
            print(f"  Estimated Downtime: {act.get('estimated_downtime_minutes')} min")
            print(f"  Maintenance Ticket: {'YES' if act.get('requires_maintenance_ticket') else 'NO'}")
            print(f"  Priority: {act.get('maintenance_priority', 'N/A')}")
            print(f"\n  Checklist:")
            for step in act.get('checklist_steps', []):
                print(f"    {step}")
        
        if result['edge_case_decision']:
            edge = result['edge_case_decision']
            if edge['action'] != 'proceed':
                print(f"\nEdge Case Detected:")
                print(f"  Action: {edge['action']}")
                print(f"  Reason: {edge['reason']}")
                print(f"  Issues: {edge.get('issues', [])}")
        
        print(f"\nOdoo Updated: {'YES' if result['odoo_updated'] else 'NO'}")
        print(f"Maintenance Ticket Created: {'YES' if result['maintenance_ticket_created'] else 'NO'}")
        
        # Compare to expected
        print("\n" + "-"*70)
        print("COMPARISON TO EXPECTED:")
        print("-"*70)
        
        expected_cls = scenario.get('expected_classification', {})
        if result['classification']:
            cls = result['classification']
            
            # Check failure mode
            if cls.get('failure_mode') == expected_cls.get('failure_mode'):
                print("✓ Failure mode matches")
            else:
                print(f"✗ Failure mode: got '{cls.get('failure_mode')}', expected '{expected_cls.get('failure_mode')}'")
            
            # Check confidence
            conf = cls.get('confidence', 0)
            conf_min = expected_cls.get('confidence_min', 0)
            conf_max = expected_cls.get('confidence_max', 1)
            if conf_min <= conf <= conf_max:
                print(f"✓ Confidence in range: {conf:.2f}")
            else:
                print(f"✗ Confidence {conf:.2f} outside range [{conf_min}, {conf_max}]")
        
        # Baseline comparison
        baseline = scenario.get('baseline_metrics', {})
        if baseline:
            print(f"\nBaseline Metrics:")
            print(f"  Manual classification time: {baseline.get('manual_classification_time_minutes', 'N/A')} min")
            print(f"  → AI classification time: {result['processing_time_seconds']/60:.2f} min")
            print(f"  Triage latency: {baseline.get('triage_latency_hours', 'N/A')} hours")
            print(f"  → AI triage latency: {result['processing_time_seconds']/3600:.4f} hours")
        
        # Clean up - delete test alert
        try:
            orchestrator.odoo.models.execute_kw(
                orchestrator.odoo.db,
                orchestrator.odoo.uid,
                orchestrator.odoo.password,
                'quality.alert',
                'unlink',
                [[alert_id]]
            )
            print(f"\n✓ Cleaned up test alert {alert_id}")
        except:
            print(f"\n⚠ Could not delete test alert {alert_id} - delete manually")
        
        return result
        
    except Exception as e:
        logger.error(f"✗ Scenario failed: {e}", exc_info=True)
        return None


def main():
    """Run all test scenarios."""
    print("\n" + "="*70)
    print("GENAI MES INTEGRATION - TEST SCENARIOS")
    print("="*70)
    
    # Initialize
    print("\nInitializing...")
    client = OdooClient(ODOO_URL, ODOO_DB, ODOO_USERNAME, ODOO_PASSWORD)
    orchestrator = DefectTriageOrchestrator(client)
    
    # Load scenarios
    scenarios = load_test_scenarios()
    print(f"Loaded {len(scenarios)} test scenarios")
    
    # Run each scenario
    results = []
    for i, scenario in enumerate(scenarios, 1):
        print(f"\n{'#'*70}")
        print(f"# SCENARIO {i}/{len(scenarios)}")
        print(f"{'#'*70}")
        result = run_single_scenario(orchestrator, scenario)
        if result:
            results.append({
                'scenario': scenario['name'],
                'success': result['success'],
                'processing_time': result['processing_time_seconds']
            })
        
        # Pause between scenarios
        if i < len(scenarios):
            input("\nPress Enter to continue to next scenario...")
    
    # Summary
    print("\n" + "="*70)
    print("SUMMARY")
    print("="*70)
    successful = sum(1 for r in results if r['success'])
    print(f"\nScenarios run: {len(results)}")
    print(f"Successful: {successful}/{len(results)}")
    print(f"Average processing time: {sum(r['processing_time'] for r in results)/len(results):.2f}s")
    
    print("\nTest complete!")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nTest interrupted by user")
    except Exception as e:
        logger.error(f"Test failed: {e}", exc_info=True)
        sys.exit(1)
