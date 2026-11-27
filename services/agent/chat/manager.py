"""ChatSessionManager — Singleton gestor de sesiones de chat.

Orquesta el ciclo de vida de cada sesión:
  - Creación y recuperación de ChatSessionState
  - Ejecución del agente LangGraph en modo streaming (astream_events)
  - Publicación de tokens en el pub/sub de la sesión
  - Manejo de interrupciones (cancelación del stream y nuevo turno)

Arquitectura:
  ChatSessionManager (singleton)
    ├── sesiones: dict[session_id → ChatSessionState]
    ├── hubs: dict[session_id → ChatSessionPubSub]
    ├── tasks: dict[session_id → asyncio.Task]  # generación en curso
    └── cancel_events: dict[session_id → asyncio.Event]  # señal de cancelación
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from services.agent.chat.models import (
    ChatMessage,
    ChatSessionState,
    EventPayload,
    make_token_chunk,
    make_generation_completed,
    make_generation_interrupted,
    make_session_snapshot,
    make_error,
)
from services.agent.chat.pubsub import ChatSessionPubSub
from services.agent.graph import agent_graph, build_initial_state

logger = logging.getLogger(__name__)


class ChatSessionManager:
    """Singleton que gestiona todas las sesiones de chat activas.

    Métodos públicos:
      get_or_create_session(session_id, user_id) → ChatSessionState
      subscribe(session_id, queue) → None
      unsubscribe(session_id, queue) → None
      handle_user_message(session_id, text) → None
      handle_interrupt(session_id, new_input) → None
      get_snapshot(session_id) → EventPayload
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        # Estado persistente de las sesiones (en memoria)
        self._sessions: dict[str, ChatSessionState] = {}
        # Hubs pub/sub por sesión
        self._hubs: dict[str, ChatSessionPubSub] = {}
        # Tareas de generación en curso por sesión
        self._tasks: dict[str, asyncio.Task[None]] = {}
        # Señal de cancelación por sesión
        self._cancel_events: dict[str, asyncio.Event] = {}

    # ── Sesiones ─────────────────────────────────────────────────────

    async def get_or_create_session(
        self,
        session_id: str,
        user_id: str,
    ) -> ChatSessionState:
        """Recupera una sesión existente o crea una nueva."""
        async with self._lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = ChatSessionState(
                    session_id=session_id,
                    user_id=user_id,
                )
                self._hubs[session_id] = ChatSessionPubSub(session_id)
                self._cancel_events[session_id] = asyncio.Event()
                logger.info(
                    "Sesión creada: session_id=%s user_id=%s",
                    session_id,
                    user_id,
                )
            return self._sessions[session_id]

    # ── Pub/Sub ──────────────────────────────────────────────────────

    async def subscribe(
        self,
        session_id: str,
        queue: asyncio.Queue[str],
    ) -> None:
        """Suscribe una cola (WebSocket) al canal pub/sub de la sesión."""
        hub = await self._get_hub(session_id)
        await hub.subscribe(queue)

    async def unsubscribe(
        self,
        session_id: str,
        queue: asyncio.Queue[str],
    ) -> None:
        """Elimina una cola (WebSocket) del canal pub/sub de la sesión."""
        hub = await self._get_hub(session_id)
        await hub.unsubscribe(queue)

    async def _get_hub(self, session_id: str) -> ChatSessionPubSub:
        """Retorna el hub pub/sub de la sesión (lo crea si no existe)."""
        async with self._lock:
            if session_id not in self._hubs:
                self._hubs[session_id] = ChatSessionPubSub(session_id)
            return self._hubs[session_id]

    # ── Mensajes de usuario ──────────────────────────────────────────

    async def handle_user_message(
        self,
        session_id: str,
        text: str,
    ) -> None:
        """Procesa un mensaje de usuario: lo guarda y arranca generación."""
        session = await self._get_session(session_id)

        # Añadir el mensaje del usuario al historial
        session.add_message(role="user", content=text, status="completed")

        # Arrancar la generación del asistente
        await self._start_generation(session_id)

    # ── Interrupción ─────────────────────────────────────────────────

    async def handle_interrupt(
        self,
        session_id: str,
        new_input: str,
    ) -> None:
        """Interrumpe la generación en curso y arranca un nuevo turno.

        Flujo:
          1. Señalizar cancelación (asyncio.Event).
          2. Publicar generation_interrupted.
          3. Marcar el mensaje parcial como "interrupted".
          4. Añadir el nuevo input del usuario al historial.
          5. Esperar a que la tarea anterior termine.
          6. Arrancar nuevo turno de generación.
        """
        session = await self._get_session(session_id)
        session.status = "interrupted"

        # Señalizar cancelación
        cancel_event = self._cancel_events.get(session_id)
        if cancel_event:
            cancel_event.set()

        # Publicar evento de interrupción
        current_msg_id = session.current_message_id
        if current_msg_id:
            hub = await self._get_hub(session_id)
            await hub.publish(
                make_generation_interrupted(session_id, current_msg_id)
            )

        # Cancelar la tarea asyncio (fuerza CancelledError en el generador)
        task = self._tasks.get(session_id)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Marcar mensaje parcial como interrupted
        current = session.get_current_message()
        if current:
            current.status = "interrupted"
            current.content = current.content  # preservar tokens ya generados

        # Resetear estado de cancelación para el nuevo turno
        session.current_message_id = None
        session.status = "active"
        if cancel_event:
            cancel_event.clear()

        # Añadir nuevo input del usuario
        session.add_message(role="user", content=new_input, status="completed")

        # Arrancar nuevo turno
        await self._start_generation(session_id)

    # ── Snapshot ─────────────────────────────────────────────────────

    async def get_snapshot(self, session_id: str) -> EventPayload:
        """Retorna el evento session_snapshot con el historial completo."""
        session = await self._get_session(session_id)
        return make_session_snapshot(session)

    # ── Generación (streaming) ───────────────────────────────────────

    async def _start_generation(self, session_id: str) -> None:
        """Arranca una tarea asíncrona que ejecuta el agente en modo streaming."""
        session = await self._get_session(session_id)

        # Crear mensaje del asistente (inicialmente vacío, status="streaming")
        assistant_msg = session.add_message(role="assistant", content="", status="streaming")
        session.current_message_id = assistant_msg.message_id

        # Crear tarea asyncio para la generación
        task = asyncio.create_task(
            self._run_agent_stream(session_id, assistant_msg.message_id)
        )
        async with self._lock:
            # Cancelar tarea previa si existe
            old_task = self._tasks.get(session_id)
            if old_task and not old_task.done():
                old_task.cancel()
                try:
                    await old_task
                except asyncio.CancelledError:
                    pass
            self._tasks[session_id] = task

    async def _run_agent_stream(
        self,
        session_id: str,
        message_id: str,
    ) -> None:
        """Ejecuta el agente LangGraph en modo streaming y publica tokens.

        Usa `agent_graph.astream_events(version="v2")` para obtener
        eventos a nivel de nodo. Filtra por nodo `generate_answer` para
        extraer el contenido del campo `answer` y emitirlo token por token.

        El thread_id se iguala al session_id para permitir rehidratación
        del checkpoint al reconectar (BRIEFING Parte 2 §3).

        Soporta cancelación vía `asyncio.CancelledError`.
        """
        session = await self._get_session(session_id)
        hub = await self._get_hub(session_id)

        # Obtener el último mensaje de usuario como pregunta
        user_messages = [m for m in session.messages if m.role == "user"]
        if not user_messages:
            await hub.publish(make_error(session_id, "No hay mensaje de usuario para procesar."))
            return

        question = user_messages[-1].content

        # Construir estado inicial para el agente
        try:
            # Usar session_id como thread_id para permitir rehidratación
            # del checkpoint al reconectar (según BRIEFING Parte 2 §3).
            initial_state = build_initial_state(question, run_id=session_id)
        except Exception as exc:
            logger.error("Error building initial state: %s", exc)
            await hub.publish(make_error(session_id, f"Error al preparar el agente: {exc}"))
            return

        config = {
            "configurable": {"thread_id": session_id},
        }

        sequence = 0
        accumulated_answer = ""

        try:
            # Usar astream con version="v2" para token-level streaming.
            # v2 es la versión actual recomendada por LangChain; v1 está deprecado.
            async for event in agent_graph.astream_events(
                initial_state,
                config=config,
                version="v2",
            ):
                # Verificar cancelación
                cancel_event = self._cancel_events.get(session_id)
                if cancel_event and cancel_event.is_set():
                    logger.info("Generación cancelada para sesión %s", session_id)
                    break

                # Filtrar eventos de nodo con datos relevantes
                if event["event"] == "on_chain_end" and "generate_answer" in event.get("name", ""):
                    # El nodo generate_answer ha terminado — extraer el answer
                    output = event.get("data", {}).get("output", {})
                    if isinstance(output, dict) and "answer" in output:
                        new_text = output["answer"]
                        # Emitir el texto completo como un solo token si es la primera vez
                        if not accumulated_answer:
                            # Calcular la diferencia con lo ya acumulado
                            diff = new_text
                            if diff:
                                sequence += 1
                                accumulated_answer = new_text
                                await hub.publish(
                                    make_token_chunk(session_id, diff, sequence, message_id)
                                )

                # También capturar eventos de tipo on_chain_stream para streaming fino
                elif event["event"] == "on_chat_model_stream":
                    # Algunos providers emiten chunks a nivel de modelo de chat
                    chunk = event.get("data", {}).get("chunk", {})
                    if hasattr(chunk, "content") and chunk.content:
                        content = chunk.content
                        if content:
                            sequence += 1
                            accumulated_answer += content
                            await hub.publish(
                                make_token_chunk(session_id, content, sequence, message_id)
                            )

                # Dar oportunidad a otras tareas
                await asyncio.sleep(0)

            # Si nos interrumpieron, no publicar completion
            cancel_event = self._cancel_events.get(session_id)
            if cancel_event and cancel_event.is_set():
                return

            # Si no se acumuló respuesta via eventos, intentar ainvoke directo
            if not accumulated_answer:
                try:
                    final_state = await agent_graph.ainvoke(initial_state, config=config)
                    answer = final_state.get("answer", "")
                    if answer:
                        sequence += 1
                        accumulated_answer = answer
                        await hub.publish(
                            make_token_chunk(session_id, answer, sequence, message_id)
                        )
                except Exception as exc:
                    logger.error("Error en ainvoke de respaldo: %s", exc)

            # Actualizar el mensaje del asistente en el historial
            session_msg = session.get_current_message()
            if session_msg:
                session_msg.content = accumulated_answer
                session_msg.status = "completed"

            # Publicar completion
            await hub.publish(make_generation_completed(session_id, message_id))
            logger.info(
                "Generación completada: session=%s msg=%s tokens=%d",
                session_id,
                message_id,
                sequence,
            )

        except asyncio.CancelledError:
            logger.info("Generación cancelada por interrupt: session=%s", session_id)
            # Marcar el mensaje como interrumpido
            session_msg = session.get_current_message()
            if session_msg:
                session_msg.content = accumulated_answer
                session_msg.status = "interrupted"
            raise
        except Exception as exc:
            logger.error("Error en generación streaming: %s", exc, exc_info=True)
            await hub.publish(make_error(session_id, f"Error del agente: {exc}"))
            session_msg = session.get_current_message()
            if session_msg:
                session_msg.content = accumulated_answer
                session_msg.status = "interrupted"

    # ── Helpers internos ─────────────────────────────────────────────

    async def _get_session(self, session_id: str) -> ChatSessionState:
        """Retorna la sesión o lanza KeyError si no existe."""
        async with self._lock:
            if session_id not in self._sessions:
                raise KeyError(f"Sesión no encontrada: {session_id}")
            return self._sessions[session_id]

    async def close_session(self, session_id: str) -> None:
        """Cierra una sesión y cancela su generación en curso."""
        async with self._lock:
            task = self._tasks.pop(session_id, None)
            if task and not task.done():
                task.cancel()
            self._sessions.pop(session_id, None)
            self._hubs.pop(session_id, None)
            self._cancel_events.pop(session_id, None)
            logger.info("Sesión cerrada: %s", session_id)


# ── Singleton ────────────────────────────────────────────────────────

_instance: ChatSessionManager | None = None


def get_chat_manager() -> ChatSessionManager:
    """Factoría singleton del ChatSessionManager."""
    global _instance
    if _instance is None:
        _instance = ChatSessionManager()
    return _instance