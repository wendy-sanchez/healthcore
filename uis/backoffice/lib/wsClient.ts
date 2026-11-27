/**
 * Cliente WebSocket para el chat en tiempo real del Compliance Assistant.
 *
 * Conecta al endpoint /ws/chat del backend con autenticación JWT
 * y soporta:
 *   - Reconexión con backoff exponencial progresivo (1s → 2s → ... → 30s max)
 *   - Rehidratación del historial via session_snapshot al reconectar
 *   - Envío de mensajes de usuario (user_message)
 *   - Interrupción de generación en curso (interrupt_requested)
 *   - Tokens en vivo (token_chunk → callback onToken)
 *   - Detección de completado e interrupción
 *
 * @example
 * ```ts
 * const client = new WSClient({
 *   sessionId: "chat_0076",
 *   token: "eyJ...",
 *   onToken: (token, msgId) => appendToMessage(msgId, token),
 *   onCompleted: (msgId) => finalizeMessage(msgId),
 *   onConnected: () => setConnected(true),
 * });
 * client.connect();
 * ```
 */

import { getToken } from "@/lib/auth";

// ── Tipos ─────────────────────────────────────────────────────────────

export interface WsChatMessage {
  message_id: string;
  role: string;
  content: string;
  status: string;
  created_at: string;
}

export interface WsClientOptions {
  /** Identificador único de la sesión de chat (ej. "chat_0076") */
  sessionId: string;
  /** Token JWT opcional. Si se omite, se lee de localStorage via getToken() */
  token?: string;
  /** Callback por cada token recibido */
  onToken?: (token: string, sequence: number, messageId: string) => void;
  /** Callback cuando la generación se completa */
  onCompleted?: (messageId: string) => void;
  /** Callback cuando la generación se interrumpe */
  onInterrupted?: (messageId: string) => void;
  /** Callback con el snapshot del historial al conectar/reconectar */
  onSnapshot?: (messages: WsChatMessage[]) => void;
  /** Callback cuando ocurre un error */
  onError?: (message: string) => void;
  /** Callback cuando el estado de la conexión cambia */
  onStatusChange?: (connected: boolean) => void;
  /** Timeout máximo de backoff en segundos (default: 30) */
  maxBackoff?: number;
  /** URL base del WebSocket (default: auto-detecta de window.location) */
  wsBaseUrl?: string;
}

// ── Constantes ─────────────────────────────────────────────────────────

const INITIAL_BACKOFF = 1_000; // 1s
const MAX_BACKOFF_DEFAULT = 30_000; // 30s
const BACKOFF_MULTIPLIER = 2;

// ── Cliente WebSocket ──────────────────────────────────────────────────

export class WSClient {
  private ws: WebSocket | null = null;
  private options: WsClientOptions;
  private backoff = INITIAL_BACKOFF;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private intentionalClose = false;
  private connected = false;

  constructor(options: WsClientOptions) {
    this.options = options;
  }

  /** Conecta al WebSocket. */
  connect(): void {
    this.intentionalClose = false;
    this._doConnect();
  }

  /** Desconecta intencionalmente. */
  disconnect(): void {
    this.intentionalClose = true;
    this._cleanup();
  }

  /** Envía un mensaje de usuario al agente. */
  sendUserMessage(text: string): void {
    this._send("user_message", { text });
  }

  /** Envía una interrupción con redirección. */
  sendInterrupt(newInput: string): void {
    this._send("interrupt_requested", { new_input: newInput });
  }

  /** Retorna true si está conectado. */
  isConnected(): boolean {
    return this.connected;
  }

  // ── Privados ───────────────────────────────────────────────────────

  private _doConnect(): void {
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
      return; // Ya conectado o conectando
    }

    const { sessionId, wsBaseUrl } = this.options;
    const token = this.options.token ?? getToken() ?? "";
    const baseUrl = wsBaseUrl ?? this._getDefaultWsBase();
    const url = `${baseUrl}/ws/chat?token=${encodeURIComponent(token)}&session_id=${encodeURIComponent(sessionId)}`;

