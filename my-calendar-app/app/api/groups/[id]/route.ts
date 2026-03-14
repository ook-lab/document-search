import { getServerSession } from "next-auth";
import { authOptions } from "../../_lib/auth-options";

const SUPABASE_URL = process.env.SUPABASE_URL!;
const SUPABASE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY!;

function sbHeaders() {
  return {
    "apikey": SUPABASE_KEY,
    "Authorization": `Bearer ${SUPABASE_KEY}`,
    "Content-Type": "application/json",
    "Prefer": "return=representation",
  };
}

async function getEmail(): Promise<string | null> {
  const session = await getServerSession(authOptions);
  return session?.user?.email ?? null;
}

export async function PUT(req: Request, { params }: { params: Promise<{ id: string }> }) {
  const email = await getEmail();
  if (!email) return Response.json({ error: "Unauthorized" }, { status: 401 });
  try {
    const { id } = await params;
    const body = await req.json();
    const url = `${SUPABASE_URL}/rest/v1/calendar_groups?id=eq.${id}&owner_email=eq.${encodeURIComponent(email)}`;
    const res = await fetch(url, {
      method: "PATCH",
      headers: sbHeaders(),
      body: JSON.stringify({
        name:             body.name,
        color:            body.color,
        calendar_configs: body.calendars,
        sort_order:       body.sortOrder ?? 0,
        updated_at:       new Date().toISOString(),
      }),
    });
    const text = await res.text();
    if (!res.ok) {
      console.error("[groups PUT] Supabase error:", res.status, text);
      return Response.json({ error: text }, { status: 500 });
    }
    return Response.json(JSON.parse(text));
  } catch (e) {
    console.error("[groups PUT] exception:", String(e));
    return Response.json({ error: String(e) }, { status: 500 });
  }
}

export async function DELETE(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  const email = await getEmail();
  if (!email) return Response.json({ error: "Unauthorized" }, { status: 401 });
  try {
    const { id } = await params;
    await fetch(
      `${SUPABASE_URL}/rest/v1/calendar_groups?id=eq.${id}&owner_email=eq.${encodeURIComponent(email)}`,
      { method: "DELETE", headers: sbHeaders() }
    );
    return Response.json({ ok: true });
  } catch (e) {
    console.error("[groups DELETE] exception:", String(e));
    return Response.json({ error: String(e) }, { status: 500 });
  }
}
