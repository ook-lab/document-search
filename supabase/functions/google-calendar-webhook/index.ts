// supabase/functions/google-calendar-webhook/index.ts
import { serve } from "https://deno.land/std@0.224.0/http/server.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

function mustGetEnv(name: string): string {
  const v = Deno.env.get(name);
  if (!v) throw new Error(`Missing env: ${name}`);
  return v;
}

serve(async (req) => {
  try {
    // Google push sends empty body; all meaningful data is in headers.
    const PROJECT_URL = mustGetEnv("PROJECT_URL");
    const SERVICE_ROLE_KEY = mustGetEnv("SERVICE_ROLE_KEY");
    const supabase = createClient(PROJECT_URL, SERVICE_ROLE_KEY, { auth: { persistSession: false } });

    const channelId = req.headers.get("x-goog-channel-id");
    const resourceId = req.headers.get("x-goog-resource-id");
    const resourceState = req.headers.get("x-goog-resource-state"); // "sync" / "exists" / "not_exists"
    const channelToken = req.headers.get("x-goog-channel-token");   // we will set this when creating watch
    const messageNumber = req.headers.get("x-goog-message-number") ?? "0";

    // Always respond quickly to Google.
    if (!channelId || !resourceId) return new Response("ok", { status: 200 });

    // 1) Find matching watch record
    const { data: watch, error: werr } = await supabase
      .from("calendar_watches")
      .select("user_id, calendar_id, channel_id, resource_id, channel_token")
      .eq("channel_id", channelId)
      .maybeSingle();

    if (werr) return new Response("ok", { status: 200 });

    // 2) Verify token/resource_id matches what we expect (basic spoofing defense)
    if (!watch) return new Response("ok", { status: 200 });
    if (watch.resource_id !== resourceId) return new Response("ok", { status: 200 });
    if (watch.channel_token !== (channelToken ?? "")) return new Response("ok", { status: 200 });

    // sync 完了後に calendar-index-sync を呼ぶ（fire and forget）
    const syncUrl = `${PROJECT_URL}/functions/v1/google-calendar-sync?user_id=${encodeURIComponent(watch.user_id)}&calendar_id=${encodeURIComponent(watch.calendar_id)}`;
    const indexUrl = `${PROJECT_URL}/functions/v1/calendar-index-sync?user_id=${encodeURIComponent(watch.user_id)}&calendar_id=${encodeURIComponent(watch.calendar_id)}`;
    const authHeader = { Authorization: `Bearer ${SERVICE_ROLE_KEY}` };

    fetch(syncUrl, { method: "GET", headers: authHeader })
      .then((r) => {
        if (r.ok) fetch(indexUrl, { method: "GET", headers: authHeader }).catch(() => {});
      })
      .catch(() => {});

    return new Response("ok", { status: 200 });
  } catch {
    // Never fail hard; Google will retry aggressively.
    return new Response("ok", { status: 200 });
  }
});