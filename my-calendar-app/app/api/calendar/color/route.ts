import { getAccessToken } from "../../_lib/google-token";

// PATCH /api/calendar/color
// body: { calendarId, backgroundColor }
export async function PATCH(req: Request) {
  try {
    const { calendarId, backgroundColor } = await req.json();
    if (!calendarId || !backgroundColor)
      return Response.json({ error: "calendarId, backgroundColor required" }, { status: 400 });

    const token = await getAccessToken();
    const res = await fetch(
      `https://www.googleapis.com/calendar/v3/users/me/calendarList/${encodeURIComponent(calendarId)}`,
      {
        method: "PATCH",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify({ backgroundColor, foregroundColor: "#ffffff" }),
      }
    );
    const data = await res.json();
    return Response.json(data, { status: res.ok ? 200 : 500 });
  } catch (e) {
    return Response.json({ error: String(e) }, { status: 500 });
  }
}
