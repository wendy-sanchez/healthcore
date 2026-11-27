# HealthCore: Sistemas en Tiempo Real

> **Proyecto:** Notificación en tiempo real de tickets RFP vía Server-Sent Events
> **Fecha:** 2026-11-27
> **Estado:** ✅ Implementado — 2 eventos SSE, dashboard con reconexión, backoff progresivo, recuperación vía Last-Event-ID + refetch

---
## Notificaciones SSE - Parte 1 

### 1. Preguntas de Diseño (Respondidas)

#### 1.1 ¿Dos personas del equipo comercial abren el dashboard al mismo tiempo, cada conexión SSE debería ser independiente o deberían compartir alguna capa intermedia? ¿Qué pasaría si 50 personas lo abren a la vez?

**Cada conexión SSE es independiente.** No compartimos una capa intermedia como Redis Pub/Sub en esta parte porque:

- El volumen de eventos es bajísimo: una RFP cada horas/días, no miles por segundo. Una cola en memoria por conexión es perfectamente viable.
- Simplifica la infraestructura: no necesitamos Redis ni un message broker adicional solo para SSE.
- Cada cliente mantiene su propio `last_event_id` y buffer de replay, lo que permite recuperación independiente sin contaminación entre suscriptores.

**Con 50 personas conectadas:**
- Cada conexión mantiene una `asyncio.Queue` en memoria. 50 colas × ~200 bytes de overhead ≈ 10 KB de RAM. Irrelevante.
- El broadcast (`publish` → iterar 50 colas y hacer `put`) es O(n) pero con n=50 es despreciable (microsegundos).
- El cuello de botella real sería el ancho de banda de salida: 50 conexiones SSE recibiendo el mismo payload de ~200 bytes cada una = 10 KB por evento. Con 1 evento cada varios minutos, es trivial.
- Si escalara a miles de conexiones, migraríamos a Redis Pub/Sub + worker dedicado, pero con el perfil de HealthCore (decenas de usuarios backoffice, no miles) la solución en memoria es correcta.

**Riesgo mitigado:** El `SSEManager` usa un `asyncio.Lock` para la lista de suscriptores, y cada `publish` adquiere el lock solo para iterar la lista, no para escribir en cada cola individual (las colas de `asyncio` son thread-safe por diseño).

#### 1.2 ¿Qué estrategia de recuperación elegiste para tickets registrados mientras estabas desconectado, y cómo evitas duplicados tras reconectar?

**Estrategia elegida: Last-Event-ID + replay corto + refetch de reconciliación.**

El flujo es:

1. **Cada evento SSE lleva un campo `id:`** con un UUID incremental (`last_event_id`). El servidor mantiene un buffer circular de los últimos 50 eventos.
2. **Al reconectar**, el cliente envía `Last-Event-ID: <id>` en el header de la petición fetch. El servidor:
   - Replayea todos los eventos posteriores a ese ID desde el buffer circular.
   - Luego continúa con eventos en vivo.
3. **Refetch de reconciliación:** Inmediatamente después de reconectar, el cliente hace un `GET /rfp/tickets` para obtener la lista completa actual. Esto cubre el caso borde de que el buffer circular del servidor ya haya rotado (se hayan emitido >50 eventos desde la desconexión).
4. **Deduplicación en UI:** El frontend mantiene un `Set<string>` de `ticket_id`s ya mostrados. Tanto los eventos SSE como los tickets del refetch verifican contra este set antes de añadir una notificación. Si el `ticket_id` ya existe, se omite. Esto elimina cualquier riesgo de duplicado, incluso si el replay y el refetch traen el mismo ticket.

**Buffer circular:** Tamaño fijo de 50 eventos configurable via `SSEManager(max_history=50)`. Si el servidor ha emitido más de 50 eventos desde la desconexión, el replay solo recupera los últimos 50 — el refetch cubre el resto.

