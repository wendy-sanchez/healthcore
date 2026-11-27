"use client";

import React, { useState, useRef, useCallback } from "react";
import { useWsChat, type WsChatMessage } from "@/lib/wsClient";

/**
 * ComplianceChatPage — Chat en tiempo real con el Compliance Assistant.
 *
 * Características:
 *   - Conexión WebSocket con autenticación JWT y reconexión automática
 *   - Renderizado de tokens en vivo (efecto de escritura)
 *   - Interrupción de generación en curso al enviar nuevo mensaje
 *   - Historial de mensajes con scroll automático
 *   - Indicador de estado de conexión
 *   - Mensajes interrumpidos marcados con badge y contenido parcial visible
 *
 * Esta página está protegida por AuthGuard (ver layout.tsx).
 */

// ── Tipos internos ─────────────────────────────────────────────────────

interface DisplayMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  status: "completed" | "streaming" | "interrupted";
}

// ── IDs de sesión persistentes ─────────────────────────────────────────

function getSessionId(): string {
  if (typeof window === "undefined") return `chat_${Date.now()}`;
  const stored = localStorage.getItem("compliance_chat_session_id");
  if (stored) return stored;
  const newId = `chat_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
  localStorage.setItem("compliance_chat_session_id", newId);
  return newId;
}

// ── Componente principal ───────────────────────────────────────────────

export default function ComplianceChatPage() {
  const [messages, setMessages] = useState<DisplayMessage[]>([]);
  const [input, setInput] = useState("");
  const [streamingMessageId, setStreamingMessageId] = useState<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Callbacks del WebSocket
  const onToken = useCallback((token: string, _sequence: number, messageId: string) => {
    setMessages((prev) => {
      // Buscar si ya existe el mensaje streaming
      const existing = prev.find((m) => m.id === messageId);
      if (existing) {
        return prev.map((m) =>
          m.id === messageId
            ? { ...m, content: m.content + token }
            : m
        );
      }
      // Nuevo mensaje del asistente (primer token)
      return [
        ...prev,
        {
          id: messageId,
          role: "assistant",
          content: token,
          status: "streaming",
        },
      ];
    });
    setStreamingMessageId(messageId);
  }, []);

  const onCompleted = useCallback((messageId: string) => {
    setMessages((prev) =>
      prev.map((m) =>
        m.id === messageId ? { ...m, status: "completed" } : m
      )
    );
    setStreamingMessageId(null);
    // Scroll al final
    setTimeout(() => messagesEndRef.current?.scrollIntoView({ behavior: "smooth" }), 100);
  }, []);

  const onInterrupted = useCallback((messageId: string) => {
    setMessages((prev) =>
      prev.map((m) =>
        m.id === messageId ? { ...m, status: "interrupted" } : m
      )
    );
    setStreamingMessageId(null);
  }, []);

  const onSnapshot = useCallback((wsMessages: WsChatMessage[]) => {
    const display: DisplayMessage[] = wsMessages.map((m) => ({
      id: m.message_id,
      role: m.role as "user" | "assistant",
      content: m.content,
      status: m.status as "completed" | "streaming" | "interrupted",
    }));
    setMessages(display);
    // Si hay un mensaje en streaming, trackearlo
    const streaming = display.find((m) => m.status === "streaming");
    setStreamingMessageId(streaming?.id ?? null);
    // Scroll al final
    setTimeout(() => messagesEndRef.current?.scrollIntoView({ behavior: "smooth" }), 100);
  }, []);

  const onWsError = useCallback((message: string) => {
    console.error("WebSocket error:", message);
  }, []);

  // Hook del WebSocket
  const { connected, sendMessage, sendInterrupt } = useWsChat({
    sessionId: getSessionId(),
    autoConnect: true,
    onToken,
    onCompleted,
    onInterrupted,
    onSnapshot,
    onError: onWsError,
  });

  // Enviar mensaje
  function handleSend() {
    const text = input.trim();
    if (!text) return;

    setInput("");

    // Si hay streaming en curso, interrumpir primero
    if (streamingMessageId) {
      // Marcar el mensaje anterior como interrumpido localmente
      setMessages((prev) =>
        prev.map((m) =>
          m.id === streamingMessageId ? { ...m, status: "interrupted" } : m
        )
      );
      setStreamingMessageId(null);
      // Enviar interrupción + nuevo mensaje
      sendInterrupt(text);
    } else {
      // Solo enviar mensaje
      sendMessage(text);
    }

    // Añadir mensaje del usuario al estado local inmediatamente
    const userMsgId = `user_${Date.now()}`;
    setMessages((prev) => [
      ...prev,
      { id: userMsgId, role: "user", content: text, status: "completed" },
    ]);
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }

  return (
    <div className="mx-auto flex max-w-4xl flex-col px-4 py-6 sm:px-6 lg:px-8">
      {/* Header */}
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-slate-800 dark:text-white">
          Compliance Assistant
        </h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Asistente de cumplimiento de HealthCore — consulta políticas,
          procedimientos y requisitos regulatorios.
        </p>
        <div className="mt-2 flex items-center gap-2">
          <span
            className={`inline-block h-2.5 w-2.5 rounded-full ${
              connected ? "bg-green-500" : "bg-red-500"
            }`}
          />
          <span className="text-xs text-slate-500">
            {connected ? "Conectado" : "Reconectando..."}
          </span>
        </div>
      </div>

      {/* Mensajes */}
      <div className="mb-4 flex-1 space-y-4 overflow-y-auto rounded-lg border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-700 dark:bg-slate-900 min-h-[400px] max-h-[600px]">
        {messages.length === 0 && (
          <div className="flex h-full items-center justify-center text-center text-sm text-slate-400">
            <div>
              <p className="text-lg font-medium text-slate-300">Bienvenido al Compliance Assistant</p>
              <p className="mt-1">Haz una pregunta sobre políticas, procedimientos o requisitos de HealthCore.</p>
            </div>
          </div>
        )}

        {messages.map((msg) => (
          <div
            key={msg.id}
            className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
          >
            <div
              className={`max-w-[80%] rounded-lg px-4 py-2 ${
                msg.role === "user"
                  ? "bg-blue-600 text-white"
                  : "bg-slate-100 text-slate-800 dark:bg-slate-800 dark:text-slate-100"
              }`}
            >
              {msg.role === "assistant" && msg.status === "interrupted" && (
                <span className="mb-1 inline-block rounded bg-yellow-200 px-2 py-0.5 text-xs font-semibold text-yellow-800 dark:bg-yellow-900 dark:text-yellow-200">
                  ⏹ Interrumpido
                </span>
              )}
              <div className="whitespace-pre-wrap text-sm leading-relaxed">
                {msg.content}
                {msg.status === "streaming" && (
                  <span className="ml-0.5 inline-block h-4 w-2 animate-pulse bg-blue-500" />
                )}
              </div>
            </div>
          </div>
        ))}
        <div ref={messagesEndRef} />
      </div>

      {/* Input */}
      <div className="flex gap-2">
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={
            streamingMessageId
              ? "Escribe para interrumpir y redirigir al asistente..."
              : "Escribe tu consulta sobre compliance..."
          }
          rows={2}
          className="flex-1 resize-none rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm text-slate-800 placeholder-slate-400 focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-200 dark:border-slate-600 dark:bg-slate-800 dark:text-white dark:placeholder-slate-500"
        />
        <button
          type="button"
          onClick={handleSend}
          disabled={!input.trim()}
          className="self-end rounded-lg bg-blue-600 px-6 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {streamingMessageId ? "↳ Redirigir" : "Enviar"}
        </button>
      </div>

      {/* Footer */}
      <p className="mt-2 text-center text-[10px] text-slate-400">
        Este asistente proporciona información general sobre políticas de HealthCore.
        No sustituye el consejo legal o regulatorio profesional.
      </p>
    </div>
  );
}