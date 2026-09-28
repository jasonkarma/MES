"""Configuration management for MES GenAI integration."""
import os
import logging
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

# Set up logging
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/mes_integration.log'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

# Validation
if not ODOO_PASSWORD:
    raise ValueError("ODOO_PASSWORD must be set in .env file")

if not OPENAI_API_KEY:
    raise ValueError("OPENAI_API_KEY must be set in .env file")

logger.info("✓ Configuration loaded successfully")
logger.info(f"  - Odoo URL: {ODOO_URL}")
logger.info(f"  - Odoo DB: {ODOO_DB}")
logger.info(f"  - Odoo User: {ODOO_USERNAME}")
logger.info(f"  - OpenAI API Key: {OPENAI_API_KEY[:12]}...")