#### 1.3 ¿Por qué SSE es la herramienta correcta para notificar la llegada de un ticket y no WebSockets? ¿En qué momento dejaría de serlo?

**SSE es la herramienta correcta porque:**

- **Comunicación unidireccional:** La notificación de un ticket nuevo es servidor → cliente. El cliente nunca necesita enviar datos al servidor en este canal. SSE está diseñado exactamente para esto.
- **HTTP nativo:** SSE funciona sobre HTTP estándar, atraviesa proxies y balanceadores de carga sin configuración especial. WebSockets requieren upgrade de protocolo y a menudo conexiones dedicadas.
- **Reconexión automática:** La mayoría de clientes SSE (y el nuestro con fetch+ReadableStream) pueden reconectarse con backoff sin lógica adicional. WebSockets requieren implementar heartbeat y reconexión manual.
- **Simplicidad:** SSE es un stream de texto plano. No hay handshake de upgrade, no hay framing binario, no hay close frame. El servidor envía líneas `event:` y `data:` y ya.

**Dejaría de serlo si:**

- El cliente necesitara **enviar datos al servidor** en el mismo canal (ej. un chat donde el usuario escribe mensajes y recibe respuestas en tiempo real). En ese caso WebSockets (o un flujo bidireccional) sería necesario.
- Necesitáramos **comunicación full-duplex con baja latencia** donde ambos lados inician mensajes frecuentemente.
- El frontend necesitara **reaccionar al ticket desde el mismo canal** (ej. aprobar, rechazar, o modificar el estado del ticket directamente desde la notificación sin una llamada HTTP separada). Aunque se podría diseñar un protocolo sobre SSE+POST para esto, WebSockets son más naturales para bidireccional.

> **Nota:** La Parte 2 de este proyecto (chat en tiempo real con agentes) usará WebSockets precisamente porque ahí la comunicación es bidireccional. Esta separación de canales (SSE para notificaciones unidireccionales, WebSocket para chat) es intencional y está alineada con el principio de «la herramienta correcta para cada trabajo».

---

### 2. Arquitectura del Sistema

```
┌─────────────────────────────────────────────────────────────────┐
│                         Frontend (Next.js)                      │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │  RFP Dashboard (rfp/page.tsx)                              │ │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────────────────────┐  │ │
│  │  │ Tabla    │  │ Banners  │  │ SSE Client (sseClient.ts)│  │ │
│  │  │ tickets  │  │ en vivo  │  │ fetch + ReadableStream   │  │ │
│  │  └──────────┘  └──────────┘  │ backoff progresivo       │  │ │
│  │                              │ Last-Event-ID replay     │  │ │
│  │                              │ refetch reconciliación   │  │ │
│  │                              └──────────────────────────┘  │ │
│  └────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
                            │ SSE stream (text/event-stream)
                            │ Authorization: Bearer <JWT>
                            ▼
┌──────────────────────────────────────────────────────────────────┐
│                     Backend (FastAPI + Celery)                   │
│  ┌──────────┐    ┌────────────────────────────────┐              │
│  │ POST     │───▶│  SSE Manager (sse/manager.py) │               │
│  │ /upload  │    │  ┌─────────────────────────┐   │              │
│  │          │    │  │ broadcast(): Publica    │   │              │
│  │          │    │  │ evento a todas las colas│   │              │
│  │          │    │  │ Buffer circular (50 ev.)│   │              │
│  │          │    │  │ Replay por Last-Event-ID│   │              │
│  │          │    │  └─────────────────────────┘   │              │
│  │          │    └────────────────────────────────┘              │
│  │  publish │                                                    │
│  │  evento  │    ┌───────────────────────────────┐               │
│  │  sse     │    │ GET /rfp/events               │               │
│  ▼          │    │ StreamingResponse(async_gen())│               │
│  ┌──────────┐    │ JWT required                  │               │
│  │ Celery   │    │ Keep-alive cada 30s           │               │
│  │ pipeline │    └───────────────────────────────┘               │
│  └──────────┘                                                    │
└──────────────────────────────────────────────────────────────────┘
```

