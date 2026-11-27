"""SSE (Server-Sent Events) para HealthCore.

Paquete que implementa un gestor de eventos en tiempo real para notificar
al frontend sobre cambios de estado en el sistema, sin polling.

Componentes:
  - SSEManager: singleton que mantiene colas de suscriptores, publica eventos
    y soporta replay mediante buffer circular.
  - get_sse_manager(): factoría que retorna la instancia única.

Uso:
  from services.api.sse.manager import get_sse_manager

  manager = get_sse_manager()
  manager.publish(
      event="rfp_ticket_created",
      data={"ticket_id": "..."},
      event_id="evt_001",
  )
"""

from services.api.sse.manager import SSEManager, get_sse_manager

__all__ = ["SSEManager", "get_sse_manager"]