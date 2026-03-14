// supabase/functions/google-oauth-callback/index.ts
import { serve } from "https://deno.land/std@0.224.0/http/server.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

function mustGetEnv(name: string): string {
  const v = Deno.env.get(name);
  if (!v) throw new Error(`Missing env: ${name}`);
  return v;
}

serve(async (req) => {
  try {
    const PROJECT_URL = mustGetEnv("PROJECT_URL");
    const SERVICE_ROLE_KEY = mustGetEnv("SERVICE_ROLE_KEY");

    const GOOGLE_CLIENT_ID = mustGetEnv("GOOGLE_CLIENT_ID");
    const GOOGLE_CLIENT_SECRET = mustGetEnv("GOOGLE_CLIENT_SECRET");

    const url = new URL(req.url);
    const code = url.searchParams.get("code");
    const state = url.searchParams.get("state"); // we set: `${user_id}:${random}`
    const error = url.searchParams.get("error");

    if (error) return new Response(`OAuth error: ${error}`, { status: 400 });
    if (!code) return new Response("missing code", { status: 400 });
    if (!state || !state.includes(":")) return new Response("missing/invalid state", { status: 400 });

    const user_id = state.split(":")[0];
    if (!user_id) return new Response("missing user_id in state", { status: 400 });

    const redirectUri = `${PROJECT_URL}/functions/v1/google-oauth-callback`;

    // Exchange authorization code -> tokens
    const tokenRes = await fetch("https://oauth2.googleapis.com/token", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        code,
        client_id: GOOGLE_CLIENT_ID,
        client_secret: GOOGLE_CLIENT_SECRET,
        redirect_uri: redirectUri,
        grant_type: "authorization_code",
      }),
    });

    const tokenJson = await tokenRes.json();
    if (!tokenRes.ok) {
      return new Response(`token exchange failed: ${JSON.stringify(tokenJson)}`, { status: 400 });
    }

    const refresh_token: string | undefined = tokenJson.refresh_token;
    const access_token: string | undefined = tokenJson.access_token;
    const expires_in: number | undefined = tokenJson.expires_in;

    // NOTE: refresh_token may be missing if Google didn't issue it (e.g. already granted w/o prompt=consent).
    if (!refresh_token) {
      return new Response(
        "No refresh_token returned. Try again after revoking app access in your Google Account, then re-consent.",
        { status: 400 },
      );
    }

    const access_token_expires_at = (typeof expires_in === "number")
      ? new Date(Date.now() + expires_in * 1000).toISOString()
      : null;

    const supabase = createClient(PROJECT_URL, SERVICE_ROLE_KEY, {
      auth: { persistSession: false },
    });

    const { error: upsertErr } = await supabase
      .from("google_oauth_tokens")
      .upsert({
        user_id,
        refresh_token,
        access_token: access_token ?? null,
        access_token_expires_at,
      }, { onConflict: "user_id" });

    if (upsertErr) {
      return new Response(`db upsert failed: ${upsertErr.message}`, { status: 500 });
    }

    return new Response(
      "OAuth success. refresh_token stored. You can close this tab.",
      { status: 200, headers: { "Content-Type": "text/plain; charset=utf-8" } },
    );
  } catch (e) {
    return new Response(String(e?.message ?? e), { status: 500 });
  }
});