---

### 3. Componentes Implementados

#### 3.1 Backend — SSE Manager (`services/api/sse/manager.py`)

- `SSEManager`: singleton thread-safe con mapa de colas de suscriptores.
- `subscribe()`: registra un suscriptor y devuelve un `async generator` que escucha la cola.
- `unsubscribe()`: elimina el suscriptor cuando la conexión se cierra.
- `publish(event, data, event_id)`: encola el evento en todas las colas activas.
- Buffer circular configurable para replay (`max_history=50`).
- Keep-alive: envía un comentario `: keepalive` cada 30s en cada stream.

#### 3.2 Backend — Endpoint SSE (`GET /rfp/events`)

- Requiere JWT vía `Authorization: Bearer` (reutiliza `get_current_user` de `security.py`).
- Headers: `Content-Type: text/event-stream`, `Cache-Control: no-cache`, `Connection: keep-alive`, `X-Accel-Buffering: no`.
- Escucha `last_event_id` opcional del header `Last-Event-ID` para replay.
- Emite eventos nombrados:
  - `event: rfp_ticket_created` — cuando un nuevo ticket de RFP es registrado.
  - `event: kpi_threshold_alert` — cuando un KPI cruza umbral crítico.
- Keep-alive cada 30 segundos con `: keepalive\n\n`.

#### 3.3 Backend — Publicación de Eventos

- **`POST /rfp/upload`**: después de crear el ticket en DB, llama a `sse_manager.publish("rfp_ticket_created", payload)`.
- **`POST /rfp/kpi-alert`** (nuevo): endpoint que acepta alertas de umbral de KPI y las publica como `kpi_threshold_alert`. Diseñado para ser llamado por el pipeline de reporting cuando detecta condiciones críticas (no-show rate > 25%, denial rate > 10%).

#### 3.4 Frontend — Cliente SSE (`uis/backoffice/lib/sseClient.ts`)

- Usa `fetch()` con `Authorization: Bearer` en lugar de `EventSource`.
- Lee el cuerpo como `ReadableStream<Uint8Array>` y lo decodifica línea por línea.
- Parsea el framing SSE: `event:`, `data:`, `id:`.
- Reconexión con backoff exponencial progresivo: 1s → 2s → 4s → 8s → … → 30s máx.
- Envía `Last-Event-ID` al reconectar para replay.
- Ejecuta callback `onReconnected` para que el dashboard dispare el refetch de reconciliación.
- Notifica errores de conexión al dashboard via `onStatusChange`.

#### 3.5 Frontend — Dashboard RFP (`uis/backoffice/app/rfp/page.tsx`)

- Conecta al SSE stream al cargar la página.
- Muestra un **banner de notificación** en vivo cuando llega un `rfp_ticket_created`:
  - Distinto visualmente del resto de la tabla (fondo azul, borde izquierdo, icono de "NUEVO").
  - Persiste 10 segundos o hasta que el usuario lo descarta.
- Al reconectar, hace refetch de tickets y deduplica contra `Set<refresh>`.

#### 3.6 Segundo Evento: `kpi_threshold_alert`

Reutiliza el mismo endpoint SSE (`GET /rfp/events`) con un nuevo nombre de evento.

**Trigger:** Cuando el pipeline de reporting detecta que un KPI supera el umbral definido en CONTEXT.md:
- Tasa de no-show por ubicación > 25%
- Tasa de rechazo de facturación > 10%

**Payload:**
```json
{
  "location_id": "clinic-001",
  "location_name": "Central — Austin Main Clinic",
  "kpi_type": "no_show_rate",
  "kpi_value": 27.3,
  "threshold": 25.0,
  "unit": "%",
  "detected_at": "2026-11-27T14:32:00Z"
}
```

