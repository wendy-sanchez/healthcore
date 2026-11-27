"""ChatSessionPubSub — Hub pub/sub por sesión de chat.

Cada sesión tiene su propio `ChatSessionPubSub` que mantiene un conjunto
de colas asyncio (una por WebSocket suscrito). Cuando el agente produce
un evento (token_chunk, generation_completed, etc.), se publica en todas
las colas simultáneamente.

Patrón idéntico a SSEManager (Parte 1) pero:
  - Un canal por sesión, no un único bus global.
  - Sin buffer circular (los tokens no se persisten para replay —
    el histórico de mensajes se envía como session_snapshot al reconectar).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from services.agent.chat.models import EventPayload

logger = logging.getLogger(__name__)


class ChatSessionPubSub:
    """Hub pub/sub para una sesión de chat.

    Uso:
        hub = ChatSessionPubSub("chat_0076")
        # WebSocket A se suscribe
        await hub.subscribe(ws_queue_a)
        # WebSocket B se suscribe
        await hub.subscribe(ws_queue_b)
        # El agente publica un token
        await hub.publish(token_event)
        # → ws_queue_a y ws_queue_b reciben el token
    """

    def __init__(self, session_id: str) -> None:
        self._session_id = session_id
        self._lock = asyncio.Lock()
        self._queues: set[asyncio.Queue[str]] = set()

    async def subscribe(self, queue: asyncio.Queue[str]) -> None:
        """Registra un nuevo suscriptor (cola asyncio) en este canal."""
        async with self._lock:
            self._queues.add(queue)
        logger.debug(
            "ChatPubSub [%s] suscriptor añadido. Total: %d",
            self._session_id,
            len(self._queues),
        )

    async def unsubscribe(self, queue: asyncio.Queue[str]) -> None:
        """Elimina un suscriptor de este canal."""
        async with self._lock:
            self._queues.discard(queue)
        logger.debug(
            "ChatPubSub [%s] suscriptor eliminado. Total: %d",
            self._session_id,
            len(self._queues),
        )

    async def publish(self, event: EventPayload) -> None:
        """Publica un evento serializado a todos los suscriptores del canal.

        El evento se serializa a JSON y se encola en cada cola.
        Si una cola está llena (Queue ilimitada, improbable) el suscriptor
        se descarta.
        """
        message = json.dumps(
            {"event": event.event, "data": event.data},
            ensure_ascii=False,
            default=str,
        )

        disconnected: list[asyncio.Queue[str]] = []
        async with self._lock:
            for queue in list(self._queues):
                try:
                    queue.put_nowait(message)
                except asyncio.QueueFull:
                    # Queue ilimitada, no debería ocurrir, pero por seguridad
                    disconnected.append(queue)

        if disconnected:
            async with self._lock:
                for q in disconnected:
                    self._queues.discard(q)

    @property
    def subscriber_count(self) -> int:
        """Número de suscriptores activos en este canal."""
        return len(self._queues)