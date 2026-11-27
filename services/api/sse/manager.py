"""
Gestor de Server-Sent Events (SSE) para HealthCore.

Mantiene colas asyncio por suscriptor, publica eventos a todas las
conexiones activas, y soporta replay de eventos perdidos mediante
un buffer circular con Last-Event-ID.

Arquitectura:
  - SSEManager es un singleton thread-safe.
  - Cada suscriptor (conexión SSE) obtiene una asyncio.Queue.
  - publish() itera la lista de suscriptores bajo un lock y encola
    el evento en cada cola.
  - El buffer circular (max_history) retiene los últimos N eventos
    para replay cuando un cliente reconecta con Last-Event-ID.

Uso en rfp.py:
  from services.api.sse.manager import get_sse_manager
  manager = get_sse_manager()
  manager.publish(event="rfp_ticket_created", data={...}, event_id="evt_001")
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Optional

logger = logging.getLogger(__name__)

# ── 30s de keep-alive ────────────────────────────────────────────────
KEEPALIVE_INTERVAL = 30.0

# ── Config del buffer circular ───────────────────────────────────────
MAX_HISTORY = 50


@dataclass
class SseEvent:
    """Un evento dentro del buffer circular."""
    event_id: str
    event_name: str
    data: dict[str, Any]
    timestamp: float = field(default_factory=time.time)


class SSEManager:
    """
    Gestor de conexiones SSE thread-safe.

    Mantiene un conjunto de suscriptores, cada uno con su cola asyncio.
    Publica eventos a todos los suscriptores activos y mantiene un buffer
    circular para replay de eventos perdidos.

    Métodos públicos:
      subscribe()   → Registra un suscriptor, devuelve un async generator
      unsubscribe() → Elimina un suscriptor
      publish()     → Encola un evento en todos los suscriptores activos
      get_history() → Retorna eventos desde un event_id dado (replay)
    """

    def __init__(self, max_history: int = MAX_HISTORY):
        self._lock = asyncio.Lock()
        self._queues: set[asyncio.Queue[str]] = set()
        self._history: deque[SseEvent] = deque(maxlen=max_history)
        self._max_history = max_history
        self._event_counter = 0

    async def subscribe(
        self,
        request: Optional["Request"] = None,
    ) -> AsyncGenerator[str, None]:
        """
        Registra un nuevo suscriptor y produce eventos en su cola.

        Si se provee un request de FastAPI, chequea `request.is_disconnected()`
        en cada iteración para detectar desconexión del cliente sin esperar
        el timeout de keep-alive.

        El generador se ejecuta dentro de un StreamingResponse de FastAPI.
        Cuando el cliente cierra la conexión, el generador se limpia solo
        al salir del bloque `finally`.
        """
        queue: asyncio.Queue[str] = asyncio.Queue()
        async with self._lock:
            self._queues.add(queue)
        logger.debug("SSE suscriptor conectado. Total: %d", len(self._queues))

        try:
            while True:
                # Detectar desconexión temprana del cliente
                if request is not None and await request.is_disconnected():
                    logger.debug("SSE cliente desconectado detectado — saliendo")
                    break

                try:
                    # Espera hasta 30s por un evento; si expira, envía keep-alive
                    message = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_INTERVAL)
                    yield message
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            # Limpieza al desconectar
            async with self._lock:
                self._queues.discard(queue)
            logger.debug("SSE suscriptor desconectado. Total: %d", len(self._queues))

    async def unsubscribe(self, queue: asyncio.Queue[str]) -> None:
        """Elimina un suscriptor (llamado explícitamente si es necesario)."""
        async with self._lock:
            self._queues.discard(queue)

    async def publish(
        self,
        event: str,
        data: dict[str, Any],
        event_id: str | None = None,
    ) -> str:
        """
        Publica un evento a todos los suscriptores activos.

        Args:
            event: Nombre del evento (ej. "rfp_ticket_created").
            data: Payload del evento (dict serializable a JSON).
            event_id: ID único opcional. Si no se provee, se genera uno.

        Returns:
            El event_id asignado.
        """
        # Generar event_id auto-incremental
        if event_id is None:
            self._event_counter += 1
            event_id = f"evt_{self._event_counter:05d}_{int(time.time())}"

        # Serializar a framing SSE
        payload_lines = json.dumps(data, ensure_ascii=False, default=str)
        message = f"id: {event_id}\nevent: {event}\ndata: {payload_lines}\n\n"

        # Guardar en buffer circular para replay
        sse_event = SseEvent(
            event_id=event_id,
            event_name=event,
            data=data,
        )
        self._history.append(sse_event)

        # Broadcast a todos los suscriptores
        disconnected: list[asyncio.Queue[str]] = []
        async with self._lock:
            for queue in list(self._queues):
                try:
                    queue.put_nowait(message)
                except asyncio.QueueFull:
                    # Rarísimo con Queue ilimitada, pero por si acaso
                    disconnected.append(queue)

        # Limpiar colas que fallaron
        if disconnected:
            async with self._lock:
                for q in disconnected:
                    self._queues.discard(q)

        logger.debug(
            "SSE evento publicado: %s [%s] → %d suscriptores",
            event, event_id, len(self._queues),
        )
        return event_id

    def get_history(self, since_event_id: str | None) -> list[SseEvent]:
        """
        Retorna eventos desde un event_id dado (exclusivo).

        Usado cuando un cliente reconecta con Last-Event-ID para replay.
        Si since_event_id es None, retorna la historia completa (últimos N).
        """
        if not since_event_id:
            return list(self._history)

        events = []
        found = False
        for ev in self._history:
            if found:
                events.append(ev)
            elif ev.event_id == since_event_id:
                found = True
        return events

    @property
    def subscriber_count(self) -> int:
        """Número de suscriptores activos."""
        return len(self._queues)


# ── Singleton global ──────────────────────────────────────────────────

_instance: SSEManager | None = None


def get_sse_manager(max_history: int = MAX_HISTORY) -> SSEManager:
    """
    Factoría singleton. Retorna la misma instancia de SSEManager en cada
    llamada, compartida por todos los routers y servicios.
    """
    global _instance
    if _instance is None:
        _instance = SSEManager(max_history=max_history)
    return _instance