**Publicación:** Endpoint `POST /rfp/kpi-alert` (autenticado con JWT), diseñado para ser integrado con el pipeline de reporting mensual.

En el frontend, estos eventos se muestran como un banner distintivo (color ámbar/rojo según severidad) con el KPI y la ubicación.

---

### 4. Formato SSE

#### 4.1 `rfp_ticket_created`

```text
id: evt_0017_abc123
event: rfp_ticket_created
data: {"ticket_id":"tkt_0508","rfp_id":"rfp_0193","client_name":"Westbrook Manufacturing","client_country":"US","program_type":"occupational_health","status":"analizando","created_at":"2026-11-27T14:32:00Z"}

```

Campos en `data`: `ticket_id`, `rfp_id`, `client_name`, `client_country`, `program_type`, `status`, `created_at`. Coinciden exactamente con las entidades definidas en CONTEXT.md §2.

### 4.2 `kpi_threshold_alert`

```text
id: evt_0018_def456
event: kpi_threshold_alert
data: {"location_id":"clinic-001","location_name":"Central — Austin Main Clinic","kpi_type":"no_show_rate","kpi_value":27.3,"threshold":25.0,"unit":"%","detected_at":"2026-11-27T14:32:00Z"}

```

---

### 5. Estrategia de Reconexión y Recuperación

| Evento | Mecanismo |
|--------|-----------|
| Conexión inicial | `fetch("/api/rfp/events")` con token JWT |
| Desconexión abrupta | Backoff exponencial 1s→2s→4s...→30s max, reintento automático |
| Replay de eventos perdidos | Header `Last-Event-ID` en la nueva conexión → servidor replayea desde buffer circular |
| Buffer circular insuficiente (>50 eventos perdidos) | Refetch de `GET /rfp/tickets` + deduplicación contra `Set<ticket_id>` |
| Token expirado (401) | Cliente detecta, cierra SSE, notifica al usuario |
| Error de red prolongado | Backoff continúa indefinidamente; al recuperarse, replay + refetch |

---

### 6. Archivos Modificados/Creados

| Archivo | Acción | Descripción |
|---------|--------|-------------|
| `services/api/sse/__init__.py` | ✅ Creado | Init del paquete |
| `services/api/sse/manager.py` | ✅ Creado | SSEManager: colas, broadcast, buffer circular, keep-alive |
| `services/api/routes/rfp.py` | ✅ Modificado | Añadido `GET /rfp/events`, `POST /rfp/kpi-alert` + publicación SSE en `POST /upload` |
| `uis/backoffice/lib/sseClient.ts` | ✅ Creado | Cliente SSE con fetch + ReadableStream + backoff |
| `uis/backoffice/app/rfp/page.tsx` | ✅ Modificado | Banner de notificación en vivo + integración SSE |
| `tests/pipelines/test_sse_events.py` | ✅ Creado | Tests de headers, framing, auth, reconexión |

---

### 7. Limitaciones Conocidas

- **SSEManager en memoria:** Si el servidor FastAPI se reinicia, todas las conexiones SSE se pierden (los clientes reconectan automáticamente). El buffer circular también se pierde. Para un sistema crítico, migrar a Redis Pub/Sub.
- **Escalabilidad vertical:** El broadcast O(n) funciona para decenas de conexiones. Para cientos, considerar worker dedicado con Redis.
- **Sin compresión:** El payload SSE es pequeño (~200 bytes), no se justifica compresión. Si creciera, habilitar gzip a nivel de reverse proxy.

---

## Streaming de Chat por WebSocket — Parte 2

> **Proyecto:** Chat en tiempo real del Compliance Assistant (Claire Whitfield) — respuesta token a token vía WebSocket con interrupción de generación y recuperación de sesión.
> **Fecha:** 2026-11-27
> **Estado:** ✅ Implementado — 35 tests WebSocket, 16 tests SSE, 16 tests RFP, todos PASS

