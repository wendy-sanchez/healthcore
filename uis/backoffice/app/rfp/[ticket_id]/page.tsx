"use client";

import { use, useCallback, useEffect, useState } from "react";
import { request } from "@/lib/httpClient";

// ── Types ──────────────────────────────────────────────────────────

interface ReadabilityMetrics {
  flesch_reading_ease: number;
  flesch_kincaid_grade: number;
  gunning_fog: number;
  coleman_liau_index: number;
  automated_readability_index: number;
  word_count: number;
}

interface RfpMetadata {
  client_name: string;
  client_country: string;
  program_type: string;
  covered_population: number | null;
  deadline: string | null;
  budget_range: string | null;
  currency: string | null;
  departments_needed: string[];
  extra_departments_mentioned: string[];
  readability: ReadabilityMetrics | null;
  word_count: number | null;
  estimated_complexity: string | null;
}

interface KeyAspect {
  aspect: string;
  detail: string;
  source_reference: string;
  confidence: string;
}

interface DepartmentSection {
  department_id: string;
  key_aspects: KeyAspect[];
  open_questions: string[];
  contains_phi: boolean;
}

interface TicketDetail {
  ticket_id: string;
  status: string;
  client_name: string | null;
  classification_reason: string | null;
  error_detail: string | null;
  metadata: RfpMetadata | null;
  departments: DepartmentSection[];
  synthesis: string | null;
  handoff_payload: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

const STATUS_LABELS: Record<string, string> = {
  analizando: "Analizando",
  descartado: "Descartado",
  analisis_completo: "Análisis completo",
  error: "Error",
};

const STATUS_COLORS: Record<string, string> = {
  analizando: "bg-yellow-100 text-yellow-800 border-yellow-300",
  descartado: "bg-red-100 text-red-800 border-red-300",
  analisis_completo: "bg-green-100 text-green-800 border-green-300",
  error: "bg-red-100 text-red-800 border-red-300",
};

const DEPT_LABELS: Record<string, string> = {
  revenue: "Revenue — Tom Callahan",
  clinical: "Clinical — Dr. Marcus Reid",
  compliance: "Compliance — Claire Whitfield",
};

// ── Component ───────────────────────────────────────────────────────

export default function RfpTicketPage({
  params: paramsPromise,
}: {
  params: Promise<{ ticket_id: string }>;
}) {
  const { ticket_id } = use(paramsPromise);
  const [ticket, setTicket] = useState<TicketDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reprocessing, setReprocessing] = useState(false);

  // ── Fetch ticket ─────────────────────────────────────────────────

  const fetchTicket = useCallback(async () => {
    try {
      const data = await request<TicketDetail>(
        `/api/rfp/tickets/${ticket_id}`
      );
      setTicket(data);
      setError(null);
      return data;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error al cargar ticket");
      return null;
    } finally {
      setLoading(false);
    }
  }, [ticket_id]);

  // Initial fetch + poll while "analizando"
  useEffect(() => {
    let stopped = false;

    const poll = async () => {
      const data = await fetchTicket();
      if (
        !stopped &&
        data &&
        data.status === "analizando"
      ) {
        setTimeout(poll, 3000);
      }
    };

    poll();
    return () => {
      stopped = true;
    };
  }, [fetchTicket]);

  // ── Reprocess ────────────────────────────────────────────────────

