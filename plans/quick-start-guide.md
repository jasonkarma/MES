# Quick Start Guide - GenAI MES Integration

This guide will get you from zero to a working connection with your Odoo instance in ~30 minutes.

---

## Prerequisites Checklist

Before starting, verify you have:

- [ ] Odoo running in Docker (`docker ps` shows Odoo container)
- [ ] Odoo accessible at http://localhost:8069 in browser
- [ ] Python 3.10 or higher installed (`python --version`)
- [ ] OpenAI API key (get one at https://platform.openai.com/api-keys)
- [ ] Git installed
- [ ] Text editor or IDE (VS Code recommended)

---

## Step 1: Gather Odoo Connection Info (5 minutes)

### Find Odoo Container

```bash
# List running Docker containers
docker ps | grep odoo

# Example output:
# abc123def456   odoo:16.0   ... 0.0.0.0:8069->8069/tcp   odoo_web_1
```

### Get Odoo Credentials

Open your browser to http://localhost:8069 and log in with your admin credentials. Note:
- **URL**: http://localhost:8069
- **Username**: (usually `admin`)
- **Password**: (whatever you set during Odoo setup)
- **Database**: (usually `odoo`, check in URL after login)

### Verify Quality Module

In Odoo:
1. Go to Apps menu
2. Search for "Quality"
3. If not installed, install "Quality Control" app
4. Similarly, verify "Manufacturing" and "Maintenance" are installed

---

## Step 2: Create Project Structure (5 minutes)

```bash
# Navigate to your projects directory
cd ~/Documents/SF\ docs/MES/MES

# Create project structure
mkdir -p src/llm src/rag tests data/knowledge_base logs scripts docs/screenshots plans

# Create __init__.py files
touch src/__init__.py src/llm/__init__.py src/rag/__init__.py

# Create main files
touch src/config.py src/odoo_client.py src/main.py
touch src/llm/prompts.py src/llm/classifier.py src/llm/function_schemas.py
touch src/rag/vector_store.py
touch tests/test_connection.py

# Create config files
touch .env.example .gitignore requirements.txt README.md
```

---

## Step 3: Set Up Python Environment (5 minutes)

```bash
# Create virtual environment
python3 -m venv venv

# Activate it
source venv/bin/activate  # Mac/Linux
# OR
venv\Scripts\activate     # Windows

# Upgrade pip
pip install --upgrade pip
```

Create [`requirements.txt`](requirements.txt):

```
python-dotenv==1.0.0
requests==2.31.0
openai==1.12.0
langchain==0.1.10
langchain-openai==0.0.6
chromadb==0.4.22
pypdf==4.0.1
```

Install dependencies:

```bash
pip install -r requirements.txt
```

---

## Step 4: Configure Environment (3 minutes)

Create [`.env`](.env) file (this file should NOT be committed to Git):

```bash
# Odoo Configuration
ODOO_URL=http://localhost:8069
ODOO_DB=odoo
ODOO_USERNAME=admin
ODOO_PASSWORD=your_password_here

# OpenAI Configuration
OPENAI_API_KEY=sk-proj-xxxxxxxxxxxxxxxxxxxxx

# Optional: Logging
LOG_LEVEL=INFO
```

Create [`.gitignore`](.gitignore):

```
# Environment
.env
venv/
env/

# Python
__pycache__/
*.py[cod]
*$py.class
*.so
.Python

# Logs
logs/*.jsonl
logs/*.log

# IDE
.vscode/
.idea/
*.swp
*.swo

# Data
data/chroma_db/
*.db

# OS
.DS_Store
Thumbs.db
```

Create [`.env.example`](.env.example) (this SHOULD be committed):

```bash
# Odoo Configuration
ODOO_URL=http://localhost:8069
ODOO_DB=odoo
ODOO_USERNAME=admin
ODOO_PASSWORD=your_password_here

# OpenAI Configuration
OPENAI_API_KEY=sk-proj-xxxxxxxxxxxxxxxxxxxxx

# Optional: Logging
LOG_LEVEL=INFO
```

---

## Step 5: Test Odoo Connection (10 minutes)

Create [`src/config.py`](src/config.py):

```python
"""Configuration management for MES GenAI integration."""
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Odoo Configuration
ODOO_URL = os.getenv("ODOO_URL", "http://localhost:8069")
ODOO_DB = os.getenv("ODOO_DB", "odoo")
ODOO_USERNAME = os.getenv("ODOO_USERNAME", "admin")
ODOO_PASSWORD = os.getenv("ODOO_PASSWORD")

# OpenAI Configuration
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Logging Configuration
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Validation
if not ODOO_PASSWORD:
    raise ValueError("ODOO_PASSWORD must be set in .env file")

if not OPENAI_API_KEY:
    raise ValueError("OPENAI_API_KEY must be set in .env file")

print(f"✓ Configuration loaded successfully")
print(f"  - Odoo URL: {ODOO_URL}")
print(f"  - Odoo DB: {ODOO_DB}")
print(f"  - Odoo User: {ODOO_USERNAME}")
print(f"  - OpenAI API Key: {OPENAI_API_KEY[:12]}...")
```

Create [`src/odoo_client.py`](src/odoo_client.py):

```python
"""Odoo API client using XML-RPC."""
import xmlrpc.client
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


class OdooClient:
    """Client for interacting with Odoo via XML-RPC API."""
    
    def __init__(self, url: str, db: str, username: str, password: str):
        """
        Initialize Odoo client.
        
        Args:
            url: Odoo server URL (e.g., http://localhost:8069)
            db: Database name
            username: Odoo username
            password: Odoo password
        """
        self.url = url
        self.db = db
        self.username = username
        self.password = password
        
        # XML-RPC endpoints
        self.common = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/common')
        self.models = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/object')
        
        # Authenticate
        self.uid = None
        self.authenticate()
    
    def authenticate(self) -> int:
        """Authenticate with Odoo and return user ID."""
        try:
            self.uid = self.common.authenticate(
                self.db, 
                self.username, 
                self.password, 
                {}
            )
            if not self.uid:
                raise ValueError("Authentication failed - check credentials")
            logger.info(f"✓ Authenticated as user ID: {self.uid}")
            return self.uid
        except Exception as e:
            logger.error(f"✗ Authentication failed: {e}")
            raise
    
    def search(self, model: str, domain: List = None) -> List[int]:
        """
        Search for records matching domain.
        
        Args:
            model: Odoo model name (e.g., 'quality.alert')
            domain: Search domain (e.g., [('state', '=', 'open')])
        
        Returns:
            List of record IDs
        """
        domain = domain or []
        try:
            ids = self.models.execute_kw(
                self.db, self.uid, self.password,
                model, 'search', [domain]
            )
            logger.debug(f"Found {len(ids)} records in {model}")
            return ids
        except Exception as e:
            logger.error(f"Search failed for {model}: {e}")
            raise
    
    def read(self, model: str, ids: List[int], fields: List[str] = None) -> List[Dict]:
        """
        Read records by IDs.
        
        Args:
            model: Odoo model name
            ids: List of record IDs
            fields: List of field names to retrieve (None = all fields)
        
        Returns:
            List of record dictionaries
        """
        if not ids:
            return []
        
        try:
            records = self.models.execute_kw(
                self.db, self.uid, self.password,
                model, 'read', [ids], {'fields': fields} if fields else {}
            )
            logger.debug(f"Read {len(records)} records from {model}")
            return records
        except Exception as e:
            logger.error(f"Read failed for {model}: {e}")
            raise
    
    def write(self, model: str, ids: List[int], values: Dict[str, Any]) -> bool:
        """
        Update records.
        
        Args:
            model: Odoo model name
            ids: List of record IDs to update
            values: Dictionary of field values to update
        
        Returns:
            True if successful
        """
        try:
            result = self.models.execute_kw(
                self.db, self.uid, self.password,
                model, 'write', [ids, values]
            )
            logger.info(f"✓ Updated {len(ids)} records in {model}")
            return result
        except Exception as e:
            logger.error(f"Write failed for {model}: {e}")
            raise
    
    def create(self, model: str, values: Dict[str, Any]) -> int:
        """
        Create a new record.
        
        Args:
            model: Odoo model name
            values: Dictionary of field values
        
        Returns:
            ID of created record
        """
        try:
            record_id = self.models.execute_kw(
                self.db, self.uid, self.password,
                model, 'create', [values]
            )
            logger.info(f"✓ Created record {record_id} in {model}")
            return record_id
        except Exception as e:
            logger.error(f"Create failed for {model}: {e}")
            raise
    
    def search_read(self, model: str, domain: List = None, 
                    fields: List[str] = None, limit: int = None) -> List[Dict]:
        """
        Search and read in one call (more efficient).
        
        Args:
            model: Odoo model name
            domain: Search domain
            fields: Fields to retrieve
            limit: Maximum number of records
        
        Returns:
            List of record dictionaries
        """
        domain = domain or []
        kwargs = {}
        if fields:
            kwargs['fields'] = fields
        if limit:
            kwargs['limit'] = limit
        
        try:
            records = self.models.execute_kw(
                self.db, self.uid, self.password,
                model, 'search_read', [domain], kwargs
            )
            logger.debug(f"Search_read found {len(records)} records in {model}")
            return records
        except Exception as e:
            logger.error(f"Search_read failed for {model}: {e}")
            raise
```

Create [`tests/test_connection.py`](tests/test_connection.py):

```python
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
```

Run the connection test:

```bash
python tests/test_connection.py
```

**Expected output:**

```
============================================================
ODOO CONNECTION TEST
============================================================

1. Authenticating with Odoo...
   ✓ Authenticated as user ID: 2

2. Testing Manufacturing Orders access...
   ✓ Found 3 manufacturing orders
   Example: WH/MO/00002 - progress

3. Testing Quality module access...
   ✓ Found 0 quality alerts
   (No quality alerts found - this is normal for new setup)

4. Testing Maintenance module access...
   ✓ Found 0 maintenance requests

5. Testing write capability...
   Creating a test quality alert...
   ✓ Created test alert ID: 42
   Deleting test alert...
   ✓ Test alert deleted

============================================================
CONNECTION TEST COMPLETE
============================================================

✓ Your Odoo instance is accessible and ready for integration!
```

---

## Step 6: Create Sample Data in Odoo (Optional, 10 minutes)

To test the integration, create some sample data in Odoo:

### Create a Quality Alert Manually

1. Go to **Quality** app in Odoo
2. Click **Quality Alerts** > **Create**
3. Fill in:
   - **Title**: "Tooling Issue - Assembly Line 1"
   - **Description**: "Tooling chatter on step 2, tool insert T3 chipped, surface roughness high"
   - **Product**: Select any product
   - Save

### Create a Manufacturing Order

1. Go to **Manufacturing** app
2. Click **Manufacturing Orders** > **Create**
3. Fill in:
   - **Product**: Select a product (e.g., furniture item)
   - **Quantity**: 1.00
   - Confirm order

Now you have data to test with!

---

## Troubleshooting

### Problem: "Authentication failed"

**Solution:**
- Check Odoo is running: `docker ps | grep odoo`
- Verify credentials in browser login
- Check `.env` file has correct password
- Ensure database name is correct (check Odoo URL after login)

### Problem: "quality.alert not available"

**Solution:**
- Go to Odoo Apps menu
- Search for "Quality"
- Install "Quality Control" module
- Refresh browser and try again

### Problem: "Module 'dotenv' not found"

**Solution:**
- Ensure virtual environment is activated: `source venv/bin/activate`
- Reinstall dependencies: `pip install -r requirements.txt`

### Problem: "Can't connect to Docker container"

**Solution:**
```bash
# Check container is running
docker ps

# Check port mapping
docker port <container-name>

# Try 127.0.0.1 instead of localhost in .env
ODOO_URL=http://127.0.0.1:8069
```

---

## What's Next?

After completing this quick start, you should have:

- ✅ Project structure created
- ✅ Python environment set up
- ✅ Odoo connection verified
- ✅ Test script passing

**Next steps:**

1. Review the [Architecture Plan](./genai-integration-architecture.md) for system design details
2. Follow the [Implementation Checklist](./implementation-checklist.md) for phase-by-phase development
3. Start Phase 2: Build the LLM classification engine
4. Test with real defect descriptions from your Odoo instance

---

## Quick Reference Commands

```bash
# Activate virtual environment
source venv/bin/activate

# Run connection test
python tests/test_connection.py

# Check Odoo container
docker ps | grep odoo

# View Odoo logs
docker logs <container-name>

# Install new dependency
pip install <package-name>
pip freeze > requirements.txt

# Deactivate virtual environment
deactivate
```

---

## File Structure After Quick Start

```
MES/
├── src/
│   ├── __init__.py
│   ├── config.py              ✓ Created
│   ├── odoo_client.py         ✓ Created
│   ├── llm/
│   │   └── __init__.py
│   └── rag/
│       └── __init__.py
├── tests/
│   └── test_connection.py     ✓ Created
├── data/
│   └── knowledge_base/
├── logs/
├── plans/
│   ├── genai-integration-architecture.md
│   ├── implementation-checklist.md
│   └── quick-start-guide.md
├── .env                       ✓ Created (not in Git)
├── .env.example               ✓ Created
├── .gitignore                 ✓ Created
├── requirements.txt           ✓ Created
└── venv/                      ✓ Created
```

---

## Support

If you encounter issues not covered in troubleshooting:

1. Check Odoo documentation: https://www.odoo.com/documentation/
2. Review the detailed [Architecture Plan](./genai-integration-architecture.md)
3. Check logs in `logs/` directory
4. Verify all prerequisites are met

---

*Quick Start Guide v1.0 - Last updated: 2026-09-28*
