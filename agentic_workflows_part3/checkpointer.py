"""
SQLite Checkpointer for Part 3 Agentic Workflow
Namespaces thread_id by ticket_id and optionally by department_id
Enables resumable state across interrupts and approvals
"""

import sqlite3
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any
from dataclasses import dataclass


@dataclass
class CheckpointConfig:
    """Configuration for SQLite checkpointer"""
    db_path: str = "/tmp/healthcore_part3_checkpoints.db"
    auto_create: bool = True


class Part3SQLiteCheckpointer:
    """SQLite-based checkpointer with ticket_id:department_id namespacing"""
    
    def __init__(self, config: CheckpointConfig):
        self.config = config
        self.db_path = Path(config.db_path)
        
        if config.auto_create:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._init_db()
    
    def _init_db(self) -> None:
        """Initialize SQLite database with required tables"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Checkpoints table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS checkpoints (
                    id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    ticket_id TEXT NOT NULL,
                    department_id TEXT,
                    state_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(thread_id)
                )
            """)
            
            # Approval history table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS approval_history (
                    id TEXT PRIMARY KEY,
                    ticket_id TEXT NOT NULL,
                    department_id TEXT NOT NULL,
                    approval_status TEXT NOT NULL,
                    approver_name TEXT,
                    approved_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            
            # Arbitrage history table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS arbitrage_history (
                    id TEXT PRIMARY KEY,
                    ticket_id TEXT NOT NULL,
                    conflict_id TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    rationale TEXT,
                    arbitrated_by TEXT,
                    arbitrated_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            
            conn.commit()
    
    def _make_thread_id(self, ticket_id: str, department_id: Optional[str] = None) -> str:
        """Generate namespaced thread_id: rfp-{ticket_id} or rfp-{ticket_id}:{department_id}"""
        base = f"rfp-{ticket_id}"
        if department_id:
            return f"{base}:{department_id}"
        return base
    
    def put(self, ticket_id: str, state_dict: Dict[str, Any], department_id: Optional[str] = None) -> str:
        """Save checkpoint for a ticket (optionally scoped by department)"""
        thread_id = self._make_thread_id(ticket_id, department_id)
        checkpoint_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        state_json = json.dumps(state_dict, default=str)
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO checkpoints 
                (id, thread_id, ticket_id, department_id, state_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (checkpoint_id, thread_id, ticket_id, department_id, state_json, now, now))
            conn.commit()
        
        return checkpoint_id
    
    def get(self, ticket_id: str, department_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Retrieve latest checkpoint for a ticket (optionally scoped by department)"""
        thread_id = self._make_thread_id(ticket_id, department_id)
        
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT state_json FROM checkpoints WHERE thread_id = ? ORDER BY updated_at DESC LIMIT 1
            """, (thread_id,))
            row = cursor.fetchone()
        
        if row:
            return json.loads(row['state_json'])
        return None
    
    def record_approval(
        self,
        ticket_id: str,
        department_id: str,
        approval_status: str,
        approver_name: str,
        approved_at_iso: str,
    ) -> None:
        """Record department approval in history"""
        record_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO approval_history
                (id, ticket_id, department_id, approval_status, approver_name, approved_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (record_id, ticket_id, department_id, approval_status, approver_name, approved_at_iso, now))
            conn.commit()
    
    def record_arbitrage(
        self,
        ticket_id: str,
        conflict_id: str,
        decision: str,
        rationale: Optional[str],
        arbitrated_by: str,
        arbitrated_at_iso: str,
    ) -> None:
        """Record arbitrage decision in history"""
        record_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO arbitrage_history
                (id, ticket_id, conflict_id, decision, rationale, arbitrated_by, arbitrated_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (record_id, ticket_id, conflict_id, decision, rationale, arbitrated_by, arbitrated_at_iso, now))
            conn.commit()
    
    def get_approval_history(self, ticket_id: str) -> list:
        """Get all approval records for a ticket"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM approval_history WHERE ticket_id = ? ORDER BY approved_at DESC
            """, (ticket_id,))
            rows = cursor.fetchall()
        
        return [dict(row) for row in rows]
    
    def get_arbitrage_history(self, ticket_id: str) -> list:
        """Get all arbitrage records for a ticket"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM arbitrage_history WHERE ticket_id = ? ORDER BY arbitrated_at DESC
            """, (ticket_id,))
            rows = cursor.fetchall()
        
        return [dict(row) for row in rows]
