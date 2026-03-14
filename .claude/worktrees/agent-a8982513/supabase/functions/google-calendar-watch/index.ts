// supabase/functions/google-calendar-watch/index.ts
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

serve(async (req) => {
  try {
    const PROJECT_URL = mustGetEnv("PROJECT_URL");
    const SERVICE_ROLE_KEY = mustGetEnv("SERVICE_ROLE_KEY");
    const GOOGLE_CLIENT_ID = mustGetEnv("GOOGLE_CLIENT_ID");
    const GOOGLE_CLIENT_SECRET = mustGetEnv("GOOGLE_CLIENT_SECRET");

    const supabase = createClient(PROJECT_URL, SERVICE_ROLE_KEY, { auth: { persistSession: false } });

    // call: /functions/v1/google-calendar-watch?user_id=...&calendar_id=primary
    const url = new URL(req.url);
    const user_id = url.searchParams.get("user_id");
    const calendar_id = url.searchParams.get("calendar_id") ?? "primary";
    if (!user_id) return new Response("missing user_id", { status: 400 });

    // 1) get refresh_token
    const { data: tok, error: tokErr } = await supabase
      .from("google_oauth_tokens")
      .select("refresh_token")
      .eq("user_id", user_id)
      .maybeSingle();

    if (tokErr) return new Response(`token fetch error: ${tokErr.message}`, { status: 500 });
    if (!tok?.refresh_token) return new Response("no refresh_token for user", { status: 400 });

    // 2) refresh access token
    const { access_token } = await refreshAccessToken(tok.refresh_token, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET);

    // 2.5) get calendar name
    const calRes = await fetch(
      `https://www.googleapis.com/calendar/v3/calendars/${encodeURIComponent(calendar_id)}`,
      { headers: { Authorization: `Bearer ${access_token}` } },
    );
    const calJson = await calRes.json();
    const calendar_name: string = calJson.summary ?? calendar_id;

    // 3) create watch
    const channel_id = crypto.randomUUID();
    const channel_token = crypto.randomUUID(); // webhook spoofing防止（DBと照合する）

    const webhookAddress = `${PROJECT_URL}/functions/v1/google-calendar-webhook`;

    const watchRes = await fetch(
      `https://www.googleapis.com/calendar/v3/calendars/${encodeURIComponent(calendar_id)}/events/watch`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${access_token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          id: channel_id,
          type: "web_hook",
          address: webhookAddress,
          token: channel_token,
          // params: { ttl: "86400" }  // optional (seconds). Google may ignore/override.
        }),
      },
    );

    const watchJson = await watchRes.json();
    if (!watchRes.ok) {
      return new Response(`watch failed: ${JSON.stringify(watchJson)}`, { status: 400 });
    }

    const resource_id = watchJson.resourceId as string;
    const expiration_ms = watchJson.expiration ? Number(watchJson.expiration) : null;

    // 4) save watch info
    const { error: upErr } = await supabase.from("calendar_watches").upsert({
      user_id,
      calendar_id,
      channel_id,
      resource_id,
      channel_token,
      expiration_ms,
    }, { onConflict: "user_id,calendar_id" });

    // 4.5) save calendar_name to calendar_sync_state
    await supabase.from("calendar_sync_state").upsert({
      user_id,
      calendar_id,
      calendar_name,
    }, { onConflict: "user_id,calendar_id" });

    if (upErr) return new Response(`db upsert failed: ${upErr.message}`, { status: 500 });

    return new Response(JSON.stringify({
      ok: true,
      calendar_id,
      calendar_name,
      channel_id,
      resource_id,
      expiration_ms,
      webhook: webhookAddress,
    }), { status: 200, headers: { "Content-Type": "application/json" } });
  } catch (e) {
    return new Response(String(e?.message ?? e), { status: 500 });
  }
});