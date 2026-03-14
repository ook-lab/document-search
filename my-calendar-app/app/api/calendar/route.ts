import { getAccessToken } from "../_lib/google-token";

// GET /api/calendar?action=list
// GET /api/calendar?action=events&calendarIds=id1,id2&timeMin=...&timeMax=...
export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const action = searchParams.get("action");

  try {
    const token = await getAccessToken();

    if (action === "list") {
      const res = await fetch(
        "https://www.googleapis.com/calendar/v3/users/me/calendarList?maxResults=250",
        { headers: { Authorization: `Bearer ${token}` } }
      );
      const data = await res.json();
      if (!res.ok) {
        console.error("[calendar/list] Google API error:", JSON.stringify(data));
        return Response.json({ error: "Google API error", detail: data }, { status: 500 });
      }
      return Response.json(data);
    }

    if (action === "events") {
      const calendarIds = (searchParams.get("calendarIds") ?? "").split(",").filter(Boolean);
      const timeMin = searchParams.get("timeMin") ?? "";
      const timeMax = searchParams.get("timeMax") ?? "";

      const results = await Promise.all(
        calendarIds.map(async (calId) => {
          const res = await fetch(
            `https://www.googleapis.com/calendar/v3/calendars/${encodeURIComponent(calId)}/events?timeMin=${encodeURIComponent(timeMin)}&timeMax=${encodeURIComponent(timeMax)}&singleEvents=true&orderBy=startTime&maxResults=500`,
            { headers: { Authorization: `Bearer ${token}` } }
          );
          if (!res.ok) return { calendarId: calId, items: [] };
          const data = await res.json();
          return { calendarId: calId, items: data.items ?? [] };
        })
      );
      return Response.json(results);
    }

    return Response.json({ error: "invalid action" }, { status: 400 });
  } catch {
    return Response.json({ error: "Google API error" }, { status: 500 });
  }
}

// POST /api/calendar  { calendarId, event }
export async function POST(req: Request) {
  const { calendarId, event } = await req.json();
  const token = await getAccessToken();
  const res = await fetch(
    `https://www.googleapis.com/calendar/v3/calendars/${encodeURIComponent(calendarId)}/events`,
    { method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, body: JSON.stringify(event) }
  );
  const data = await res.json();
  if (!res.ok) {
    console.error("[calendar/POST] error calendarId:", calendarId, "status:", res.status, "detail:", JSON.stringify(data));
    return Response.json({ error: data?.error ?? data }, { status: 500 });
  }
  return Response.json(data);
}

// PUT /api/calendar  { calendarId, eventId, event }
export async function PUT(req: Request) {
  const { calendarId, eventId, event } = await req.json();
  const token = await getAccessToken();
  const res = await fetch(
    `https://www.googleapis.com/calendar/v3/calendars/${encodeURIComponent(calendarId)}/events/${encodeURIComponent(eventId)}`,
    { method: "PUT", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, body: JSON.stringify(event) }
  );
  const data = await res.json();
  return Response.json(data, { status: res.ok ? 200 : 500 });
}

// DELETE /api/calendar  { calendarId, eventId }
export async function DELETE(req: Request) {
  const { calendarId, eventId } = await req.json();
  const token = await getAccessToken();
  const res = await fetch(
    `https://www.googleapis.com/calendar/v3/calendars/${encodeURIComponent(calendarId)}/events/${encodeURIComponent(eventId)}`,
    { method: "DELETE", headers: { Authorization: `Bearer ${token}` } }
  );
  return new Response(null, { status: res.ok || res.status === 204 ? 204 : 500 });
}