### 1. Preguntas de Diseño (Respondidas)

#### 1.1 ¿Por qué esta funcionalidad necesita WebSockets y no te alcanza con lo que construiste en la Parte 1?

**SSE no es suficiente porque la comunicación es unidireccional — servidor → cliente.**

La Parte 1 (SSE para notificaciones RFP) funciona porque:
- El servidor publica eventos (`rfp_ticket_created`, `kpi_threshold_alert`) y el cliente solo los consume.
- El cliente nunca necesita enviar datos al servidor por ese canal.

El chat del Compliance Assistant, en cambio, exige **bidireccionalidad**:
1. **El servidor envía tokens** conforme el LLM los genera (igual que SSE, pero no es el único flujo).
2. **El cliente envía un mensaje de interrupción** (`interrupt_requested`) mientras el servidor aún está generando, para abortar la respuesta en curso y redirigir al agente.
3. **El servidor confirma la interrupción** y arranca un nuevo turno — todo en la misma conexión persistente.

Con SSE no podríamos enviar la interrupción del cliente en el mismo canal. Habría que hacerlo vía un `POST` HTTP aparte, lo que añade complejidad, latencia y la pregunta de qué pasa si el POST llega después de que el servidor terminó de generar.

**WebSocket** resuelve esto de forma natural: ambos lados pueden enviar frames JSON en cualquier momento, sobre la misma conexión TCP, sin handshake HTTP adicional.

#### 1.2 Si más de un cliente está suscrito a la misma sesión de chat (por ejemplo, un supervisor observando la conversación en vivo), ¿cómo te aseguras de que todos reciban los mismos eventos sin duplicar las llamadas al agente?

**Arquitectura pub/sub con un Hub por sesión:**

```
                  ┌──────────────────────────────┐
                  │   ChatSessionPubSub Hub      │
                  │   (por sesión)               │
                  │                              │
                  │   canales:                   │
                  │     chat.<session_id>        │
                  │                              │
                  │   suscriptores: [ws1, ws2]   │
                  └──────┬───────────────────────┘
                         │ broadcast(token_chunk)
            ┌────────────┴────────────┐
            ▼                         ▼
        WebSocket 1              WebSocket 2
        (operador staff)          (supervisor compliance)
```

El `ChatSessionManager` mantiene un mapa de sesiones activas. Cada sesión tiene un `ChatSessionPubSub` con:
- **Un canal `chat.<session_id>`** al que se suscriben todas las conexiones WebSocket.
- **Un único producer** que ejecuta el agente LangGraph y publica eventos (tokens, completion, interruption) en el canal.
- **Broadcast O(n)** a todos los suscriptores del canal.

Esto asegura que:
- El agente se ejecuta **una sola vez**, sin importar cuántos clientes estén conectados.
- Todos los clientes ven exactamente los mismos eventos de agente (tokens, interrupciones, completado).
- Si un cliente se desconecta y reconecta, se re-suscribe al mismo canal y recibe todo desde ese momento.

#### 1.3 ¿Cómo separaste el abort del stream (parar tokens) de LangGraph HITL interrupt() (pausa de grafo), si usaste lo segundo? ¿Qué pasa con el mensaje parcial del asistente y con el siguiente turno?

No usé `interrupt()` de LangGraph HITL para el abort.

La separación es:

| Mecanismo | Propósito | Implementación |
|-----------|-----------|----------------|
| **Abort del stream** | Detener la generación de tokens en curso porque el usuario quiere redirigir | Se señaliza `asyncio.Event` de cancelación y se cancela la `asyncio.Task` que ejecuta `agent_graph.astream_events(version="v2")`. El generador asíncrono lanza `asyncio.CancelledError`, que se captura limpiamente. |
| **LangGraph interrupt()** | Pausar el grafo en un punto determinado para esperar una entrada humana (ej. aprobación de un departamento en RFP Parte 3) | No se usa en este flujo de chat. Si en el futuro el Compliance Assistant necesitara una pausa de grafo (esperar una confirmación), se añadiría como nodo `interrupt()` independiente del stream de tokens. |

