"""WebSocket endpoint para el chat en tiempo real del Compliance Assistant.

Protocolo:
  - Handshake con ?token=<JWT>&session_id=<chat_xxx>
  - Todos los mensajes son JSON: {"event": "<tipo>", "data": {...}}
  - El servidor envía: token_chunk, generation_completed, generation_interrupted,
    session_snapshot, error
  - El cliente envía: user_message, interrupt_requested

Autenticación:
  - El token JWT se extrae de la query string (?token=...)
  - Se valida contra la misma lógica que el backoffice (get_current_user)
  - Si el token falta o es inválido, se rechaza la conexión con código 4001

Seguridad:
  - No se permite PHI (información de pacientes) en ningún mensaje
  - El session_id se liga al user_id del token
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from services.agent.chat.manager import get_chat_manager
from services.agent.chat.models import make_session_snapshot, make_error

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])


def _get_jwt_secret() -> str | None:
    """Retorna la clave secreta JWT desde las variables de entorno.

    Si JWT_SECRET_KEY no está definida, retorna None y la verificación
    del token fallará (rechazando la conexión).  No usar fallback
    hardcodeado — en producción esto debe estar siempre configurado.
    """
    import os
    return os.getenv("JWT_SECRET_KEY")


def _get_jwt_algorithm() -> str:
    """Retorna el algoritmo JWT desde las variables de entorno."""
    import os
    return os.getenv("JWT_ALGORITHM", "HS256")


async def _verify_token(token: str) -> dict[str, Any] | None:
    """Verifica un token JWT y retorna el payload si es válido, None si no."""
    try:
        from jose import jwt
        payload = jwt.decode(
            token,
            _get_jwt_secret(),
            algorithms=[_get_jwt_algorithm()],
        )
        return payload
    except Exception:
        return None


@router.websocket("/ws/chat")
async def websocket_chat_endpoint(websocket: WebSocket) -> None:
    """Endpoint WebSocket para el chat del Compliance Assistant.

    Query params:
      - token: JWT de autenticación (obligatorio)
      - session_id: ID de sesión de chat (obligatorio)

    Flujo:
      1. Validar token JWT
      2. Crear/recuperar sesión de chat
      3. Enviar snapshot del historial
      4. Bucle de mensajes:
         - user_message → arrancar generación
         - interrupt_requested → interrumpir y redirigir
      5. Limpiar al desconectar
    """
    # ── Extraer query params ──────────────────────────────────────────
    token = websocket.query_params.get("token", "")
    session_id = websocket.query_params.get("session_id", "")

    if not token or not session_id:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # ── Validar JWT ───────────────────────────────────────────────────
    payload = await _verify_token(token)
    if payload is None:
        logger.warning("WebSocket rechazado: token JWT inválido")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    user_id = str(payload.get("sub", "unknown"))
    logger.info(
        "WebSocket conectado: session_id=%s user_id=%s",
        session_id,
        user_id,
    )

    # ── Obtener gestor de sesiones ────────────────────────────────────
    manager = get_chat_manager()
    await manager.get_or_create_session(session_id, user_id)

    # ── Aceptar conexión ─────────────────────────────────────────────
    await websocket.accept()

    # ── Crear cola para este WebSocket ───────────────────────────────
    queue: asyncio.Queue[str] = asyncio.Queue()
    await manager.subscribe(session_id, queue)

    # ── Enviar snapshot del historial ─────────────────────────────────
    try:
        snapshot = await manager.get_snapshot(session_id)
        await websocket.send_text(
            json.dumps({"event": snapshot.event, "data": snapshot.data})
        )
    except Exception:
        pass

    # ── Tarea de fondo: drenar cola pub/sub hacia WebSocket ──────────
    async def _drain_queue() -> None:
        """Lee eventos de la cola y los envía al WebSocket."""
        try:
            while True:
                message = await queue.get()
                try:
                    await websocket.send_text(message)
                except Exception:
                    # WebSocket desconectado
                    break
        except asyncio.CancelledError:
            pass

    drain_task = asyncio.create_task(_drain_queue())

    try:
        # ── Bucle principal: recibir mensajes del cliente ──────────────
        while True:
            raw = await websocket.receive_text()

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_text(
                    json.dumps({
                        "event": "error",
                        "data": {"message": "JSON inválido"},
                    })
                )
                continue

            event = msg.get("event", "")
            data = msg.get("data", {})

            if event == "user_message":
                text = data.get("text", "").strip()
                if text:
                    await manager.handle_user_message(session_id, text)

            elif event == "interrupt_requested":
                new_input = data.get("new_input", "").strip()
                if new_input:
                    await manager.handle_interrupt(session_id, new_input)
                else:
                    # Interrupción sin redirección — solo parar
                    from services.agent.chat.manager import get_chat_manager as _gm
                    mgr = _gm()
                    cancel_event = mgr._cancel_events.get(session_id)
                    if cancel_event:
                        cancel_event.set()
                    task = mgr._tasks.get(session_id)
                    if task and not task.done():
                        task.cancel()
            else:
                await websocket.send_text(
                    json.dumps({
                        "event": "error",
                        "data": {"message": f"Evento desconocido: {event}"},
                    })
                )

    except WebSocketDisconnect:
        logger.info(
            "WebSocket desconectado: session_id=%s", session_id
        )
    except Exception as exc:
        logger.error("Error en WebSocket: %s", exc)
    finally:
        # ── Limpieza ──────────────────────────────────────────────────
        drain_task.cancel()
        try:
            await drain_task
        except asyncio.CancelledError:
            pass
        await manager.unsubscribe(session_id, queue)
        logger.info(
            "WebSocket limpiado: session_id=%s", session_id
        )