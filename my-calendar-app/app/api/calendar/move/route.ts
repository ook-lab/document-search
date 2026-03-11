import { getAccessToken } from "../../_lib/google-token";

// POST /api/calendar/move
// body: { fromCalendarId, toCalendarId, eventId }
// Google Calendar の move API を使ってイベントを別カレンダーに移動
export async function POST(req: Request) {
  try {
    const { fromCalendarId, toCalendarId, eventId } = await req.json();
    if (!fromCalendarId || !toCalendarId || !eventId)
      return Response.json({ error: "fromCalendarId, toCalendarId, eventId required" }, { status: 400 });

    const token = await getAccessToken();
    const res = await fetch(
      `https://www.googleapis.com/calendar/v3/calendars/${encodeURIComponent(fromCalendarId)}/events/${encodeURIComponent(eventId)}/move?destination=${encodeURIComponent(toCalendarId)}`,
      { method: "POST", headers: { Authorization: `Bearer ${token}` } }
    );
    const data = await res.json();
    return Response.json(data, { status: res.ok ? 200 : 500 });
  } catch (e) {
    return Response.json({ error: String(e) }, { status: 500 });
  }
}
