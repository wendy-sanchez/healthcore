"""
Part 2 Persistence Enhancement: Real-time Progress Tracking
Hito 9 — HealthCore RFP Intake Pipeline — Parte 2 (Persistence Fix)

Problema: Ticket queda en 'generando_borrador' durante toda la parte 2.
Estados intermedios (borrador, evaluación) solo se persisten al final.

Solución: Persistir estado por departamento e iteración ANTES de continuar.
"""

from typing import Dict, List, Optional
from datetime import datetime
from enum import Enum
import json
import sqlite3
from pathlib import Path


class DepartmentPhase(Enum):
    """Fases dentro de un departamento"""
    GENERANDO = "generando"
    EVALUANDO = "evaluando"
    COMPLETADO = "completado"


class Part2PersistenceTracker:
    """
    Tracker para persistir estados intermedios de Parte 2.
    
    Registra:
    - Fase actual por departamento (generando/evaluando/completado)
    - Borrador parcial tras generación
    - Evaluación parcial tras evaluación
    - Timestamps de cada transición
    """
    
    def __init__(self, db_path: str = "/tmp/healthcore_part2_progress.db"):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        """Inicializar tablas de persistencia"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Tabla: departamento_progress (seguimiento por departamento)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS department_progress (
                id INTEGER PRIMARY KEY,
                ticket_id TEXT NOT NULL,
                department_id TEXT NOT NULL,
                iteration INTEGER NOT NULL,
                phase TEXT NOT NULL,
                generation_status TEXT,
                draft TEXT,
                evaluation_json TEXT,
                started_at TEXT,
                updated_at TEXT,
                UNIQUE(ticket_id, department_id, iteration)
            )
        """)
        
        # Tabla: state_transitions (audit trail de cambios de estado)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS state_transitions (
                id INTEGER PRIMARY KEY,
                ticket_id TEXT NOT NULL,
                department_id TEXT NOT NULL,
                from_phase TEXT,
                to_phase TEXT,
                timestamp TEXT,
                data_json TEXT
            )
        """)
        
        # Tabla: ticket_progress (estado general del ticket)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ticket_progress (
                ticket_id TEXT PRIMARY KEY,
                current_status TEXT,
                updated_at TEXT,
                total_departments INTEGER,
                completed_departments INTEGER,
                current_dept_index INTEGER,
                metadata_json TEXT
            )
        """)
        
        conn.commit()
        conn.close()
    
    def start_department_generation(
        self,
        ticket_id: str,
        department_id: str,
        iteration: int,
        metadata: Optional[Dict] = None,
    ) -> None:
        """
        Registrar inicio de generación para un departamento.
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        now = datetime.utcnow().isoformat()
        
        cursor.execute("""
            INSERT OR REPLACE INTO department_progress
            (ticket_id, department_id, iteration, phase, generation_status, started_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            ticket_id, department_id, iteration,
            DepartmentPhase.GENERANDO.value,
            "in_progress",
            now, now
        ))
        
        # Registrar transición
        cursor.execute("""
            INSERT INTO state_transitions (ticket_id, department_id, from_phase, to_phase, timestamp, data_json)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            ticket_id, department_id,
            None,  # Inicio
            DepartmentPhase.GENERANDO.value,
            now,
            json.dumps(metadata or {})
        ))
        
        # Actualizar ticket progress
        cursor.execute("""
            UPDATE ticket_progress
            SET updated_at = ?, current_status = ?
            WHERE ticket_id = ?
        """, (now, "generando_borrador", ticket_id))
        
        conn.commit()
        conn.close()
    
    def complete_department_draft(
        self,
        ticket_id: str,
        department_id: str,
        iteration: int,
        draft: str,
        metadata: Optional[Dict] = None,
    ) -> None:
        """
        Persistir borrador completado para un departamento.
        ANTES de pasar a evaluación.
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        now = datetime.utcnow().isoformat()
        
        cursor.execute("""
            UPDATE department_progress
            SET draft = ?, generation_status = ?, updated_at = ?
            WHERE ticket_id = ? AND department_id = ? AND iteration = ?
        """, (
            draft, "draft_complete", now,
            ticket_id, department_id, iteration
        ))
        
        # Registrar transición
        cursor.execute("""
            INSERT INTO state_transitions (ticket_id, department_id, from_phase, to_phase, timestamp, data_json)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            ticket_id, department_id,
            DepartmentPhase.GENERANDO.value,
            DepartmentPhase.GENERANDO.value,  # Aún generando, pero borrador completo
            now,
            json.dumps({"event": "draft_complete", **(metadata or {})})
        ))
        
        conn.commit()
        conn.close()
    
    def start_department_evaluation(
        self,
        ticket_id: str,
        department_id: str,
        iteration: int,
        metadata: Optional[Dict] = None,
    ) -> None:
        """
        Registrar transición a fase de evaluación.
        ANTES de evaluar.
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        now = datetime.utcnow().isoformat()
        
        cursor.execute("""
            UPDATE department_progress
            SET phase = ?, generation_status = ?, updated_at = ?
            WHERE ticket_id = ? AND department_id = ? AND iteration = ?
        """, (
            DepartmentPhase.EVALUANDO.value,
            "evaluating",
            now,
            ticket_id, department_id, iteration
        ))
        
        # Registrar transición
        cursor.execute("""
            INSERT INTO state_transitions (ticket_id, department_id, from_phase, to_phase, timestamp, data_json)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            ticket_id, department_id,
            DepartmentPhase.GENERANDO.value,
            DepartmentPhase.EVALUANDO.value,
            now,
            json.dumps(metadata or {})
        ))
        
        # Actualizar ticket progress
        cursor.execute("""
            UPDATE ticket_progress
            SET updated_at = ?, current_status = ?
            WHERE ticket_id = ?
        """, (now, "en_evaluación", ticket_id))
        
        conn.commit()
        conn.close()
    
    def complete_department_evaluation(
        self,
        ticket_id: str,
        department_id: str,
        iteration: int,
        evaluation: Dict,
        metadata: Optional[Dict] = None,
    ) -> None:
        """
        Persistir evaluación completada para un departamento.
        ANTES de continuar con el siguiente departamento.
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        now = datetime.utcnow().isoformat()
        
        evaluation_json = json.dumps(evaluation)
        
        cursor.execute("""
            UPDATE department_progress
            SET phase = ?, generation_status = ?, evaluation_json = ?, updated_at = ?
            WHERE ticket_id = ? AND department_id = ? AND iteration = ?
        """, (
            DepartmentPhase.COMPLETADO.value,
            "completed",
            evaluation_json,
            now,
            ticket_id, department_id, iteration
        ))
        
        # Registrar transición
        cursor.execute("""
            INSERT INTO state_transitions (ticket_id, department_id, from_phase, to_phase, timestamp, data_json)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            ticket_id, department_id,
            DepartmentPhase.EVALUANDO.value,
            DepartmentPhase.COMPLETADO.value,
            now,
            json.dumps({"evaluation_complete": True, **(metadata or {})})
        ))
        
        conn.commit()
        conn.close()
    
    def get_department_progress(
        self,
        ticket_id: str,
        department_id: Optional[str] = None,
    ) -> Dict:
        """
        Consultar estado actual de departamento(s).
        
        Si department_id es None, retorna todos los departamentos.
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        if department_id:
            cursor.execute("""
                SELECT ticket_id, department_id, iteration, phase, generation_status, draft, evaluation_json, updated_at
                FROM department_progress
                WHERE ticket_id = ? AND department_id = ?
                ORDER BY iteration DESC
                LIMIT 1
            """, (ticket_id, department_id))
            row = cursor.fetchone()
            conn.close()
            
            if row:
                return {
                    "ticket_id": row[0],
                    "department_id": row[1],
                    "iteration": row[2],
                    "phase": row[3],
                    "generation_status": row[4],
                    "draft": row[5],
                    "evaluation": json.loads(row[6]) if row[6] else None,
                    "updated_at": row[7],
                }
            return None
        else:
            cursor.execute("""
                SELECT ticket_id, department_id, iteration, phase, generation_status, draft, evaluation_json, updated_at
                FROM department_progress
                WHERE ticket_id = ?
                ORDER BY department_id, iteration DESC
            """, (ticket_id,))
            rows = cursor.fetchall()
            conn.close()
            
            result = {}
            for row in rows:
                dept = row[1]
                if dept not in result:
                    result[dept] = {
                        "ticket_id": row[0],
                        "department_id": row[1],
                        "iteration": row[2],
                        "phase": row[3],
                        "generation_status": row[4],
                        "draft": row[5],
                        "evaluation": json.loads(row[6]) if row[6] else None,
                        "updated_at": row[7],
                    }
            return result
    
    def get_ticket_progress(self, ticket_id: str) -> Dict:
        """Consultar estado general del ticket"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT current_status, updated_at, total_departments, completed_departments, current_dept_index, metadata_json
            FROM ticket_progress
            WHERE ticket_id = ?
        """, (ticket_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return {
                "ticket_id": ticket_id,
                "current_status": row[0],
                "updated_at": row[1],
                "total_departments": row[2],
                "completed_departments": row[3],
                "current_dept_index": row[4],
                "metadata": json.loads(row[5]) if row[5] else None,
            }
        return None
    
    def initialize_ticket_progress(
        self,
        ticket_id: str,
        total_departments: int,
        metadata: Optional[Dict] = None,
    ) -> None:
        """Inicializar seguimiento de ticket"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        now = datetime.utcnow().isoformat()
        
        cursor.execute("""
            INSERT OR REPLACE INTO ticket_progress
            (ticket_id, current_status, updated_at, total_departments, completed_departments, current_dept_index, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            ticket_id,
            "iniciando",
            now,
            total_departments,
            0,
            0,
            json.dumps(metadata or {})
        ))
        
        conn.commit()
        conn.close()
    
    def get_state_transitions(self, ticket_id: str, department_id: Optional[str] = None) -> List[Dict]:
        """
        Obtener audit trail de transiciones de estado.
        Útil para debugging y auditoría.
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        if department_id:
            query = """
                SELECT ticket_id, department_id, from_phase, to_phase, timestamp, data_json
                FROM state_transitions
                WHERE ticket_id = ? AND department_id = ?
                ORDER BY timestamp
            """
            cursor.execute(query, (ticket_id, department_id))
        else:
            query = """
                SELECT ticket_id, department_id, from_phase, to_phase, timestamp, data_json
                FROM state_transitions
                WHERE ticket_id = ?
                ORDER BY timestamp
            """
            cursor.execute(query, (ticket_id,))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [
            {
                "ticket_id": row[0],
                "department_id": row[1],
                "from_phase": row[2],
                "to_phase": row[3],
                "timestamp": row[4],
                "data": json.loads(row[5]),
            }
            for row in rows
        ]


# Example usage pattern for Part 2:
"""
# En run_part2_pipeline():

tracker = Part2PersistenceTracker()
tracker.initialize_ticket_progress(ticket_id, total_departments=3)

for dept_index, department_id in enumerate(["revenue", "clinical", "compliance"]):
    # ANTES de generar
    tracker.start_department_generation(ticket_id, department_id, iteration=1)
    
    # Generar borrador
    draft = llm_generate_draft(department_id)
    
    # INMEDIATAMENTE DESPUÉS de generar, persistir
    tracker.complete_department_draft(ticket_id, department_id, iteration=1, draft=draft)
    
    # ANTES de evaluar
    tracker.start_department_evaluation(ticket_id, department_id, iteration=1)
    
    # Evaluar
    evaluation = llm_evaluate(draft)
    
    # INMEDIATAMENTE DESPUÉS de evaluar, persistir
    tracker.complete_department_evaluation(ticket_id, department_id, iteration=1, evaluation=evaluation)
    
    # Ahora continuar con el siguiente departamento

# Al final
tracker.update_ticket_progress(ticket_id, status="completado")
"""
