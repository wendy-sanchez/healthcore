/**
 * Cliente SSE (Server-Sent Events) para HealthCore.
 *
 * Usa `fetch()` + `ReadableStream` en lugar de `EventSource` porque
 * necesitamos enviar el header `Authorization: Bearer <JWT>` para
 * autenticación, y `EventSource` no soporta cabeceras personalizadas.
 *
 * Características:
 *   - Parsing manual del framing SSE (event:, data:, id:)
 *   - Reconexión con backoff exponencial progresivo (1s → 2s → 4s → ... → 30s max)
 *   - Envío de `Last-Event-ID` en reconexión para replay de eventos perdidos
 *   - Callback onReconnected() para que el dashboard dispare refetch de reconciliación
 *   - Callback onStatusChange() para notificar conectado/desconectado
 *   - Deduplicación interna contra Set<eventId> (via skipDuplicates)
 *
 * @example
 * ```ts
 * import { SseClient } from "@/lib/sseClient";
 *
 * const client = new SseClient({
 *   url: "/api/rfp/events",
 *   onEvent: (eventName, data) => {
 *     if (eventName === "rfp_ticket_created") {
 *       showNotification(data);
 *     }
 *   },
 *   onReconnected: () => refetchTickets(),
 * });
 * client.connect();
 * ```
 */

import { getToken } from "@/lib/auth";

// ── Tipos ─────────────────────────────────────────────────────────────

export interface SseClientOptions {
  /** URL del endpoint SSE (ej. "/api/rfp/events") */
  url: string;
  /** Token JWT opcional. Si se omite, se lee de localStorage via getToken() */
  token?: string;
  /** Callback por cada evento SSE recibido */
  onEvent: (eventName: string, data: Record<string, unknown>, eventId: string) => void;
  /** Callback cuando la conexión se restablece tras una desconexión */
  onReconnected?: () => void;
  /** Callback cuando el estado de la conexión cambia */
  onStatusChange?: (connected: boolean, error?: string) => void;
  /** Si true, omite eventos cuyo eventId ya haya sido procesado */
  skipDuplicates?: boolean;
  /** Timeout máximo de backoff en segundos (default: 30) */
  maxBackoffSeconds?: number;
}

interface InternalOptions {
  url: string;
  token: string;
  onEvent: (eventName: string, data: Record<string, unknown>, eventId: string) => void;
  onReconnected: () => void;
  onStatusChange: (connected: boolean, error?: string) => void;
  skipDuplicates: boolean;
  maxBackoffSeconds: number;
}

// ── Cliente SSE ────────────────────────────────────────────────────────

export class SseClient {
  private options: InternalOptions;
  private controller: AbortController | null = null;
  private connected = false;
  private lastEventId: string | null = null;
  private seenEventIds: Set<string> = new Set();
  private retryDelayMs = 1000; // empieza en 1s
  private active = false;

  constructor(options: SseClientOptions) {
    this.options = {
      url: options.url,
      token: options.token ?? "",
      onEvent: options.onEvent,
      onReconnected: options.onReconnected ?? (() => {}),
      onStatusChange: options.onStatusChange ?? (() => {}),
      skipDuplicates: options.skipDuplicates ?? true,
      maxBackoffSeconds: options.maxBackoffSeconds ?? 30,
    };
  }

  /** Inicia la conexión SSE. No-reentrante: si ya está activo, es no-op. */
  connect(): void {
    if (this.active) return;
    this.active = true;
    this.lastEventId = null;
    this.seenEventIds = new Set();
    this.retryDelayMs = 1000;
    void this._doConnect();
  }

  /** Desconexión gracefully. */
  disconnect(): void {
    this.active = false;
    this.controller?.abort();
    this.controller = null;
    this._setConnected(false, "disconnected");
  }

  /** ¿Está actualmente conectado? */
  get isConnected(): boolean {
    return this.connected;
  }

  /** Limpia el historial de event IDs vistos (útil tras refetch). */
  clearSeen(): void {
    this.seenEventIds = new Set();
  }

  // ── Privado ──────────────────────────────────────────────────────────

  private _setConnected(value: boolean, error?: string): void {
    if (this.connected === value) return;
    this.connected = value;
    this.options.onStatusChange(value, error);
  }

