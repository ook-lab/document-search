import { getServerSession } from "next-auth";
import { authOptions } from "../_lib/auth-options";

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

export async function GET() {
  const email = await getEmail();
  if (!email) return Response.json([]);
  try {
    const url = `${SUPABASE_URL}/rest/v1/calendar_groups?owner_email=eq.${encodeURIComponent(email)}&order=sort_order`;
    const res = await fetch(url, { headers: sbHeaders() });
    const text = await res.text();
    if (!res.ok) {
      console.error("[groups GET] Supabase error:", res.status, text);
      return Response.json([]);
    }
    return Response.json(JSON.parse(text));
  } catch (e) {
    console.error("[groups GET] exception:", String(e));
    return Response.json([]);
  }
}

export async function POST(req: Request) {
  const email = await getEmail();
  if (!email) return Response.json({ error: "Unauthorized" }, { status: 401 });
  try {
    const body = await req.json();
    const payload = {
      owner_email:      email,
      name:             body.name,
      color:            body.color,
      calendar_configs: body.calendars,
      sort_order:       body.sortOrder ?? 0,
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
