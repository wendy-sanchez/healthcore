const BACKEND_URL = process.env.SUPPLIERS_API_URL ?? process.env.BACKEND_URL ?? "http://backend:8000";

export async function POST(request: Request): Promise<Response> {
  try {
    const formData = await request.formData();
    const auth = request.headers.get("authorization");

    const upstream = await fetch(`${BACKEND_URL}/rfp/upload`, {
      method: "POST",
      headers: auth ? { authorization: auth } : {},
      body: formData,
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

export const config = {
  api: {
    bodyParser: false,
  },
};