// supabase/functions/google-calendar-renew-watches/index.ts
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
): Promise<string> {
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
  return j.access_token;
}

async function stopChannel(accessToken: string, channelId: string, resourceId: string) {
  // Best-effort: failure is not fatal.
  await fetch("https://www.googleapis.com/calendar/v3/channels/stop", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ id: channelId, resourceId }),
  }).catch(() => {});
}

async function createWatch(
  accessToken: string,
  calendarId: string,
  webhookAddress: string,
): Promise<{ channel_id: string; channel_token: string; resource_id: string; expiration_ms: number | null }> {
  const channel_id = crypto.randomUUID();
  const channel_token = crypto.randomUUID();

  const watchRes = await fetch(
    `https://www.googleapis.com/calendar/v3/calendars/${encodeURIComponent(calendarId)}/events/watch`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        id: channel_id,
        type: "web_hook",
        address: webhookAddress,
        token: channel_token,
      }),
    },
  );

  const watchJson = await watchRes.json();
  if (!watchRes.ok) throw new Error(`watch failed: ${JSON.stringify(watchJson)}`);

  return {
    channel_id,
    channel_token,
    resource_id: watchJson.resourceId as string,
    expiration_ms: watchJson.expiration ? Number(watchJson.expiration) : null,
  };
}

serve(async (req) => {
  try {
    const PROJECT_URL = mustGetEnv("PROJECT_URL");
    const SERVICE_ROLE_KEY = mustGetEnv("SERVICE_ROLE_KEY");
    const GOOGLE_CLIENT_ID = mustGetEnv("GOOGLE_CLIENT_ID");
    const GOOGLE_CLIENT_SECRET = mustGetEnv("GOOGLE_CLIENT_SECRET");

    const supabase = createClient(PROJECT_URL, SERVICE_ROLE_KEY, { auth: { persistSession: false } });

    // Optional filters:
    // /google-calendar-renew-watches?user_id=...&calendar_id=primary&within_hours=48
    const url = new URL(req.url);
    const user_id = url.searchParams.get("user_id");
    const calendar_id = url.searchParams.get("calendar_id");
    const withinHours = Number(url.searchParams.get("within_hours") ?? "48");

    const nowMs = Date.now();
    const thresholdMs = nowMs + withinHours * 3600_000;

    // 1) select watches expiring soon (or already expired)
    let q = supabase
      .from("calendar_watches")
      .select("user_id, calendar_id, channel_id, resource_id, channel_token, expiration_ms")
      .limit(200);

    if (user_id) q = q.eq("user_id", user_id);
    if (calendar_id) q = q.eq("calendar_id", calendar_id);

    // Supabase query builder doesn’t support computed comparisons; do coarse filter then refine in code.
    const { data: watches, error: werr } = await q;
    if (werr) return new Response(`watch fetch error: ${werr.message}`, { status: 500 });

    const targets = (watches ?? []).filter((w) => {
      const exp = typeof w.expiration_ms === "number" ? w.expiration_ms : Number(w.expiration_ms ?? NaN);
      if (!Number.isFinite(exp)) return true; // if unknown expiration, renew it
      return exp <= thresholdMs;
    });

    if (targets.length === 0) {
      return new Response(JSON.stringify({ ok: true, renewed: 0, within_hours: withinHours }), {
        headers: { "Content-Type": "application/json" },
      });
    }

    const webhookAddress = `${PROJECT_URL}/functions/v1/google-calendar-webhook`;

    let renewed = 0;
    const errors: Array<{ user_id: string; calendar_id: string; error: string }> = [];

    // 2) renew each target
    for (const w of targets) {
      try {
        // Fetch refresh_token per user
        const { data: tok, error: tokErr } = await supabase
          .from("google_oauth_tokens")
          .select("refresh_token")
          .eq("user_id", w.user_id)
          .maybeSingle();

        if (tokErr || !tok?.refresh_token) throw new Error(`no refresh_token for user`);

        const accessToken = await refreshAccessToken(tok.refresh_token, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET);

        // Get calendar name
        const calRes = await fetch(
          `https://www.googleapis.com/calendar/v3/calendars/${encodeURIComponent(w.calendar_id)}`,
          { headers: { Authorization: `Bearer ${accessToken}` } },
        );
        const calJson = await calRes.json();
        const calendar_name: string = calJson.summary ?? w.calendar_id;

        // Stop old channel best-effort
        await stopChannel(accessToken, w.channel_id, w.resource_id);

        // Create new watch
        const nw = await createWatch(accessToken, w.calendar_id, webhookAddress);

        // Update DB
        const { error: upErr } = await supabase.from("calendar_watches").upsert({
          user_id: w.user_id,
          calendar_id: w.calendar_id,
          channel_id: nw.channel_id,
          resource_id: nw.resource_id,
          channel_token: nw.channel_token,
          expiration_ms: nw.expiration_ms,
        }, { onConflict: "user_id,calendar_id" });

        if (upErr) throw new Error(`db upsert failed: ${upErr.message}`);

        // Save calendar_name to calendar_sync_state
        await supabase.from("calendar_sync_state").upsert({
          user_id: w.user_id,
          calendar_id: w.calendar_id,
          calendar_name,
        }, { onConflict: "user_id,calendar_id" });

        renewed += 1;
      } catch (e: any) {
        errors.push({ user_id: w.user_id, calendar_id: w.calendar_id, error: String(e?.message ?? e) });
      }
    }

    return new Response(JSON.stringify({ ok: true, renewed, errors, within_hours: withinHours }), {
      headers: { "Content-Type": "application/json" },
    });
  } catch (e: any) {
    return new Response(String(e?.message ?? e), { status: 500 });
  }
});