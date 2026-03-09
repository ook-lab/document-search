import { getServerSession } from "next-auth";
import { authOptions } from "../auth/[...nextauth]/route";

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

export async function GET() {
  const session = await getServerSession(authOptions);
  if (!session?.user?.email) return Response.json({ error: "Unauthorized" }, { status: 401 });

  const res = await fetch(
    `${SUPABASE_URL}/rest/v1/calendar_groups?owner_email=eq.${encodeURIComponent(session.user.email)}&order=sort_order`,
    { headers: sbHeaders() }
  );
  const data = await res.json();
  return Response.json(data);
}

export async function POST(req: Request) {
  const session = await getServerSession(authOptions);
  if (!session?.user?.email) return Response.json({ error: "Unauthorized" }, { status: 401 });

  const body = await req.json();
  const res = await fetch(`${SUPABASE_URL}/rest/v1/calendar_groups`, {
    method: "POST",
    headers: sbHeaders(),
    body: JSON.stringify({
      owner_email: session.user.email,
      name:        body.name,
      color:       body.color,
      base_ids:    body.baseIds,
      sort_order:  body.sortOrder ?? 0,
    }),
  });
  const data = await res.json();
  return Response.json(data, { status: res.ok ? 200 : 500 });
}
