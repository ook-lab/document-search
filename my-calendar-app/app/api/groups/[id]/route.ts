import { getServerSession } from "next-auth";
import { authOptions } from "../../auth/[...nextauth]/route";

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

export async function PUT(req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const session = await getServerSession(authOptions);
  if (!session?.user?.email) return Response.json({ error: "Unauthorized" }, { status: 401 });

  const body = await req.json();
  const res = await fetch(
    `${SUPABASE_URL}/rest/v1/calendar_groups?id=eq.${id}&owner_email=eq.${encodeURIComponent(session.user.email)}`,
    {
      method: "PATCH",
      headers: sbHeaders(),
      body: JSON.stringify({
        name:       body.name,
        color:      body.color,
        base_ids:   body.baseIds,
        sort_order: body.sortOrder ?? 0,
        updated_at: new Date().toISOString(),
      }),
    }
  );
  const data = await res.json();
  return Response.json(data, { status: res.ok ? 200 : 500 });
}

export async function DELETE(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const session = await getServerSession(authOptions);
  if (!session?.user?.email) return Response.json({ error: "Unauthorized" }, { status: 401 });

  await fetch(
    `${SUPABASE_URL}/rest/v1/calendar_groups?id=eq.${id}&owner_email=eq.${encodeURIComponent(session.user.email)}`,
    { method: "DELETE", headers: sbHeaders() }
  );
  return Response.json({ ok: true });
}
