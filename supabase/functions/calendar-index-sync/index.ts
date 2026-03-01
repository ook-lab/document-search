// supabase/functions/calendar-index-sync/index.ts
//
// Google Calendar イベントを Rawdata_FILE_AND_MAIL に1イベント1レコードで同期し、
// 10_ix_search_index にベクトルインデックスを作成する。
//
// Rawdata_FILE_AND_MAIL のカラム対応:
//   display_subject  = 件名 (summary)
//   display_post_text = 説明 (description)
//   start_ts         = 開始日時
//   end_ts           = 終了日時
//   display_sent_at  = 開始日時（既存の日付フィルター用に同値を入れる）
//   workspace        = カレンダー名
//   metadata         = { calendar_id, event_id }

import { serve } from "https://deno.land/std@0.224.0/http/server.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

function mustGetEnv(name: string): string {
  const v = Deno.env.get(name);
  if (!v) throw new Error(`Missing env: ${name}`);
  return v;
}

// イベント1件の検索用チャンクテキストを生成
function buildChunkText(payload: any, startTs: string | null, endTs: string | null): string {
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

    // 1) index_enabled チェック
    const { data: syncState } = await supabase
      .from("calendar_sync_state")
      .select("calendar_name, index_enabled")
      .eq("user_id", user_id)
      .eq("calendar_id", calendar_id)
      .maybeSingle();

    if (!syncState?.index_enabled) {
      return new Response(
        JSON.stringify({ ok: true, skipped: true, reason: "index_enabled=false" }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }

    const calendar_name: string = syncState.calendar_name ?? calendar_id;

    // 2) アクティブなイベントを取得
    const { data: events, error: evErr } = await supabase
      .from("calendar_events")
      .select("*")
      .eq("user_id", user_id)
      .eq("calendar_id", calendar_id)
      .neq("status", "cancelled")
      .not("start_ts", "is", null);

    if (evErr) return new Response(`events fetch error: ${evErr.message}`, { status: 500 });

    // 3) 既存 Rawdata レコードを取得（event_id → document_id のマップを作る）
    const { data: existingDocs } = await supabase
      .from("Rawdata_FILE_AND_MAIL")
      .select("id, metadata")
      .eq("owner_id", user_id)
      .eq("doc_type", "GOOGLE_CALENDAR")
      .filter("metadata->>calendar_id", "eq", calendar_id);

    const eventIdToDocId = new Map<string, string>();
    for (const doc of (existingDocs ?? [])) {
      const eid = doc.metadata?.event_id;
      if (eid) eventIdToDocId.set(eid, doc.id);
    }

    // 4) アクティブでなくなったイベントの Rawdata + チャンクを削除
    const activeEventIds = new Set((events ?? []).map((e: any) => e.event_id));
    for (const [eid, docId] of eventIdToDocId) {
      if (!activeEventIds.has(eid)) {
        await supabase.from("10_ix_search_index").delete().eq("document_id", docId);
        await supabase.from("Rawdata_FILE_AND_MAIL").delete().eq("id", docId);
      }
    }

    // 5) 各イベントを upsert → チャンク生成 → embedding 保存
    let saved = 0;
    let failed = 0;

    for (const ev of (events ?? [])) {
      const payload    = ev.payload ?? {};
      const event_id   = ev.event_id as string;
      const title      = payload.summary    ?? "(タイトルなし)";
      const description: string | null = payload.description ?? null;
      const location:    string | null = payload.location    ?? null;
      const startTs: string | null = ev.start_ts ?? null;
      const endTs:   string | null = ev.end_ts   ?? null;

      try {
        // Rawdata upsert（既存あれば update、なければ insert）
        let document_id: string;

        if (eventIdToDocId.has(event_id)) {
          document_id = eventIdToDocId.get(event_id)!;
          await supabase
            .from("Rawdata_FILE_AND_MAIL")
            .update({
              display_subject:   title,
              display_post_text: description,
              display_sent_at:   startTs,
              start_ts:          startTs,
              end_ts:            endTs,
              workspace:         calendar_name,
            })
            .eq("id", document_id);
        } else {
          const { data: newDoc, error: insErr } = await supabase
            .from("Rawdata_FILE_AND_MAIL")
            .insert({
              owner_id:          user_id,
              doc_type:          "GOOGLE_CALENDAR",
              workspace:         calendar_name,
              display_subject:   title,
              display_post_text: description,
              display_sent_at:   startTs,
              start_ts:          startTs,
              end_ts:            endTs,
              status:            "processed",
              chunk_count:       0,
              metadata:          { calendar_id, event_id },
            })
            .select("id")
            .single();

          if (insErr) throw new Error(`rawdata insert error: ${insErr.message}`);
          document_id = newDoc.id;
        }

        // 古いチャンクを削除して再生成
        await supabase.from("10_ix_search_index").delete().eq("document_id", document_id);

        const chunk_text = buildChunkText(payload, startTs, endTs);
        if (!chunk_text.trim()) continue;

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
            chunk_index:   0,
            chunk_metadata: {
              event_id,
              calendar_id,
              start_ts:      startTs,
              end_ts:        endTs,
              structure_type: "calendar_event",
              event_updated:  ev.updated,
            },
          });

        if (chunkErr) throw new Error(chunkErr.message);

        await supabase
          .from("Rawdata_FILE_AND_MAIL")
          .update({ chunk_count: 1 })
          .eq("id", document_id);

        saved++;
      } catch (e: any) {
        failed++;
        console.error(`failed event ${event_id}: ${e?.message}`);
      }
    }

    return new Response(JSON.stringify({
      ok: true,
      calendar_id,
      calendar_name,
      total:  events?.length ?? 0,
      saved,
      failed,
    }), { status: 200, headers: { "Content-Type": "application/json" } });

  } catch (e: any) {
    return new Response(String(e?.message ?? e), { status: 500 });
  }
});
