"""Chat en tiempo real del Compliance Assistant — modelo de datos, pub/sub y gestor de sesiones.

Paquete autocontenido que implementa el lado servidor del chat WebSocket:
  - ChatSessionState, ChatMessage: modelos Pydantic de la sesión
  - ChatSessionPubSub: hub pub/sub por sesión (broadcast a N conexiones)
  - ChatSessionManager: singleton que orquesta sesiones + streaming del agente
"""

from __future__ import annotations

from services.agent.chat.models import ChatMessage, ChatSessionState, EventPayload
from services.agent.chat.pubsub import ChatSessionPubSub
from services.agent.chat.manager import ChatSessionManager, get_chat_manager

__all__ = [
    "ChatMessage",
    "ChatSessionState",
    "EventPayload",
    "ChatSessionPubSub",
    "ChatSessionManager",
    "get_chat_manager",
]