  const handleReprocess = async () => {
    if (!confirm("¿Reprocesar este ticket? Se reiniciará el análisis.")) return;
    setReprocessing(true);
    try {
      const res = await fetch(`/api/rfp/tickets/${ticket_id}/reprocess`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${localStorage.getItem("token") || ""}`,
        },
      });

      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || `Error ${res.status}`);
      }

      setTicket((prev) =>
        prev ? { ...prev, status: "analizando" } : prev
      );
      // Poll again
      setTimeout(() => fetchTicket(), 1000);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error al reprocesar");
    } finally {
      setReprocessing(false);
    }
  };

  // ── Render ───────────────────────────────────────────────────────

  if (loading) {
    return (
      <main className="mx-auto w-full max-w-5xl px-4 py-8">
        <p className="text-sm text-slate-500">Cargando ticket...</p>
      </main>
    );
  }

  if (error && !ticket) {
    return (
      <main className="mx-auto w-full max-w-5xl px-4 py-8">
        <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          {error}
        </div>
        <a
          href="/rfp"
          className="mt-4 inline-block text-sm font-medium text-blue-600 hover:text-blue-800"
        >
          ← Volver a lista
        </a>
      </main>
    );
  }

  if (!ticket) return null;

  return (
    <main className="mx-auto w-full max-w-5xl px-4 py-8 sm:px-6 lg:px-8">
      {/* Header */}
      <div className="mb-6">
        <a
          href="/rfp"
          className="mb-2 inline-block text-sm font-medium text-blue-600 hover:text-blue-800"
        >
          ← Tickets RFP
        </a>
        <div className="flex items-center justify-between">
          <h1 className="text-2xl font-bold text-slate-900">
            Ticket:{" "}
            <span className="font-mono text-base">{ticket.ticket_id}</span>
          </h1>
          <span
            className={`inline-block rounded-full border px-3 py-1 text-sm font-medium ${
              STATUS_COLORS[ticket.status] || "bg-slate-100 text-slate-800"
            }`}
          >
            {STATUS_LABELS[ticket.status] || ticket.status}
          </span>
        </div>
      </div>

      {/* Error banner */}
      {ticket.status === "error" && ticket.error_detail && (
        <div className="mb-6 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          <strong>Error:</strong> {ticket.error_detail}
        </div>
      )}

      {/* Classification result (if descartado) */}
      {ticket.status === "descartado" && ticket.classification_reason && (
        <div className="mb-6 rounded-lg border border-orange-200 bg-orange-50 p-4 text-sm text-orange-800">
          <strong>No es una RFP válida.</strong>{" "}
          {ticket.classification_reason}
        </div>
      )}

      {/* Reprocess button (only for error / descartado) */}
      {(ticket.status === "error" || ticket.status === "descartado") && (
        <div className="mb-6">
          <button
            onClick={handleReprocess}
            disabled={reprocessing}
            className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {reprocessing ? "Reprocesando..." : "Reprocesar ticket"}
          </button>
        </div>
      )}

      {/* Loading spinner for active analysis */}
      {ticket.status === "analizando" && (
        <div className="mb-6 flex items-center gap-3 rounded-lg border border-blue-200 bg-blue-50 p-4 text-sm text-blue-700">
          <svg
            className="h-5 w-5 animate-spin text-blue-600"
            xmlns="http://www.w3.org/2000/svg"
            fill="none"
            viewBox="0 0 24 24"
          >
            <circle
              className="opacity-25"
              cx="12"
              cy="12"
              r="10"
              stroke="currentColor"
              strokeWidth="4"
            />
            <path
              className="opacity-75"
              fill="currentColor"
              d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z"
            />
          </svg>
          Analizando propuesta...
        </div>
      )}

      {/* Metadata section */}
      {ticket.metadata && (
        <section className="mb-6 rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
          <h2 className="mb-4 text-lg font-semibold text-slate-900">
            Metadatos
          </h2>
          <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <div>
              <dt className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                Cliente
              </dt>
              <dd className="mt-1 text-sm font-medium text-slate-900">
                {ticket.metadata.client_name || "—"}
              </dd>
            </div>
            <div>
              <dt className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                País
              </dt>
              <dd className="mt-1 text-sm font-medium text-slate-900">
                {ticket.metadata.client_country || "—"}
              </dd>
            </div>
            <div>
              <dt className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                Tipo de programa
              </dt>
              <dd className="mt-1 text-sm font-medium text-slate-900">
                {ticket.metadata.program_type || "—"}
              </dd>
            </div>
            <div>
              <dt className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                Población cubierta
              </dt>
              <dd className="mt-1 text-sm font-medium text-slate-900">
                {ticket.metadata.covered_population?.toLocaleString() || "—"}
              </dd>
            </div>
            <div>
              <dt className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                Presupuesto
              </dt>
              <dd className="mt-1 text-sm font-medium text-slate-900">
                {ticket.metadata.budget_range
                  ? `${ticket.metadata.currency || ""} ${ticket.metadata.budget_range}`
                  : "—"}
              </dd>
            </div>
            <div>
              <dt className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                Fecha límite
              </dt>
              <dd className="mt-1 text-sm font-medium text-slate-900">
                {ticket.metadata.deadline || "—"}
              </dd>
            </div>
            <div>
              <dt className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                Departamentos necesarios
              </dt>
              <dd className="mt-1 text-sm font-medium text-slate-900">
                {ticket.metadata.departments_needed?.join(", ") || "—"}
              </dd>
            </div>
            <div>
              <dt className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                Complejidad estimada
              </dt>
              <dd className="mt-1 text-sm font-medium text-slate-900">
                {ticket.metadata.estimated_complexity || "—"}
              </dd>
            </div>
          </dl>

          {/* Readability */}
          {ticket.metadata.readability && (
            <div className="mt-6 border-t border-slate-100 pt-4">
              <h3 className="mb-2 text-sm font-semibold text-slate-700">
                Legibilidad
              </h3>
              <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
                <div>
                  <dt className="text-xs text-slate-500">Flesch Reading Ease</dt>
                  <dd className="text-sm font-medium">
                    {ticket.metadata.readability.flesch_reading_ease.toFixed(1)}
                  </dd>
                </div>
                <div>
                  <dt className="text-xs text-slate-500">Flesch-Kincaid Grade</dt>
                  <dd className="text-sm font-medium">
                    {ticket.metadata.readability.flesch_kincaid_grade.toFixed(1)}
                  </dd>
                </div>
                <div>
                  <dt className="text-xs text-slate-500">Gunning Fog</dt>
                  <dd className="text-sm font-medium">
                    {ticket.metadata.readability.gunning_fog.toFixed(1)}
                  </dd>
                </div>
                <div>
                  <dt className="text-xs text-slate-500">Coleman-Liau</dt>
                  <dd className="text-sm font-medium">
                    {ticket.metadata.readability.coleman_liau_index.toFixed(1)}
                  </dd>
                </div>
                <div>
                  <dt className="text-xs text-slate-500">ARI</dt>
                  <dd className="text-sm font-medium">
                    {ticket.metadata.readability.automated_readability_index.toFixed(1)}
                  </dd>
                </div>
                <div>
                  <dt className="text-xs text-slate-500">Palabras</dt>
                  <dd className="text-sm font-medium">
                    {ticket.metadata.readability.word_count.toLocaleString()}
                  </dd>
                </div>
              </dl>
            </div>
          )}
        </section>
      )}

      {/* Department sections */}
      {ticket.departments.length > 0 && (
        <section className="mb-6 space-y-4">
          <h2 className="text-lg font-semibold text-slate-900">
            Análisis por departamento
          </h2>
          {ticket.departments.map((dept) => (
            <div
              key={dept.department_id}
              className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm"
            >
              <h3 className="mb-3 text-base font-semibold text-slate-900">
                {DEPT_LABELS[dept.department_id] || dept.department_id}
              </h3>

              {dept.contains_phi && (
                <div className="mb-3 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
                  ⚠️ Esta sección contiene PHI (información de salud protegida)
                </div>
              )}

              {dept.key_aspects.length === 0 && (
                <p className="text-sm text-slate-500">
                  No se identificaron aspectos clave.
                </p>
              )}

              {dept.key_aspects.map((aspect, i) => (
                <div key={i} className="mb-3 last:mb-0">
                  <p className="text-sm font-medium text-slate-900">
                    {aspect.aspect}
                  </p>
                  <p className="text-sm text-slate-600">{aspect.detail}</p>
                  <p className="text-xs text-slate-400">
                    Confianza: {aspect.confidence} · Referencia:{" "}
                    {aspect.source_reference}
                  </p>
                </div>
              ))}

              {dept.open_questions.length > 0 && (
                <div className="mt-4 border-t border-slate-100 pt-4">
                  <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
                    Preguntas abiertas
                  </h4>
                  <ul className="list-inside list-disc space-y-1 text-sm text-slate-600">
                    {dept.open_questions.map((q, i) => (
                      <li key={i}>{q}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          ))}
        </section>
      )}

      {/* Synthesis */}
      {ticket.synthesis && (
        <section className="mb-6 rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
          <h2 className="mb-4 text-lg font-semibold text-slate-900">
            Síntesis para Ventas
          </h2>
          <div className="prose prose-sm max-w-none whitespace-pre-wrap text-sm text-slate-700">
            {ticket.synthesis}
          </div>
        </section>
      )}

      {/* Created / Updated */}
      <div className="flex items-center justify-between text-xs text-slate-400">
        <span>Creado: {new Date(ticket.created_at).toLocaleString()}</span>
        <span>Actualizado: {new Date(ticket.updated_at).toLocaleString()}</span>
      </div>
    </main>
  );
}