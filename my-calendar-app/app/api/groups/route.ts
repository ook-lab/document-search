const SUPABASE_URL = process.env.SUPABASE_URL!;
const SUPABASE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY!;
const OWNER_EMAIL  = process.env.CALENDAR_OWNER_EMAIL!;

function sbHeaders() {
  return {
    "apikey": SUPABASE_KEY,
    "Authorization": `Bearer ${SUPABASE_KEY}`,
    "Content-Type": "application/json",
    "Prefer": "return=representation",
  };
}

export async function GET() {
  const res = await fetch(
    `${SUPABASE_URL}/rest/v1/calendar_groups?owner_email=eq.${encodeURIComponent(OWNER_EMAIL)}&order=sort_order`,
    { headers: sbHeaders() }
  );
  const data = await res.json();
  return Response.json(data);
}

export async function POST(req: Request) {
  const body = await req.json();
  const res = await fetch(`${SUPABASE_URL}/rest/v1/calendar_groups`, {
    method: "POST",
    headers: sbHeaders(),
    body: JSON.stringify({
      owner_email: OWNER_EMAIL,
      name:        body.name,
      color:       body.color,
      base_ids:    body.baseIds,
      sort_order:  body.sortOrder ?? 0,
    }),
  });
  const data = await res.json();
  return Response.json(data, { status: res.ok ? 200 : 500 });
}
