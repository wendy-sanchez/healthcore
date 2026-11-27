"""Modelos Pydantic para el chat en tiempo real del Compliance Assistant.

Sigue el BRIEFING de CONTEXT.md § "HealthCore: Sistemas en Tiempo Real (Parte 2)":
  - ChatSession → session_id, agent_id, user_id, status, messages
  - Eventos: token_chunk, generation_completed, generation_interrupted,
    session_snapshot, user_message, interrupt_requested, error
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    """Un mensaje individual dentro del historial de la sesión.

    Attributes:
        message_id: Identificador único del mensaje (msg_<hex>).
        role: "user" | "assistant" | "system".
        content: Texto completo (para role="user") o texto parcial acumulado
                 (para role="assistant"; puede quedar incompleto si se interrumpió).
        status: "streaming" | "completed" | "interrupted".
        created_at: ISO 8601 timestamp de cuándo se creó el mensaje.
    """

    message_id: str = Field(default_factory=lambda: f"msg_{uuid.uuid4().hex[:12]}")
    role: str = "assistant"
    content: str = ""
    status: str = "streaming"
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class ChatSessionState(BaseModel):
    """Estado completo de una sesión de chat.

    Attributes:
        session_id: Identificador único de la sesión (chat_<hex>).
        agent_id: Identificador del agente ("compliance_assistant").
        user_id: ID del usuario staff (nunca paciente).
        status: "active" | "interrupted" | "closed".
        messages: Historial de mensajes en orden cronológico.
        current_message_id: ID del mensaje que se está generando (o None).
        created_at: ISO 8601 timestamp de creación.
        updated_at: ISO 8601 timestamp de última modificación.
    """

    session_id: str
    agent_id: str = "compliance_assistant"
    user_id: str
    status: str = "active"
    messages: list[ChatMessage] = Field(default_factory=list)
    current_message_id: str | None = None
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def add_message(self, role: str, content: str = "", status: str = "completed") -> ChatMessage:
        """Añade un mensaje al historial y devuelve la instancia creada."""
        msg = ChatMessage(role=role, content=content, status=status)
        self.messages.append(msg)
        self.updated_at = datetime.now(timezone.utc).isoformat()
        return msg

    def get_current_message(self) -> ChatMessage | None:
        """Retorna el mensaje que se está generando (si hay)."""
        if self.current_message_id:
            for m in self.messages:
                if m.message_id == self.current_message_id:
                    return m
        return None

    def to_snapshot(self) -> dict[str, Any]:
        """Serializa el estado completo para enviar como session_snapshot."""
        return {
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "user_id": self.user_id,
            "status": self.status,
            "messages": [m.model_dump() for m in self.messages],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class EventPayload(BaseModel):
    """Payload genérico de un evento WebSocket.

    Todos los eventos siguen la misma estructura:
      {"event": "<event_type>", "data": {...}}
    """
    event: str
    data: dict[str, Any] = Field(default_factory=dict)  # noqa: Pydantic ignores Any generics


# ── Helpers para construir eventos específicos ────────────────────────


def make_token_chunk(
    session_id: str,
    token: str,
    sequence: int,
    message_id: str,
) -> EventPayload:
    """Construye un evento token_chunk."""
    return EventPayload(
        event="token_chunk",
        data={
            "session_id": session_id,
            "token": token,
            "sequence": sequence,
            "message_id": message_id,
        },
    )


def make_generation_completed(session_id: str, message_id: str) -> EventPayload:
    """Construye un evento generation_completed."""
    return EventPayload(
        event="generation_completed",
        data={
            "session_id": session_id,
            "message_id": message_id,
        },
    )


def make_generation_interrupted(session_id: str, message_id: str) -> EventPayload:
    """Construye un evento generation_interrupted."""
    return EventPayload(
        event="generation_interrupted",
        data={
            "session_id": session_id,
            "message_id": message_id,
            "status": "interrupted",
        },
    )


def make_session_snapshot(session: ChatSessionState) -> EventPayload:
    """Construye un evento session_snapshot con el historial completo."""
    return EventPayload(
        event="session_snapshot",
        data=session.to_snapshot(),
    )


def make_error(session_id: str, message: str) -> EventPayload:
    """Construye un evento error."""
    return EventPayload(
        event="error",
        data={
            "session_id": session_id,
            "message": message,
        },
    )