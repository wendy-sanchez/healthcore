const BACKEND_URL = process.env.SUPPLIERS_API_URL ?? process.env.BACKEND_URL ?? "http://backend:8000";

function getForwardHeaders(request: Request): Record<string, string> {
  const auth = request.headers.get("authorization");
  return auth ? { authorization: auth } : {};
}

export async function POST(
  request: Request,
  { params }: { params: Promise<{ ticket_id: string }> },
): Promise<Response> {
  const { ticket_id } = await params;

  try {
    const upstream = await fetch(`${BACKEND_URL}/rfp/tickets/${ticket_id}/reprocess`, {
      method: "POST",
      headers: { ...getForwardHeaders(request) },
      cache: "no-store",
    });

    const body = await upstream.arrayBuffer();
    return new Response(body, {
      status: upstream.status,
      headers: { "content-type": upstream.headers.get("content-type") ?? "application/json" },
    });
  } catch {
    return Response.json(
      { detail: "No se pudo contactar con la API de RFP." },
      { status: 502 },
    );
  }
}