  private async _doConnect(): Promise<void> {
    if (!this.active) return;

    const token = this.options.token || getToken();
    if (!token) {
      this.options.onStatusChange(false, "No token available");
      this.active = false;
      return;
    }

    this.controller = new AbortController();
    const headers: Record<string, string> = {
      Authorization: `Bearer ${token}`,
      Accept: "text/event-stream",
    };
    if (this.lastEventId) {
      headers["Last-Event-ID"] = this.lastEventId;
    }

    try {
      const response = await fetch(this.options.url, {
        method: "GET",
        headers,
        signal: this.controller.signal,
      });

      // 401 = token inválido/expirado → no reintentar
      if (response.status === 401) {
        this._setConnected(false, "Unauthorized — token expired");
        this.active = false;
        this.options.onStatusChange(false, "Token expirado. Recarga la página.");
        return;
      }

      // Otros errores HTTP → reintentar con backoff
      if (!response.ok) {
        throw new Error(`HTTP ${response.status} ${response.statusText}`);
      }

      if (!response.body) {
        throw new Error("Response body is null");
      }

      // Conectado exitosamente
      const wasDisconnected = !this.connected;
      this._setConnected(true);
      if (wasDisconnected && this.lastEventId) {
        this.options.onReconnected();
      }

      // Resetear backoff tras conexión exitosa
      this.retryDelayMs = 1000;

      // Leer el stream
      const reader = response.body.getReader();
      let buffer = "";

      while (this.active) {
        const { done, value } = await reader.read();
        if (done) break;

        // Decodificar Uint8Array a string UTF-8
        buffer += decodeUtf8(value);
        buffer = this._processBuffer(buffer);
      }
    } catch (err: unknown) {
      const error = err as Error;
      if (error.name === "AbortError") {
        return;
      }

      const errorMsg = error.message || String(err);
      this._setConnected(false, errorMsg);

      if (this.active) {
        const delay = Math.min(
          this.retryDelayMs,
          this.options.maxBackoffSeconds * 1000,
        );
        this.retryDelayMs = Math.min(
          this.retryDelayMs * 2,
          this.options.maxBackoffSeconds * 1000,
        );

        setTimeout(() => void this._doConnect(), delay);
      }
    }
  }

  /** Procesa el buffer de texto, partiendo por doble salto de línea (fin de evento SSE). */
  private _processBuffer(buffer: string): string {
    const parts = buffer.split("\n\n");
    const complete = parts.slice(0, -1);
    const remaining = parts[parts.length - 1];

    for (const block of complete) {
      this._parseEventBlock(block.trim());
    }

    return remaining;
  }

  /** Parsea un bloque SSE individual (event:, data:, id:). */
  private _parseEventBlock(block: string): void {
    let eventName = "message";
    let dataStr: string | null = null;
    let eventId: string | null = null;

    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) {
        eventName = line.substring(6).trim();
      } else if (line.startsWith("data:")) {
        dataStr = line.substring(5).trim();
      } else if (line.startsWith("id:")) {
        eventId = line.substring(3).trim();
      }
    }

    // Ignorar comentarios (keep-alive) / líneas vacías
    if (dataStr === null) return;

    // Parsear data como JSON
    let data: Record<string, unknown>;
    try {
      data = JSON.parse(dataStr) as Record<string, unknown>;
    } catch {
      return;
    }

    const finalEventId = eventId || "";

    // Deduplicación
    if (this.options.skipDuplicates && finalEventId) {
      if (this.seenEventIds.has(finalEventId)) return;
      this.seenEventIds.add(finalEventId);
    }

    // Actualizar lastEventId para replay en reconexión
    if (finalEventId) {
      this.lastEventId = finalEventId;
    }

    this.options.onEvent(eventName, data, finalEventId);
  }
}

// ── Ayudante: decodificación de bytes a UTF-8 string ──────────────────

function decodeUtf8(bytes: Uint8Array): string {
  try {
    return new TextDecoder("utf-8").decode(bytes);
  } catch {
    const chars: string[] = new Array(bytes.length);
    for (let i = 0; i < bytes.length; i++) {
      chars[i] = String.fromCharCode(bytes[i]);
    }
    return chars.join("");
  }
}