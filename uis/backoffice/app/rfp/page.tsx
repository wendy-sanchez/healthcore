"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { request } from "@/lib/httpClient";
import { SseClient } from "@/lib/sseClient";

// ── Types ──────────────────────────────────────────────────────────

interface TicketListItem {
  ticket_id: string;
  status: string;
  client_name: string | null;
  created_at: string;
  updated_at: string;
  classification_reason: string | null;
  error_detail: string | null;
}

interface ApiResponse {
  tickets: TicketListItem[];
}

interface SseNotification {
  ticket_id: string;
  rfp_id?: string;
  client_name?: string | null;
  client_country?: string | null;
  program_type?: string | null;
  status?: string;
  created_at?: string;
}

const STATUS_LABELS: Record<string, string> = {
  analizando: "Analizando",
  analisis_completo: "Análisis completo",
  generando_borrador: "Generando borrador",
  en_evaluacion: "En evaluación",
  esperando_aprobacion: "Esperando aprobación",
  parcialmente_aprobado: "Parcialmente aprobado",
  produciendo: "Produciendo",
  terminado: "Terminado",
  descartado: "Descartado",
  error: "Error",
};

const STATUS_COLORS: Record<string, string> = {
  analizando: "bg-yellow-100 text-yellow-800 border-yellow-300",
  analisis_completo: "bg-green-100 text-green-800 border-green-300",
  generando_borrador: "bg-blue-100 text-blue-800 border-blue-300",
  en_evaluacion: "bg-purple-100 text-purple-800 border-purple-300",
  esperando_aprobacion: "bg-indigo-100 text-indigo-800 border-indigo-300",
  parcialmente_aprobado: "bg-orange-100 text-orange-800 border-orange-300",
  produciendo: "bg-teal-100 text-teal-800 border-teal-300",
  terminado: "bg-green-200 text-green-800 border-green-400",
  descartado: "bg-red-100 text-red-800 border-red-300",
  error: "bg-red-100 text-red-800 border-red-300",
};

// ── Component ───────────────────────────────────────────────────────

