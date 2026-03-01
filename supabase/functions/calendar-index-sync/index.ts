// supabase/functions/calendar-index-sync/index.ts
//
// Google Calendar イベントを Rawdata_FILE_AND_MAIL + 10_ix_search_index に同期する。
// google-calendar-webhook から google-calendar-sync 完了後に呼び出される。

import { serve } from "https://deno.land/std@0.224.0/http/server.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

function mustGetEnv(name: string): string {
  const v = Deno.env.get(name);
  if (!v) throw new Error(`Missing env: ${name}`);
  return v;
}

// イベント1件 → 検索用テキスト
function buildChunkText(ev: any): string {
  const payload = ev.payload ?? {};
  const title = payload.summary ?? "(タイトルなし)";

  const parts: string[] = [title];

  const startDt: string | undefined = payload.start?.dateTime;
  const startDate: string | undefined = payload.start?.date;
  const endDt: string | undefined = payload.end?.dateTime;

  if (startDt) {
    const s = new Date(startDt);
    const dateStr = s.toLocaleDateString("ja-JP", {
      timeZone: "Asia/Tokyo",
      year: "numeric", month: "numeric", day: "numeric",
    });
    const startTime = s.toLocaleTimeString("ja-JP", {
      timeZone: "Asia/Tokyo", hour: "2-digit", minute: "2-digit",
    });
    if (endDt) {
      const e = new Date(endDt);
      const endTime = e.toLocaleTimeString("ja-JP", {
        timeZone: "Asia/Tokyo", hour: "2-digit", minute: "2-digit",
      });
      parts.push(`日時: ${dateStr} ${startTime}〜${endTime}`);
    } else {
      parts.push(`日時: ${dateStr} ${startTime}`);
    }
  } else if (startDate) {
    parts.push(`日付: ${startDate}（終日）`);
  }

  if (payload.location)    parts.push(`場所: ${payload.location}`);
  if (payload.description) parts.push(`説明: ${String(payload.description).slice(0, 300)}`);

  return parts.join("\n");
}

// OpenAI text-embedding-3-small (1536次元)
async function generateEmbedding(text: string, apiKey: string): Promise<number[]> {
  const res = await fetch("https://api.openai.com/v1/embeddings", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${apiKey}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ model: "text-embedding-3-small", input: text }),
  });
  const j = await res.json();
  if (!res.ok) throw new Error(`embedding API error: ${JSON.stringify(j)}`);
  return j.data[0].embedding as number[];
}

serve(async (req) => {
  try {
    const PROJECT_URL      = mustGetEnv("PROJECT_URL");
    const SERVICE_ROLE_KEY = mustGetEnv("SERVICE_ROLE_KEY");
    const OPENAI_API_KEY   = mustGetEnv("OPENAI_API_KEY");

    const supabase = createClient(PROJECT_URL, SERVICE_ROLE_KEY, {
      auth: { persistSession: false },
    });

    const url         = new URL(req.url);
    const user_id     = url.searchParams.get("user_id");
    const calendar_id = url.searchParams.get("calendar_id") ?? "primary";
    if (!user_id) return new Response("missing user_id", { status: 400 });

    // 1) calendar_sync_state から calendar_name と index_enabled を取得
    const { data: syncState } = await supabase
      .from("calendar_sync_state")
      .select("calendar_name, index_enabled")
      .eq("user_id", user_id)
      .eq("calendar_id", calendar_id)
      .maybeSingle();

    // index_enabled = false なら何もしない
    if (!syncState?.index_enabled) {
      return new Response(
        JSON.stringify({ ok: true, skipped: true, reason: "index_enabled=false" }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }

    const calendar_name: string = syncState?.calendar_name ?? calendar_id;

    // 2) アクティブなイベントを calendar_events から取得
    const { data: events, error: evErr } = await supabase
      .from("calendar_events")
      .select("*")
      .eq("user_id", user_id)
      .eq("calendar_id", calendar_id)
      .neq("status", "cancelled")
      .not("start_ts", "is", null);

    if (evErr) return new Response(`events fetch error: ${evErr.message}`, { status: 500 });

    // 3) Rawdata_FILE_AND_MAIL の既存レコードを検索（metadata->>'calendar_id' で特定）
    const { data: existingDoc } = await supabase
      .from("Rawdata_FILE_AND_MAIL")
      .select("id")
      .eq("owner_id", user_id)
      .eq("doc_type", "GOOGLE_CALENDAR")
      .filter("metadata->>calendar_id", "eq", calendar_id)
      .maybeSingle();

    let document_id: string;

    if (existingDoc?.id) {
      document_id = existingDoc.id;
      await supabase
        .from("Rawdata_FILE_AND_MAIL")
        .update({
          workspace:       calendar_name,
          display_subject: calendar_name,
          status:          "processed",
        })
        .eq("id", document_id);
    } else {
      const { data: newDoc, error: insErr } = await supabase
        .from("Rawdata_FILE_AND_MAIL")
        .insert({
          owner_id:        user_id,
          doc_type:        "GOOGLE_CALENDAR",
          workspace:       calendar_name,
          display_subject: calendar_name,
          display_sent_at: new Date().toISOString(),
          status:          "processed",
          chunk_count:     0,
          metadata:        { calendar_id },
        })
        .select("id")
        .single();

      if (insErr) return new Response(`doc insert error: ${insErr.message}`, { status: 500 });
      document_id = newDoc.id;
    }

    // 4) 既存チャンクを削除（全件再インデックス）
    await supabase
      .from("10_ix_search_index")
      .delete()
      .eq("document_id", document_id);

    // 5) イベントをチャンク化 → embedding → 保存
    let saved = 0;
    let failed = 0;

    for (const ev of (events ?? [])) {
      const chunk_text = buildChunkText(ev);
      if (!chunk_text.trim()) continue;

      try {
        const embedding = await generateEmbedding(chunk_text, OPENAI_API_KEY);

        const { error: chunkErr } = await supabase
          .from("10_ix_search_index")
          .insert({
            document_id,
            owner_id:      user_id,
            chunk_content: chunk_text,
            chunk_size:    chunk_text.length,
            chunk_type:    "calendar_event",
            embedding,
            search_weight: 1.3,
            chunk_index:   saved,
            chunk_metadata: {
              event_id:      ev.event_id,
              calendar_id:   ev.calendar_id,
              start_ts:      ev.start_ts,
              end_ts:        ev.end_ts,
              structure_type: "calendar_event",
              event_updated: ev.updated,
            },
          });

        if (chunkErr) throw new Error(chunkErr.message);
        saved++;
      } catch (e: any) {
        failed++;
        console.error(`embed failed ${ev.event_id}: ${e?.message}`);
      }
    }

    // 6) chunk_count 更新
    await supabase
      .from("Rawdata_FILE_AND_MAIL")
      .update({ chunk_count: saved })
      .eq("id", document_id);

    return new Response(JSON.stringify({
      ok: true,
      calendar_id,
      calendar_name,
      document_id,
      total:  events?.length ?? 0,
      saved,
      failed,
    }), { status: 200, headers: { "Content-Type": "application/json" } });

  } catch (e: any) {
    return new Response(String(e?.message ?? e), { status: 500 });
  }
});