**Flujo de abort:**

1. Cliente envía `{"event": "interrupt_requested", "data": {"new_input": "..."}}`.
2. Backend recibe el frame, **cancela la tarea** actual (`self._generation_task.cancel()`) y captura `asyncio.CancelledError`.
3. El mensaje parcial del asistente se marca como `status: "interrupted"` y se persiste en el historial de la sesión — los tokens ya mostrados **no se pierden**.
4. El backend envía `{"event": "generation_interrupted", "data": {"message_id": "...", "status": "interrupted"}}` al cliente.
5. El nuevo input del usuario se encola como un nuevo mensaje en el historial (`role: "user"`).
6. Se arranca **un nuevo turno** del asistente: se invoca de nuevo `agent_graph.astream_events(version="v2")` con el estado actualizado (que incluye el mensaje interrumpido como parte del historial y el nuevo input del usuario como pregunta).

### 2. Arquitectura del Sistema

```
┌──────────────────────────────────────────────────────────────────┐
│                     Frontend (Next.js)                           │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │  Compliance Chat (app/compliance/page.tsx)                  │ │
│  │  ┌──────────────────┐  ┌──────────────────────────────┐     │ │
│  │  │ Historial        │  │ WebSocket Client             │     │ │
│  │  │ de mensajes      │  │ (wsClient.ts)                │     │ │
│  │  │ con tokens       │  │ reconnect + backoff          │     │ │
│  │  │ en vivo          │  │ interrupt + rehidratación    │     │ │
│  │  └──────────────────┘  └──────────────────────────────┘     │ │
│  └─────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
                      │ WebSocket ws://host/ws/chat
                      │ ?token=JWT&session_id=chat_xxx
                      ▼
┌──────────────────────────────────────────────────────────────────┐
│                  Backend (FastAPI + WebSocket)                   │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │  WebSocket Handler (routes/chat_ws.py)                      │ │
│  │  ┌──────────────┐ ┌────────────────┐ ┌──────────────────┐   │ │
│  │  │ Auth (JWT    │ │ ChatSession    │ │ Streaming Agent  │   │ │
│  │  │ query string)│ │ Manager        │ │ (pub/sub)        │   │ │
│  │  │ + frame auth │ │ (session store)│ │                  │   │ │
│  │  └──────────────┘ └────────────────┘ └──────────────────┘   │ │
│  └─────────────────────────────────────────────────────────────┘ │
│              │                                                   │
│              ▼                                                   │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │  ChatSessionPubSub Hub (por sesión)                         │ │
│  │  Canal: chat.<session_id>                                   │ │
│  │  - 1 producer: tarea asyncio que ejecuta agent.astream      │ │
│  │  - N consumers: WebSockets suscritos al canal               │ │
│  └─────────────────────────────────────────────────────────────┘ │
│              │                                                   │
│              ▼                                                   │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │  Agente LangGraph (services/agent/graph.py)                 │ │
│  │  astream_events(version="v2") → token_chunk                 │ │
│  └─────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
```

### 3. Componentes Implementados

#### 3.1 Modelo de Datos — ChatSession (`services/agent/chat/models.py`)

```python
class ChatMessage(BaseModel):
    message_id: str          # msg_0162
    role: str                # "user" | "assistant" | "system"
    content: str             # Texto completo (para user) o parcial (para assistant)
    status: str              # "streaming" | "completed" | "interrupted"
    created_at: str          # ISO timestamp

class ChatSessionState(BaseModel):
    session_id: str          # chat_0076
    agent_id: str            # "compliance_assistant"
    user_id: str             # ID del staff (nunca paciente)
    status: str              # "active" | "interrupted" | "closed"
    messages: list[ChatMessage]
    current_message_id: str | None  # ID del mensaje que se está generando
    created_at: str
    updated_at: str
```

