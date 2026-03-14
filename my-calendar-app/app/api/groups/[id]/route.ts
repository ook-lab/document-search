const SUPABASE_URL = process.env.SUPABASE_URL!;
const SUPABASE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY!;
const OWNER_EMAIL  = process.env.CALENDAR_OWNER_EMAIL ?? "ookubo.y@workspace-o.com";

function sbHeaders() {
  return {
    "apikey": SUPABASE_KEY,
    "Authorization": `Bearer ${SUPABASE_KEY}`,
    "Content-Type": "application/json",
    "Prefer": "return=representation",
  };
}

export async function PUT(req: Request, { params }: { params: Promise<{ id: string }> }) {
  try {
    const { id } = await params;
    const body = await req.json();
    const url = `${SUPABASE_URL}/rest/v1/calendar_groups?id=eq.${id}&owner_email=eq.${encodeURIComponent(OWNER_EMAIL)}`;
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
  try {
    const { id } = await params;
    await fetch(
      `${SUPABASE_URL}/rest/v1/calendar_groups?id=eq.${id}&owner_email=eq.${encodeURIComponent(OWNER_EMAIL)}`,
      { method: "DELETE", headers: sbHeaders() }
    );
    return Response.json({ ok: true });
  } catch (e) {
    console.error("[groups DELETE] exception:", String(e));
    return Response.json({ error: String(e) }, { status: 500 });
  }
}
