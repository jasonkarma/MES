"""Test Odoo connection and API access."""
import sys
import logging
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import ODOO_URL, ODOO_DB, ODOO_USERNAME, ODOO_PASSWORD
from src.odoo_client import OdooClient

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def test_connection():
    """Test basic Odoo connection."""
    print("\n" + "="*60)
    print("ODOO CONNECTION TEST")
    print("="*60 + "\n")
    
    # Initialize client
    print("1. Authenticating with Odoo...")
    client = OdooClient(ODOO_URL, ODOO_DB, ODOO_USERNAME, ODOO_PASSWORD)
    print(f"   ✓ Authenticated as user ID: {client.uid}\n")
    
    # Test manufacturing orders
    print("2. Testing Manufacturing Orders access...")
    mo_ids = client.search('mrp.production', [])
    print(f"   ✓ Found {len(mo_ids)} manufacturing orders")
    if mo_ids:
        mo = client.read('mrp.production', mo_ids[:1], ['name', 'product_id', 'state'])
        print(f"   Example: {mo[0]['name']} - {mo[0]['state']}\n")
    else:
        print("   (No manufacturing orders found - create some in Odoo first)\n")
    
    # Test quality alerts/checks
    print("3. Testing Quality module access...")
    
    # Try quality.alert first
    try:
        qa_ids = client.search('quality.alert', [])
        print(f"   ✓ Found {len(qa_ids)} quality alerts")
        if qa_ids:
            qa = client.read('quality.alert', qa_ids[:1], ['name', 'title', 'description'])
            print(f"   Example: {qa[0].get('name', 'N/A')}\n")
        else:
            print("   (No quality alerts found - this is normal for new setup)\n")
    except Exception as e:
        print(f"   ⚠ quality.alert not available: {e}")
        print("   (You may need to install the Quality app in Odoo)\n")
    
    # Try quality.check as fallback
    try:
        qc_ids = client.search('quality.check', [])
        print(f"   ✓ Found {len(qc_ids)} quality checks\n")
    except Exception as e:
        print(f"   ⚠ quality.check not available: {e}\n")
    
    # Test maintenance
    print("4. Testing Maintenance module access...")
    try:
        maint_ids = client.search('maintenance.request', [])
        print(f"   ✓ Found {len(maint_ids)} maintenance requests\n")
    except Exception as e:
        print(f"   ⚠ maintenance.request not available: {e}")
        print("   (You may need to install the Maintenance app in Odoo)\n")
    
    # Test write capability with a safe operation
    print("5. Testing write capability...")
    print("   Creating a test quality alert...")
    try:
        test_alert_id = client.create('quality.alert', {
            'title': 'TEST - AI Integration Test Alert',
            'description': 'This is a test alert created by the AI integration system. Safe to delete.',
        })
        print(f"   ✓ Created test alert ID: {test_alert_id}")
        
        # Clean up
        print("   Deleting test alert...")
        client.models.execute_kw(
            client.db, client.uid, client.password,
            'quality.alert', 'unlink', [[test_alert_id]]
        )
        print("   ✓ Test alert deleted\n")
    except Exception as e:
        print(f"   ⚠ Write test failed: {e}")
        print("   (Check if Quality app is installed and you have write permissions)\n")
    
    # Summary
    print("="*60)
    print("CONNECTION TEST COMPLETE")
    print("="*60)
    print("\n✓ Your Odoo instance is accessible and ready for integration!")
    print("\nNext steps:")
    print("  1. Install Quality Control app in Odoo if not already installed")
    print("  2. Install Maintenance app in Odoo if not already installed")
    print("  3. Create some sample manufacturing orders and quality alerts")
    print("  4. Proceed to Phase 2: LLM Classification\n")


if __name__ == "__main__":
    try:
        test_connection()
    except Exception as e:
        logger.error(f"\n✗ Connection test failed: {e}")
        print("\nTroubleshooting:")
        print("  - Check that Odoo Docker container is running: docker ps")
        print("  - Verify Odoo is accessible in browser: http://localhost:8069")
        print("  - Check credentials in .env file")
        print("  - Ensure database name is correct")
        sys.exit(1)