#### 3.2 ChatSessionPubSub — Hub por sesión (`services/agent/chat/pubsub.py`)

- `subscribe(websocket)`: registra un consumidor para recibir eventos broadcast.
- `unsubscribe(websocket)`: elimina un consumidor.
- `publish(event_type, data)`: envía el evento serializado a todos los suscriptores.
- Canal identificado por `chat.<session_id>`.

#### 3.3 ChatSessionManager — Gestor de sesiones (`services/agent/chat/manager.py`)

- Singleton (mismo patrón que `SSEManager` de la Parte 1).
- `get_or_create_session(session_id, user_id)`: crea o recupera una sesión.
- `handle_user_message(session_id, text)`: añade el mensaje del usuario al historial y arranca una tarea asyncio que ejecuta el agente LangGraph en modo streaming (vía `astream_events(version="v2")`) y publica tokens en el pub/sub.
- `handle_interrupt(session_id, new_input)`: cancela la tarea actual y arranca un nuevo turno.
- `get_snapshot(session_id)`: devuelve el historial completo de mensajes (para rehidratación).

#### 3.4 WebSocket Handler (`services/api/routes/chat_ws.py`)

- `GET /ws/chat`: endpoint WebSocket.
- **Handshake**: extrae `?token=` de query string, valida JWT con `jose.decode` y `JWT_SECRET_KEY`/`JWT_ALGORITHM`. Rechaza con código `WS_1008_POLICY_VIOLATION` antes de `accept()` si falta/es inválido.
- **Ligadura a sesión**: query param `session_id` para ligar el socket a un hilo de conversación. El mismo valor se usa como `thread_id` de LangGraph para habilitar rehidratación del checkpoint al reconectar.
- **Mensajes entrantes**:
  - `user_message`: encola un nuevo mensaje de usuario y arranca generación.
  - `interrupt_requested`: cancela generación actual, marca mensaje parcial como `interrupted`, arranca nuevo turno.
- **Eventos salientes** (formato JSON, misma disciplina de nombres que el BRIEFING):
  - `token_chunk`: un token del asistente.
  - `generation_completed`: fin de generación exitosa.
  - `generation_interrupted`: generación abortada por usuario.
  - `session_snapshot`: historial completo para rehidratación.
  - `error`: error del servidor.

#### 3.5 Frontend — WebSocket Client (`uis/backoffice/lib/wsClient.ts`)

- Clase `WSClient` con:
  - `connect()`: establece conexión WebSocket. El `session_id` y `token` (opcional, lee de localStorage si se omite) se pasan en el constructor y se codifican en la URL `ws://host/ws/chat?token=...&session_id=...`.
  - `sendUserMessage(text)`: envía `{"event": "user_message", "data": {"session_id": ..., "text": ...}}`.
  - `sendInterrupt(new_input)`: envía `{"event": "interrupt_requested", "data": {"session_id": ..., "new_input": ...}}`.
  - `disconnect()`: cierra la conexión.
  - Callbacks: `onToken`, `onCompleted`, `onInterrupted`, `onSnapshot`, `onError`, `onStatusChange`.
- **Reconexión**: backoff exponencial 1s→2s→...→30s. Al reconectar, envía el mismo `session_id` y recibe `session_snapshot` para rehidratar el historial antes de aceptar nuevos tokens.

#### 3.6 Frontend — Compliance Chat UI (`uis/backoffice/app/compliance/page.tsx`)

- Página protegida con AuthGuard.
- Input de texto + botón de enviar.
- Historial de mensajes con:
  - Mensajes del usuario (alineados a la derecha).
  - Mensajes del asistente (alineados a la izquierda) con tokens renderizados en vivo.
  - Mensajes interrumpidos marcados con badge "⏹ Interrumpido" y contenido parcial visible.
