// supabase/functions/google-calendar-sync/index.ts
import { serve } from "https://deno.land/std@0.224.0/http/server.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

function mustGetEnv(name: string): string {
  const v = Deno.env.get(name);
  if (!v) throw new Error(`Missing env: ${name}`);
  return v;
}

async function refreshAccessToken(
  refreshToken: string,
  clientId: string,
  clientSecret: string,
): Promise<{ access_token: string; expires_in: number }> {
  const res = await fetch("https://oauth2.googleapis.com/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      client_id: clientId,
      client_secret: clientSecret,
      refresh_token: refreshToken,
      grant_type: "refresh_token",
    }),
  });
  const j = await res.json();
  if (!res.ok) throw new Error(`refresh token failed: ${JSON.stringify(j)}`);
  return { access_token: j.access_token, expires_in: j.expires_in };
}

function isoOrNull(v: unknown): string | null {
  if (!v) return null;
  const s = String(v);
  const d = new Date(s);
  return isNaN(d.getTime()) ? null : d.toISOString();
}

// Google event.start/end can be {dateTime} or {date}
function parseStartEnd(ev: any): { start_ts: string | null; end_ts: string | null } {
  const start = ev?.start?.dateTime ?? ev?.start?.date ?? null;
  const end = ev?.end?.dateTime ?? ev?.end?.date ?? null;
  return { start_ts: isoOrNull(start), end_ts: isoOrNull(end) };
}

async function listEventsPage(
  accessToken: string,
  calendarId: string,
  params: Record<string, string>,
): Promise<any> {
  const u = new URL(`https://www.googleapis.com/calendar/v3/calendars/${encodeURIComponent(calendarId)}/events`);
  for (const [k, v] of Object.entries(params)) u.searchParams.set(k, v);

  const res = await fetch(u.toString(), {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  const j = await res.json();
  if (!res.ok) throw new Error(`events.list failed: ${JSON.stringify(j)}`);
  return j;
}

serve(async (req) => {
  try {
    const PROJECT_URL = mustGetEnv("PROJECT_URL");
    const SERVICE_ROLE_KEY = mustGetEnv("SERVICE_ROLE_KEY");
    const GOOGLE_CLIENT_ID = mustGetEnv("GOOGLE_CLIENT_ID");
    const GOOGLE_CLIENT_SECRET = mustGetEnv("GOOGLE_CLIENT_SECRET");

    const supabase = createClient(PROJECT_URL, SERVICE_ROLE_KEY, { auth: { persistSession: false } });

    // call: /functions/v1/google-calendar-sync?user_id=...&calendar_id=primary
    const url = new URL(req.url);
    const user_id = url.searchParams.get("user_id");
    const calendar_id = url.searchParams.get("calendar_id") ?? "primary";
    if (!user_id) return new Response("missing user_id", { status: 400 });

    // Optional initial sync window (to avoid huge full sync):
    // days_past=180 days_future=365 (defaults)
    const daysPast = Number(url.searchParams.get("days_past") ?? "180");
    const daysFuture = Number(url.searchParams.get("days_future") ?? "365");

    // 1) get refresh_token
    const { data: tok, error: tokErr } = await supabase
      .from("google_oauth_tokens")
      .select("refresh_token")
      .eq("user_id", user_id)
      .maybeSingle();

    if (tokErr) return new Response(`token fetch error: ${tokErr.message}`, { status: 500 });
    if (!tok?.refresh_token) return new Response("no refresh_token for user", { status: 400 });

    // 2) get current syncToken
    const { data: st, error: stErr } = await supabase
      .from("calendar_sync_state")
      .select("next_sync_token")
      .eq("user_id", user_id)
      .eq("calendar_id", calendar_id)
      .maybeSingle();

    if (stErr) return new Response(`sync state fetch error: ${stErr.message}`, { status: 500 });

    let syncToken: string | null = st?.next_sync_token ?? null;

    // 3) refresh access token
    const { access_token } = await refreshAccessToken(tok.refresh_token, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET);

    // 4) events.list loop
    const baseParams: Record<string, string> = {
      singleEvents: "true",
      showDeleted: "true",
      maxResults: "2500",
    };

    // 初回（syncToken無し）は範囲を切ってフル取得 → nextSyncTokenを取る（運用上の現実解）
    if (!syncToken) {
      const now = new Date();
      const timeMin = new Date(now.getTime() - daysPast * 86400_000);
      const timeMax = new Date(now.getTime() + daysFuture * 86400_000);
      baseParams.timeMin = timeMin.toISOString();
      baseParams.timeMax = timeMax.toISOString();
    } else {
      baseParams.syncToken = syncToken;
    }

    let pageToken: string | null = null;
    let fetched = 0;
    let upserted = 0;
    let deleted = 0;

    while (true) {
      const params = { ...baseParams };
      if (pageToken) params.pageToken = pageToken;

      let page: any;
      try {
        page = await listEventsPage(access_token, calendar_id, params);
      } catch (e: any) {
        // 410 Gone => syncToken invalid. Must full resync.
        const msg = String(e?.message ?? e);
        if (msg.includes('"code":410') || msg.includes("410")) {
          // clear token, and instruct caller to rerun (or we can auto-resync)
          await supabase.from("calendar_sync_state").upsert({
            user_id,
            calendar_id,
            next_sync_token: null,
            last_full_sync_at: new Date().toISOString(),
          }, { onConflict: "user_id,calendar_id" });

          return new Response(
            JSON.stringify({ ok: false, reason: "syncToken expired (410). cleared; rerun to full sync." }),
            { status: 409, headers: { "Content-Type": "application/json" } },
          );
        }
        throw e;
      }

      const items: any[] = Array.isArray(page.items) ? page.items : [];
      fetched += items.length;

      // upsert each item by (user_id, calendar_id, event_id)
      for (const ev of items) {
        const event_id = ev?.id;
        if (!event_id) continue;

        const status = ev?.status ?? null;
        const updated = isoOrNull(ev?.updated);
        const { start_ts, end_ts } = parseStartEnd(ev);

        const row = {
          user_id,
          calendar_id,
          event_id,
          status,
          updated,
          start_ts,
          end_ts,
          payload: ev,
        };

        const { error: upErr } = await supabase.from("calendar_events").upsert(row, {
          onConflict: "user_id,calendar_id,event_id",
        });

        if (upErr) throw new Error(`event upsert failed: ${upErr.message}`);

        upserted += 1;
        if (status === "cancelled") deleted += 1;
      }

      pageToken = page.nextPageToken ?? null;

      // capture nextSyncToken (only appears on last page)
      if (!pageToken) {
        const nextSyncToken = page.nextSyncToken ?? null;

        if (nextSyncToken) {
          await supabase.from("calendar_sync_state").upsert({
            user_id,
            calendar_id,
            next_sync_token: nextSyncToken,
            last_full_sync_at: syncToken ? null : new Date().toISOString(),
          }, { onConflict: "user_id,calendar_id" });
        }

        return new Response(JSON.stringify({
          ok: true,
          calendar_id,
          mode: syncToken ? "delta" : "full-window",
          fetched,
          upserted,
          cancelled: deleted,
          saved_next_sync_token: Boolean(nextSyncToken),
        }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
    }
  } catch (e) {
    return new Response(String((e as any)?.message ?? e), { status: 500 });
  }
});