export default function RfpListPage() {
  const [tickets, setTickets] = useState<TicketListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [notifications, setNotifications] = useState<SseNotification[]>([]);
  const [sseConnected, setSseConnected] = useState(false);
  const sseClientRef = useRef<SseClient | null>(null);

  // ── Notificación entrante (banner) ───────────────────────────────

  const addNotification = useCallback((notif: SseNotification) => {
    setNotifications((prev) => [notif, ...prev].slice(0, 5)); // max 5 banners

    // Auto-descartar después de 10 segundos
    setTimeout(() => {
      setNotifications((prev) => prev.filter((n) => n.ticket_id !== notif.ticket_id));
    }, 10000);
  }, []);

  // ── Fetch tickets (full refresh) ─────────────────────────────────

  const fetchTickets = useCallback(async () => {
    try {
      const data = await request<ApiResponse>("/api/rfp/tickets");
      setTickets(data.tickets);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error al cargar tickets");
    } finally {
      setLoading(false);
    }
  }, []);

  // ── Inicialización ───────────────────────────────────────────────

  useEffect(() => {
    // 1. Fetch inicial de tickets
    fetchTickets();

    // 2. Conectar SSE para notificaciones en vivo
    const sse = new SseClient({
      url: "/api/rfp/events",
      onEvent: (eventName: string, data: Record<string, unknown>) => {
        if (eventName === "rfp_ticket_created") {
          const notif = data as unknown as SseNotification;
          addNotification(notif);
          // Refrescar la lista para que aparezca en la tabla
          void fetchTickets();
        } else if (eventName === "kpi_threshold_alert") {
          // Las alertas KPI se manejan en el dashboard principal
          addNotification({
            ticket_id: `kpi-${data.location_id}-${Date.now()}`,
            client_name: data.location_name as string,
            status: data.kpi_type as string,
          });
        }
      },
      onReconnected: () => {
        // Reconexión: refetch completo para reconciliación
        void fetchTickets();
      },
      onStatusChange: (connected: boolean) => {
        setSseConnected(connected);
      },
    });

    sse.connect();
    sseClientRef.current = sse;

    return () => {
      sse.disconnect();
      sseClientRef.current = null;
    };
  }, [fetchTickets, addNotification]);

  // ── Upload handler ───────────────────────────────────────────────

  const handleUpload = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setUploadError(null);

    const formData = new FormData(e.currentTarget);
    const file = formData.get("file") as File;

    if (!file || file.size === 0) {
      setUploadError("Selecciona un archivo PDF para subir.");
      return;
    }

    if (!file.name.toLowerCase().endsWith(".pdf")) {
      setUploadError("Solo se aceptan archivos PDF.");
      return;
    }

    setUploading(true);
    try {
      const form = new FormData();
      form.append("file", file);

      const res = await fetch("/api/rfp/upload", {
        method: "POST",
        headers: {
          Authorization: `Bearer ${localStorage.getItem("token") || ""}`,
        },
        body: form,
      });

      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || `Error ${res.status}`);
      }

      // Refresh list and start polling
      await fetchTickets();
      (e.target as HTMLFormElement).reset();
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "Error al subir archivo");
    } finally {
      setUploading(false);
    }
  };

  // ── Render ───────────────────────────────────────────────────────

  return (
    <main className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6 lg:px-8">
      <section className="mb-8">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-slate-900">
              RFP Intake — Recepción de Propuestas
            </h1>
            <p className="mt-1 text-sm text-slate-600">
              Sube una RFP en PDF para que el pipeline agéntico la analice y distribuya
              el trabajo entre los departamentos correspondientes.
            </p>
          </div>
          {/* Indicador de estado SSE */}
          <div className="flex items-center gap-2 text-xs">
            <span
              className={`inline-block h-2.5 w-2.5 rounded-full ${
                sseConnected ? "bg-green-500" : "bg-red-500"
              }`}
            />
            <span className={`text-slate-500 ${sseConnected ? "text-green-700" : "text-red-600"}`}>
              {sseConnected ? "Tiempo real activo" : "Reconectando..."}
            </span>
          </div>
        </div>
      </section>

      {/* Notificaciones SSE en vivo */}
      {notifications.length > 0 && (
        <section className="mb-4 space-y-2">
          {notifications.map((notif) => (
            <div
              key={notif.ticket_id}
              className="flex items-start gap-3 rounded-lg border-l-4 border-blue-500 bg-blue-50 p-3 shadow-sm"
            >
              <div className="flex-shrink-0">
                <span className="inline-flex h-6 w-6 items-center justify-center rounded-full bg-blue-100 text-xs font-bold text-blue-700">
                  N
                </span>
              </div>
              <div className="flex-1">
                <p className="text-xs font-bold text-blue-800 uppercase tracking-wide">
                  {notif.rfp_id || "Nuevo ticket"}
                </p>
                <p className="text-sm font-medium text-slate-900">
                  {notif.client_name || "Cliente pendiente de clasificación"}
                </p>
                <p className="text-xs text-slate-500">
                  Estado: <span className="font-mono">{notif.status || "analizando"}</span>
                  {notif.created_at && (
                    <span className="ml-2">
                      • {new Date(notif.created_at).toLocaleTimeString()}
                    </span>
                  )}
                </p>
              </div>
              <button
                onClick={() =>
                  setNotifications((prev) =>
                    prev.filter((n) => n.ticket_id !== notif.ticket_id)
                  )
                }
                className="flex-shrink-0 text-slate-400 hover:text-slate-600"
                aria-label="Descartar notificación"
              >
                ✕
              </button>
            </div>
          ))}
        </section>
      )}

      {/* Upload form */}
      <section className="mb-8 rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
        <h2 className="mb-4 text-lg font-semibold text-slate-900">
          Subir nueva RFP
        </h2>

        <form onSubmit={handleUpload} className="space-y-4">
          <div>
            <label
              htmlFor="rfp-file"
              className="block text-sm font-medium text-slate-700"
            >
              Archivo PDF
            </label>
            <input
              type="file"
              id="rfp-file"
              name="file"
              accept=".pdf,application/pdf"
              className="mt-1 block w-full rounded-lg border border-slate-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              required
            />
          </div>

          {uploadError && (
            <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
              {uploadError}
            </div>
          )}

          <button
            type="submit"
            disabled={uploading}
            className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {uploading ? "Subiendo..." : "Subir RFP"}
          </button>
        </form>
      </section>

      {/* Ticket list */}
      <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
        <h2 className="mb-4 text-lg font-semibold text-slate-900">
          Tickets ({tickets.length})
        </h2>

        {loading && tickets.length === 0 && (
          <p className="text-sm text-slate-500">Cargando tickets...</p>
        )}

        {error && (
          <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
            {error}
          </div>
        )}

        {!loading && tickets.length === 0 && (
          <p className="text-sm text-slate-500">
            No hay tickets aún. Sube una RFP para comenzar.
          </p>
        )}

        {tickets.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-xs font-semibold uppercase tracking-wide text-slate-500">
                  <th className="py-3 pr-4">Ticket ID</th>
                  <th className="py-3 pr-4">Cliente</th>
                  <th className="py-3 pr-4">Estado</th>
                  <th className="py-3 pr-4">Creado</th>
                  <th className="py-3 pr-4">Acción</th>
                </tr>
              </thead>
              <tbody>
                {tickets.map((ticket) => (
                  <tr
                    key={ticket.ticket_id}
                    className="border-b border-slate-100 hover:bg-slate-50"
                  >
                    <td className="py-3 pr-4 font-mono text-xs text-slate-600">
                      {ticket.ticket_id.slice(0, 8)}...
                    </td>
                    <td className="py-3 pr-4 font-medium text-slate-900">
                      {ticket.client_name || "—"}
                    </td>
                    <td className="py-3 pr-4">
                      <span
                        className={`inline-block rounded-full border px-2.5 py-0.5 text-xs font-medium ${
                          STATUS_COLORS[ticket.status] ||
                          "bg-slate-100 text-slate-800"
                        }`}
                      >
                        {STATUS_LABELS[ticket.status] || ticket.status}
                      </span>
                    </td>
                    <td className="py-3 pr-4 text-xs text-slate-500">
                      {new Date(ticket.created_at).toLocaleDateString()}
                    </td>
                    <td className="py-3">
                      <a
                        href={`/rfp/${ticket.ticket_id}`}
                        className="text-sm font-medium text-blue-600 hover:text-blue-800"
                      >
                        Ver detalle →
                      </a>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </main>
  );
}