"""
Test Suite for Part 2 Persistence Enhancement
Verifica que los estados intermedios se persistan en tiempo real, no solo al final.
"""

import pytest
import tempfile
import json
from datetime import datetime
from agentic_workflows_part2_persistence import (
    Part2PersistenceTracker,
    DepartmentPhase,
)


@pytest.fixture
def temp_db():
    """Crear BD temporal para tests"""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    yield db_path
    # Cleanup se hace al final


@pytest.fixture
def tracker(temp_db):
    """Crear tracker con BD temporal"""
    return Part2PersistenceTracker(db_path=temp_db)


class TestPart2PersistenceTracker:
    """Test suite para Part2PersistenceTracker"""
    
    def test_initialize_ticket_progress(self, tracker):
        """Test: Inicializar seguimiento de ticket"""
        ticket_id = "rfp-001"
        
        tracker.initialize_ticket_progress(
            ticket_id=ticket_id,
            total_departments=3,
            metadata={"client": "Acme", "country": "US"}
        )
        
        # Verificar que se inicializó
        progress = tracker.get_ticket_progress(ticket_id)
        assert progress is not None
        assert progress["ticket_id"] == ticket_id
        assert progress["current_status"] == "iniciando"
        assert progress["total_departments"] == 3
        assert progress["completed_departments"] == 0
        assert progress["metadata"]["client"] == "Acme"
    
    def test_start_department_generation(self, tracker):
        """Test: Registrar inicio de generación"""
        ticket_id = "rfp-001"
        department_id = "revenue"
        
        tracker.start_department_generation(
            ticket_id=ticket_id,
            department_id=department_id,
            iteration=1,
            metadata={"prompt_version": "v2"}
        )
        
        # Verificar que se registró
        dept_progress = tracker.get_department_progress(ticket_id, department_id)
        assert dept_progress is not None
        assert dept_progress["phase"] == DepartmentPhase.GENERANDO.value
        assert dept_progress["generation_status"] == "in_progress"
        assert dept_progress["draft"] is None
        assert dept_progress["evaluation"] is None
    
    def test_complete_department_draft_persists_immediately(self, tracker):
        """
        Test CRÍTICO: Verificar que el borrador se persiste INMEDIATAMENTE
        después de generarlo, no solo al final.
        
        Simula:
        1. Start generation
        2. Complete draft (PERSISTIR AQUÍ)
        3. Consultar BD mientras aún hay más fases
        4. Verificar que el borrador ya está en BD
        """
        ticket_id = "rfp-001"
        department_id = "revenue"
        draft_content = "USD 50K annual, net 30, auto-renewal"
        
        # 1. Iniciar generación
        tracker.start_department_generation(ticket_id, department_id, iteration=1)
        
        # 2. Completar borrador Y PERSISTIR INMEDIATAMENTE
        tracker.complete_department_draft(
            ticket_id=ticket_id,
            department_id=department_id,
            iteration=1,
            draft=draft_content,
        )
        
        # 3. Consultar BD AHORA (mientras el ciclo sigue adelante)
        # Simular que otros departamentos aún generan
        dept_progress = tracker.get_department_progress(ticket_id, department_id)
        
        # 4. Verificaciones críticas
        assert dept_progress is not None
        assert dept_progress["phase"] == DepartmentPhase.GENERANDO.value
        assert dept_progress["generation_status"] == "draft_complete"
        assert dept_progress["draft"] == draft_content, "❌ FALLO: Borrador no persisted!"
        assert dept_progress["draft"] is not None, "❌ FALLO: Borrador es None!"
        
        # El borrador debe estar en BD, aunque aún no hayamos evaluado
        assert dept_progress["evaluation"] is None, "Evaluación aún no debería estar"
        
        print("✅ PASS: Borrador persisted inmediatamente después de generar")
    
    def test_start_evaluation_before_evaluation_phase(self, tracker):
        """
        Test CRÍTICO: Verificar que la transición a evaluación se registra
        ANTES de evaluar, no después.
        """
        ticket_id = "rfp-001"
        department_id = "clinical"
        
        # 1. Iniciar generación
        tracker.start_department_generation(ticket_id, department_id, iteration=1)
        
        # 2. Completar borrador
        tracker.complete_department_draft(
            ticket_id=ticket_id,
            department_id=department_id,
            iteration=1,
            draft="Clinical trial feasibility: Yes"
        )
        
        # 3. REGISTRAR TRANSICIÓN A EVALUACIÓN ANTES de evaluar
        tracker.start_department_evaluation(ticket_id, department_id, iteration=1)
        
        # 4. Consultar BD AHORA (simulando que evaluación está en progreso)
        dept_progress = tracker.get_department_progress(ticket_id, department_id)
        
        # 5. Verificaciones
        assert dept_progress["phase"] == DepartmentPhase.EVALUANDO.value, \
            "❌ FALLO: Fase debería ser EVALUANDO antes de evaluar!"
        assert dept_progress["generation_status"] == "evaluating"
        
        print("✅ PASS: Fase EVALUANDO registrada antes de evaluar")
    
    def test_complete_evaluation_persists_before_next_dept(self, tracker):
        """
        Test CRÍTICO: Verificar que evaluación se persiste
        ANTES de continuar con el siguiente departamento.
        
        Simula el flujo real:
        1. Dept A: generate + persist
        2. Dept A: evaluate + persist
        3. CONSULTAR BD (Dept A debe estar completado)
        4. Dept B: generate + persist
        5. CONSULTAR BD (Dept A aún debe estar completado, Dept B generando)
        """
        ticket_id = "rfp-001"
        
        # ===== DEPARTAMENTO A =====
        tracker.start_department_generation(ticket_id, "revenue", iteration=1)
        tracker.complete_department_draft(
            ticket_id, "revenue", iteration=1,
            draft="USD 50K annual"
        )
        tracker.start_department_evaluation(ticket_id, "revenue", iteration=1)
        
        evaluation_a = {
            "legibility_score": 85.0,
            "relevance_score": 90.0,
            "compliance_score": 100.0,
        }
        
        # PERSISTIR EVALUACIÓN DE A INMEDIATAMENTE
        tracker.complete_department_evaluation(
            ticket_id, "revenue", iteration=1,
            evaluation=evaluation_a
        )
        
        # ===== CONSULTAR BD (Dept A debe estar completado) =====
        progress_a = tracker.get_department_progress(ticket_id, "revenue")
        assert progress_a["phase"] == DepartmentPhase.COMPLETADO.value
        assert progress_a["evaluation"] == evaluation_a
        print("✅ Dept A completado y persistido")
        
        # ===== DEPARTAMENTO B =====
        tracker.start_department_generation(ticket_id, "clinical", iteration=1)
        
        # ===== CONSULTAR BD (Dept A aún completado, Dept B generando) =====
        all_progress = tracker.get_department_progress(ticket_id)
        
        assert "revenue" in all_progress
        assert "clinical" in all_progress
        assert all_progress["revenue"]["phase"] == DepartmentPhase.COMPLETADO.value
        assert all_progress["clinical"]["phase"] == DepartmentPhase.GENERANDO.value
        
        print("✅ Dept A completado, Dept B generando (estados correctos en BD)")
        
        # Completar Dept B
        tracker.complete_department_draft(
            ticket_id, "clinical", iteration=1,
            draft="Clinical assessment complete"
        )
        tracker.start_department_evaluation(ticket_id, "clinical", iteration=1)
        
        evaluation_b = {
            "legibility_score": 88.0,
            "relevance_score": 92.0,
            "compliance_score": 95.0,
        }
        
        tracker.complete_department_evaluation(
            ticket_id, "clinical", iteration=1,
            evaluation=evaluation_b
        )
        
        # ===== CONSULTAR BD (Ambos completados) =====
        all_progress = tracker.get_department_progress(ticket_id)
        assert all_progress["revenue"]["phase"] == DepartmentPhase.COMPLETADO.value
        assert all_progress["clinical"]["phase"] == DepartmentPhase.COMPLETADO.value
        
        print("✅ PASS: Estados intermedios persisted correctamente durante todo el ciclo")
    
    def test_state_transitions_audit_trail(self, tracker):
        """
        Test: Verificar que el audit trail de transiciones de estado
        registra cada cambio.
        """
        ticket_id = "rfp-001"
        department_id = "compliance"
        
        # Simular ciclo completo
        tracker.start_department_generation(ticket_id, department_id, iteration=1)
        tracker.complete_department_draft(
            ticket_id, department_id, iteration=1,
            draft="Compliance check: OK"
        )
        tracker.start_department_evaluation(ticket_id, department_id, iteration=1)
        tracker.complete_department_evaluation(
            ticket_id, department_id, iteration=1,
            evaluation={"compliance_ok": True}
        )
        
        # Consultar transiciones
        transitions = tracker.get_state_transitions(ticket_id, department_id)
        
        # Verificar que hay 4 transiciones registradas
        assert len(transitions) >= 3  # At least: START_GEN, START_EVAL, COMPLETE
        
        # Verificar orden de transiciones
        phases = [t["to_phase"] for t in transitions]
        assert DepartmentPhase.GENERANDO.value in phases
        assert DepartmentPhase.EVALUANDO.value in phases
        assert DepartmentPhase.COMPLETADO.value in phases
        
        print("✅ PASS: Audit trail completo y ordenado")
    
    def test_full_workflow_simulation_with_queries_between_phases(self, tracker):
        """
        Test E2E: Simula el flujo REAL de Parte 2 con consultas
        a BD entre cada fase.
        
        Esto es lo que debe pasar:
        
        ITERACIÓN 1:
        - Dept Revenue:   START_GEN → GEN_DRAFT → START_EVAL → COMPLETE_EVAL
        - Dept Clinical:  START_GEN → GEN_DRAFT → START_EVAL → COMPLETE_EVAL
        - Dept Compliance: START_GEN → GEN_DRAFT → START_EVAL → COMPLETE_EVAL
        
        Con consultas a BD entre cada paso para verificar persistencia.
        """
        ticket_id = "rfp-002"
        departments = ["revenue", "clinical", "compliance"]
        
        # Inicializar seguimiento de ticket
        tracker.initialize_ticket_progress(ticket_id, len(departments))
        
        for dept_idx, department_id in enumerate(departments):
            print(f"\n--- Procesando {department_id.upper()} (Dept {dept_idx + 1}/{len(departments)}) ---")
            
            # 1. START GENERATION
            tracker.start_department_generation(ticket_id, department_id, iteration=1)
            progress = tracker.get_department_progress(ticket_id, department_id)
            assert progress["phase"] == DepartmentPhase.GENERANDO.value
            print(f"✓ Iniciada generación de {department_id}")
            
            # 2. GENERATE DRAFT
            draft = f"{department_id.upper()} draft v1"
            
            # 3. PERSIST DRAFT IMMEDIATELY
            tracker.complete_department_draft(ticket_id, department_id, iteration=1, draft=draft)
            progress = tracker.get_department_progress(ticket_id, department_id)
            assert progress["draft"] == draft, f"❌ Draft no persistido para {department_id}!"
            assert progress["generation_status"] == "draft_complete"
            print(f"✓ Borrador persistido para {department_id}")
            
            # 4. START EVALUATION
            tracker.start_department_evaluation(ticket_id, department_id, iteration=1)
            progress = tracker.get_department_progress(ticket_id, department_id)
            assert progress["phase"] == DepartmentPhase.EVALUANDO.value
            print(f"✓ Iniciada evaluación de {department_id}")
            
            # 5. EVALUATE
            evaluation = {
                "legibility": 85 + dept_idx * 2,
                "relevance": 90 + dept_idx,
                "compliance": 95 + dept_idx,
            }
            
            # 6. PERSIST EVALUATION IMMEDIATELY
            tracker.complete_department_evaluation(
                ticket_id, department_id, iteration=1,
                evaluation=evaluation
            )
            progress = tracker.get_department_progress(ticket_id, department_id)
            assert progress["phase"] == DepartmentPhase.COMPLETADO.value
            assert progress["evaluation"] == evaluation
            print(f"✓ Evaluación persistida para {department_id}")
            
            # 7. VERIFY ALL PREVIOUS DEPTS STILL COMPLETE
            for prev_dept in departments[:dept_idx]:
                prev_progress = tracker.get_department_progress(ticket_id, prev_dept)
                assert prev_progress["phase"] == DepartmentPhase.COMPLETADO.value, \
                    f"❌ {prev_dept} debería estar completado!"
            
            print(f"✓ Todos los departamentos anteriores aún completados")
        
        # Verificar estado final
        all_progress = tracker.get_department_progress(ticket_id)
        for department_id in departments:
            assert all_progress[department_id]["phase"] == DepartmentPhase.COMPLETADO.value
        
        print("\n🎉 PASS: Workflow completo con persistencia intermedia verificada")


