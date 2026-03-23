import { getServerSession } from "next-auth";
import { authOptions } from "../_lib/auth-options";

const SUPABASE_URL = process.env.SUPABASE_URL!;
const SUPABASE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY!;

function sbHeaders() {
  return {
    "apikey": SUPABASE_KEY,
    "Authorization": `Bearer ${SUPABASE_KEY}`,
    "Content-Type": "application/json",
  };
}

async function getEmail(): Promise<string | null> {
  const session = await getServerSession(authOptions);
  return session?.user?.email ?? null;
}

// GET /api/preferences
export async function GET() {
  const email = await getEmail();
  if (!email) return Response.json({ selected_base_ids: [], selected_group_ids: [], cal_view_mode: {}, group_view_mode: {} });
  try {
    const url = `${SUPABASE_URL}/rest/v1/user_preferences?owner_email=eq.${encodeURIComponent(email)}&select=selected_base_ids,selected_group_ids,cal_view_mode,group_view_mode,week_start_monday,use_24h`;
    const res = await fetch(url, { headers: sbHeaders() });
    if (!res.ok) return Response.json({ selected_base_ids: [], selected_group_ids: [], cal_view_mode: {}, group_view_mode: {} });
    const rows = await res.json();
    const row = rows[0] ?? { selected_base_ids: [], selected_group_ids: [], cal_view_mode: {}, group_view_mode: {} };
    return Response.json(row);
  } catch {
    return Response.json({ selected_base_ids: [], selected_group_ids: [], cal_view_mode: {}, group_view_mode: {} });
  }
}

// PUT /api/preferences  { selected_base_ids?, selected_group_ids?, cal_view_mode?, group_view_mode? }
export async function PUT(req: Request) {
  const email = await getEmail();
  if (!email) return Response.json({ error: "Unauthorized" }, { status: 401 });
  try {
    const body = await req.json();
    const payload: Record<string, unknown> = {
      owner_email: email,
      updated_at: new Date().toISOString(),
    };
    if ("selected_base_ids"  in body) payload.selected_base_ids  = body.selected_base_ids;
    if ("selected_group_ids" in body) payload.selected_group_ids = body.selected_group_ids;
    if ("cal_view_mode"      in body) payload.cal_view_mode      = body.cal_view_mode;
    if ("group_view_mode"    in body) payload.group_view_mode    = body.group_view_mode;
    if ("week_start_monday"  in body) payload.week_start_monday  = body.week_start_monday;
    if ("use_24h"            in body) payload.use_24h            = body.use_24h;

    const url = `${SUPABASE_URL}/rest/v1/user_preferences`;
    const res = await fetch(url, {
      method: "POST",
      headers: {
        ...sbHeaders(),
        "Prefer": "resolution=merge-duplicates,return=minimal",
      },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const text = await res.text();
      console.error("[preferences PUT] Supabase error:", res.status, text);
      return Response.json({ error: text }, { status: 500 });
    }
    return Response.json({ ok: true });
  } catch (e) {
    console.error("[preferences PUT] exception:", String(e));
    return Response.json({ error: String(e) }, { status: 500 });
  }
}