- Botón de interrupción: al enviar un nuevo mensaje mientras el agente está respondiendo, se dispara `interrupt_requested` automáticamente.
- Conexión indicador verde/rojo (conectado/reconectando).

### 4. Eventos WebSocket

| Evento | Dirección | Descripción |
|--------|-----------|-------------|
| `user_message` | Cliente → Servidor | Nuevo mensaje del usuario. `data: {session_id, text}` |
| `interrupt_requested` | Cliente → Servidor | Solicitud de interrupción. `data: {session_id, new_input}` |
| `token_chunk` | Servidor → Cliente | Token del asistente. `data: {session_id, token, sequence, message_id}` |
| `generation_completed` | Servidor → Cliente | Generación terminada. `data: {session_id, message_id}` |
| `generation_interrupted` | Servidor → Cliente | Generación abortada. `data: {session_id, message_id, status: "interrupted"}` |
| `session_snapshot` | Servidor → Cliente | Historial completo. `data: {session_id, messages: [...]}` |
| `error` | Servidor → Cliente | Error. `data: {session_id, message}` |

### 5. Estrategia de Reconexión y Recuperación

| Evento | Mecanismo |
|--------|-----------|
| Conexión inicial | `ws://host/ws/chat?token=JWT&session_id=chat_xxx` |
| Desconexión abrupta | Backoff exponencial 1s→2s→4s...→30s max, reconexión automática |
| Rehidratación | Al reconectar, servidor envía `session_snapshot` con el historial completo. El `thread_id` de LangGraph es el mismo `session_id`, lo que permite reanudar checkpoints al reconectar |
| Interrupción | Cliente envía `interrupt_requested`, servidor cancela tarea y arranca nuevo turno |
| Error de red prolongado | Backoff continúa; al reconectar, rehidratación completa |

### 6. Archivos Creados/Modificados

| Archivo | Acción | Descripción |
|---------|--------|-------------|
| `services/agent/chat/__init__.py` | ✅ Creado | Init del paquete chat |
| `services/agent/chat/models.py` | ✅ Creado | Modelos ChatMessage, ChatSessionState, payloads de eventos |
| `services/agent/chat/pubsub.py` | ✅ Creado | ChatSessionPubSub — hub pub/sub por sesión |
| `services/agent/chat/manager.py` | ✅ Creado | ChatSessionManager — singleton gestor de sesiones con streaming agent |
| `services/api/routes/chat_ws.py` | ✅ Creado | Endpoint WebSocket `/ws/chat` con JWT auth y streaming |
| `services/api/routes/__init__.py` | ✅ Modificado | Registro del router chat_ws |
| `services/api/main.py` | ✅ Modificado | Montaje del WebSocket endpoint |
| `uis/backoffice/lib/wsClient.ts` | ✅ Creado | Cliente WebSocket con backoff, interrupt, rehidratación |
| `uis/backoffice/app/compliance/page.tsx` | ✅ Creado | Página de chat compliance con tokens en vivo |
| `uis/backoffice/components/BackofficeHeader.tsx` | ✅ Modificado | Enlace a Compliance Chat |
| `tests/pipelines/test_chat_websocket.py` | ✅ Creado | Tests de contrato WebSocket, interrupción, reconexión |

### 7. Limitaciones Conocidas

- **ChatSessionManager en memoria:** Si el servidor se reinicia, las sesiones activas se pierden. Para un sistema crítico, migrar a Redis.
- **Escalabilidad:** El broadcast O(n) funciona para pocos suscriptores por sesión (supervisor + staff). Para decenas de observadores, considerar Redis Pub/Sub.
- **Sin persistencia de historial:** El historial de mensajes vive en memoria dentro de `ChatSessionState`. Para producción, persistir en base de datos (PostgreSQL).