    this.ws = new WebSocket(url);
    this.ws.onopen = () => this._onOpen();
    this.ws.onclose = (event) => this._onClose(event);
    this.ws.onmessage = (event) => this._onMessage(event);
    this.ws.onerror = () => this._onError();
  }

  private _getDefaultWsBase(): string {
    if (typeof window === "undefined") return "ws://localhost:8000";
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const host = window.location.hostname;
    // En desarrollo, Next.js corre en puerto distinto al backend
    if (host === "localhost" || host === "127.0.0.1") {
      return `${protocol}//localhost:8000`;
    }
    return `${protocol}//${window.location.host}`;
  }

  private _onOpen(): void {
    this.connected = true;
    this.backoff = INITIAL_BACKOFF; // Reset backoff on successful connection
    this.options.onStatusChange?.(true);
  }

  private _onClose(event: CloseEvent): void {
    this.connected = false;
    this.ws = null;
    this.options.onStatusChange?.(false);

    if (this.intentionalClose) {
      return; // No reconectar si fue intencional
    }

    // Reconectar con backoff exponencial
    const maxBackoff = this.options.maxBackoff ?? MAX_BACKOFF_DEFAULT;
    this.reconnectTimer = setTimeout(() => {
      this._doConnect();
    }, this.backoff);
    this.backoff = Math.min(this.backoff * BACKOFF_MULTIPLIER, maxBackoff);
  }

  private _onMessage(event: MessageEvent): void {
    try {
      const msg = JSON.parse(event.data);
      const { event: eventName, data } = msg;

      switch (eventName) {
        case "token_chunk":
          this.options.onToken?.(data.token, data.sequence, data.message_id);
          break;
        case "generation_completed":
          this.options.onCompleted?.(data.message_id);
          break;
        case "generation_interrupted":
          this.options.onInterrupted?.(data.message_id);
          break;
        case "session_snapshot":
          if (data.messages) {
            this.options.onSnapshot?.(data.messages);
          }
          break;
        case "error":
          this.options.onError?.(data.message);
          break;
      }
    } catch {
      // Ignorar mensajes malformados
    }
  }

  private _onError(): void {
    // onclose se dispara después de onerror, manejamos la reconexión allí
  }

  private _send(event: string, data: Record<string, unknown>): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ event, data }));
    }
  }

  private _cleanup(): void {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      this.ws.onclose = null; // Evitar que onclose dispare reconexión
      this.ws.onmessage = null;
      this.ws.onerror = null;
      this.ws.close();
      this.ws = null;
    }
    this.connected = false;
    this.backoff = INITIAL_BACKOFF;
  }
}

// ── Hook React ─────────────────────────────────────────────────────────

import { useCallback, useEffect, useRef, useState } from "react";

export interface UseWsChatOptions {
  sessionId?: string;
  /** Si true, conecta automáticamente al montar el componente */
  autoConnect?: boolean;
  onToken?: (token: string, sequence: number, messageId: string) => void;
  onCompleted?: (messageId: string) => void;
  onInterrupted?: (messageId: string) => void;
  onSnapshot?: (messages: WsChatMessage[]) => void;
  onError?: (message: string) => void;
}

export interface UseWsChatReturn {
  connected: boolean;
  connect: () => void;
  disconnect: () => void;
  sendMessage: (text: string) => void;
  sendInterrupt: (newInput: string) => void;
}

export function useWsChat(options: UseWsChatOptions): UseWsChatReturn {
  const {
    sessionId = `chat_${Math.random().toString(36).slice(2, 10)}`,
    autoConnect = true,
    onToken,
    onCompleted,
    onInterrupted,
    onSnapshot,
    onError,
  } = options;

  const clientRef = useRef<WSClient | null>(null);
  const [connected, setConnected] = useState(false);

  const connect = useCallback(() => {
    if (clientRef.current) {
      clientRef.current.disconnect();
    }

    const client = new WSClient({
      sessionId,
      onToken,
      onCompleted,
      onInterrupted,
      onSnapshot,
      onError,
      onStatusChange: (c) => setConnected(c),
    });

    clientRef.current = client;
    client.connect();
  }, [sessionId, onToken, onCompleted, onInterrupted, onSnapshot, onError]);

  const disconnect = useCallback(() => {
    clientRef.current?.disconnect();
    clientRef.current = null;
  }, []);

  const sendMessage = useCallback((text: string) => {
    clientRef.current?.sendUserMessage(text);
  }, []);

  const sendInterrupt = useCallback((newInput: string) => {
    clientRef.current?.sendInterrupt(newInput);
  }, []);

  useEffect(() => {
    if (autoConnect) {
      connect();
    }
    return () => {
      disconnect();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoConnect]);

  return { connected, connect, disconnect, sendMessage, sendInterrupt };
}