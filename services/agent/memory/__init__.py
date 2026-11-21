"""Módulo de Memoria del Agente de Soporte HealthCore.

Este paquete implementa el sistema de memoria persistente para el agente
LangGraph, permitiendo que el agente recuerde hechos operativos entre
conversaciones, con control explícito del usuario y auditoría completa.

Arquitectura (ver agent-memory-harness-guard.md para diseño completo):

  Store (TinyDB)
    ├── memory_facts   — Hechos operativos aprobados (consolidados)
    ├── pending_proposal — Una única propuesta pendiente (0 o 1)
    └── audit_log      — Registro auditable de cada decisión

Flujo:
  1. Auto-evaluación: el agente genera answer + propuesta_memoria (opcional)
  2. Validación PHI: la propuesta se valida contra PHI antes de mostrar al usuario
  3. Propuesta al usuario: "¿Quieres que recuerde esto?"
  4. Clasificación: el próximo mensaje del usuario se clasifica (sí/no/editar/descartar)
  5. Escritura/Rechazo: según la decisión, se escribe en memory_facts o se descarta
  6. Auditoría: todo queda registrado (sea aprobado o rechazado)

Principios:
  - Estado mínimo: una sola propuesta pendiente a la vez
  - PHI-free: ninguna propuesta con PHI llega al usuario
  - Auditoría completa: cada decisión queda trazada
  - Sin escritura silenciosa: el usuario siempre autoriza o rechaza explícitamente
  - Sin asunción por defecto: silencio/ambigüedad = descarte
"""

from __future__ import annotations

__version__ = "0.1.0"