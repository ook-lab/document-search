// supabase/functions/google-oauth-start/index.ts
import { serve } from "https://deno.land/std@0.224.0/http/server.ts";

function mustGetEnv(name: string): string {
  const v = Deno.env.get(name);
  if (!v) throw new Error(`Missing env: ${name}`);
  return v;
}

serve(async (req) => {
  try {
    const GOOGLE_CLIENT_ID = mustGetEnv("GOOGLE_CLIENT_ID");
    const PROJECT_URL = mustGetEnv("PROJECT_URL");
    const SERVICE_ROLE_KEY = mustGetEnv("SERVICE_ROLE_KEY");
    const REDIRECT_URI = `${PROJECT_URL}/functions/v1/google-oauth-callback`;

    const url = new URL(req.url);
    const user_id = url.searchParams.get("user_id");
    if (!user_id) return new Response("missing user_id", { status: 400 });

    const state = crypto.randomUUID();

    const scope = encodeURIComponent("https://www.googleapis.com/auth/calendar");
    const authUrl =
      `https://accounts.google.com/o/oauth2/v2/auth` +
      `?client_id=${encodeURIComponent(GOOGLE_CLIENT_ID)}` +
      `&redirect_uri=${encodeURIComponent(REDIRECT_URI)}` +
      `&response_type=code` +
      `&access_type=offline` +
      `&prompt=consent` +
      `&include_granted_scopes=true` +
      `&scope=${scope}` +
      `&state=${encodeURIComponent(`${user_id}:${state}`)}`;

    return Response.redirect(authUrl, 302);
  } catch (e) {
    return new Response(String(e?.message ?? e), { status: 500 });
  }
});