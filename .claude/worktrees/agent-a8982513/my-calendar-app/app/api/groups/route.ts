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

export async function GET() {
  try {
    const url = `${SUPABASE_URL}/rest/v1/calendar_groups?owner_email=eq.${encodeURIComponent(OWNER_EMAIL)}&order=sort_order`;
    const res = await fetch(url, { headers: sbHeaders() });
    const text = await res.text();
    if (!res.ok) {
      console.error("[groups GET] Supabase error:", res.status, text);
      return Response.json([], { status: 200 });
    }
    return Response.json(JSON.parse(text));
  } catch (e) {
    console.error("[groups GET] exception:", String(e));
    return Response.json([], { status: 200 });
  }
}

export async function POST(req: Request) {
  try {
    const body = await req.json();
    const payload = {
      owner_email: OWNER_EMAIL,
      name:        body.name,
      color:       body.color,
      base_ids:    body.baseIds,
      sort_order:  body.sortOrder ?? 0,
    };
    const url = `${SUPABASE_URL}/rest/v1/calendar_groups`;
    const res = await fetch(url, {
      method: "POST",
      headers: sbHeaders(),
      body: JSON.stringify(payload),
    });
    const text = await res.text();
    if (!res.ok) {
      console.error("[groups POST] Supabase error:", res.status, text);
      return Response.json({ error: text }, { status: 500 });
    }
    return Response.json(JSON.parse(text));
  } catch (e) {
    console.error("[groups POST] exception:", String(e));
    return Response.json({ error: String(e) }, { status: 500 });
  }
}
