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
    
    def safe_write(self, model: str, record_id: int, values: Dict[str, Any]) -> bool:
        """
        Safely write to Odoo with error handling.
        
        Args:
            model: Odoo model name
            record_id: Record ID to update
            values: Field values to update
        
        Returns:
            True if successful, False otherwise
        """
        try:
            return self.write(model, [record_id], values)
        except Exception as e:
            logger.error(f"Safe write failed for {model} ID {record_id}: {e}")
            logger.error(f"Attempted values: {values}")
            return False