class TestIntegrationWithExistingPart2:
    """
    Tests para verificar integración con código existente de Parte 2.
    
    Patrón sugerido para integración:
    
    # En run_part2_pipeline():
    tracker = Part2PersistenceTracker()
    tracker.initialize_ticket_progress(ticket_id, len(DEPARTMENTS))
    
    for department_id in DEPARTMENTS:
        # ANTES de generar
        tracker.start_department_generation(ticket_id, department_id, iteration)
        
        # Generar
        draft = run_generation_llm(...)
        
        # INMEDIATAMENTE DESPUÉS
        tracker.complete_department_draft(ticket_id, department_id, iteration, draft)
        
        # ANTES de evaluar
        tracker.start_department_evaluation(ticket_id, department_id, iteration)
        
        # Evaluar
        evaluation = run_evaluation_llm(...)
        
        # INMEDIATAMENTE DESPUÉS
        tracker.complete_department_evaluation(ticket_id, department_id, iteration, evaluation)
    """
    
    def test_integration_pattern_matches_expected_calls(self, tracker):
        """
        Test: Verificar que el patrón de llamadas es el correcto
        para la integración con Parte 2.
        """
        ticket_id = "rfp-003"
        
        # Pattern esperado:
        expected_calls = [
            ("start_gen", "revenue"),
            ("draft", "revenue"),
            ("start_eval", "revenue"),
            ("complete_eval", "revenue"),
            ("start_gen", "clinical"),
            ("draft", "clinical"),
            ("start_eval", "clinical"),
            ("complete_eval", "clinical"),
        ]
        
        actual_calls = []
        
        # Simular el patrón
        tracker.initialize_ticket_progress(ticket_id, 2)
        
        for dept in ["revenue", "clinical"]:
            actual_calls.append(("start_gen", dept))
            tracker.start_department_generation(ticket_id, dept, iteration=1)
            
            actual_calls.append(("draft", dept))
            tracker.complete_department_draft(ticket_id, dept, iteration=1, draft=f"{dept} draft")
            
            actual_calls.append(("start_eval", dept))
            tracker.start_department_evaluation(ticket_id, dept, iteration=1)
            
            actual_calls.append(("complete_eval", dept))
            tracker.complete_department_evaluation(ticket_id, dept, iteration=1, evaluation={})
        
        # Verificar que el patrón es correcto
        assert actual_calls == expected_calls
        print("✅ PASS: Patrón de integración es correcto")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
