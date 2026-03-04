// supabase/functions/calendar-index-sync/index.ts
//
// Google Calendar デルタ同期 + インデックス更新を一括実行。
// （旧 google-calendar-sync は廃止・統合。calendar_events 中間テーブル不要）
//
// フロー: Google Calendar API → 02_gcal_01_raw → 09_unified_documents → 10_ix_search_index

import { serve } from "https://deno.land/std@0.224.0/http/server.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const RAW_TABLE     = "02_gcal_01_raw";
const UNIFIED_TABLE = "09_unified_documents";
const INDEX_TABLE   = "10_ix_search_index";

function mustGetEnv(name: string): string {
  const v = Deno.env.get(name);
  if (!v) throw new Error(`Missing env: ${name}`);
  return v;
}

async function refreshAccessToken(
  refreshToken: string,
  clientId: string,
  clientSecret: string,
): Promise<{ access_token: string }> {
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
  if (!res.ok) throw new Error(`token refresh failed: ${JSON.stringify(j)}`);
  return { access_token: j.access_token };
}

function isoOrNull(v: unknown): string | null {
  if (!v) return null;
  const d = new Date(String(v));
  return isNaN(d.getTime()) ? null : d.toISOString();
}

function parseStartEnd(ev: any): { start_at: string | null; end_at: string | null } {
  const start = ev?.start?.dateTime ?? ev?.start?.date ?? null;
  const end   = ev?.end?.dateTime   ?? ev?.end?.date   ?? null;
  return { start_at: isoOrNull(start), end_at: isoOrNull(end) };
}

async function listEventsPage(
  accessToken: string,
  calendarId: string,
  params: Record<string, string>,
): Promise<any> {
  const u = new URL(
    `https://www.googleapis.com/calendar/v3/calendars/${encodeURIComponent(calendarId)}/events`,
  );
  for (const [k, v] of Object.entries(params)) u.searchParams.set(k, v);
  // conferenceData を取得するには conferenceDataVersion=1 が必要
  u.searchParams.set("conferenceDataVersion", "1");
  // 参加者が多いイベントも全員取得
  u.searchParams.set("maxAttendees", "100");
  const res = await fetch(u.toString(), {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  const j = await res.json();
  if (!res.ok) throw new Error(`events.list failed: ${JSON.stringify(j)}`);
  return j;
}

function buildChunkText(payload: any, startAt: string | null, endAt: string | null): string {
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

  if (payload.location) parts.push(`場所: ${payload.location}`);

  // 会議URL（Google Meet 等）
  const meetUrl = payload.hangoutLink
    ?? payload.conferenceData?.entryPoints?.find((e: any) => e.entryPointType === "video")?.uri
    ?? null;
  if (meetUrl) parts.push(`会議URL: ${meetUrl}`);

  // 参加者（最大10名まで）
  if (Array.isArray(payload.attendees) && payload.attendees.length > 0) {
    const names = payload.attendees
      .slice(0, 10)
      .map((a: any) => a.displayName ?? a.email ?? "")
      .filter(Boolean);
    if (names.length > 0) parts.push(`参加者: ${names.join(", ")}`);
  }

  if (payload.description) parts.push(`説明: ${String(payload.description).slice(0, 300)}`);

  return parts.join("\n");
}

function buildGcalUiData(payload: any, startAt: string | null): object {
  const summary     = payload.summary     ?? null;
  const description = payload.description ?? null;
  const location    = payload.location    ?? null;

  const timeline: object[] = [];
  if (summary) {
    timeline.push({ event: summary, date: startAt ?? "", location, description });
  }
  const sections: object[] = [];
  if (description) {
    sections.push({ title: summary ?? "", body: description });
  }
  return { sections, tables: [], timeline, actions: [], notices: [] };
}

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
    const PROJECT_URL          = mustGetEnv("PROJECT_URL");
    const SERVICE_ROLE_KEY     = mustGetEnv("SERVICE_ROLE_KEY");
    const GOOGLE_CLIENT_ID     = mustGetEnv("GOOGLE_CLIENT_ID");
    const GOOGLE_CLIENT_SECRET = mustGetEnv("GOOGLE_CLIENT_SECRET");
    const OPENAI_API_KEY       = mustGetEnv("OPENAI_API_KEY");

    const supabase = createClient(PROJECT_URL, SERVICE_ROLE_KEY, {
      auth: { persistSession: false },
    });

    const url         = new URL(req.url);
    const user_id     = url.searchParams.get("user_id");
    const calendar_id = url.searchParams.get("calendar_id") ?? "primary";
    if (!user_id) return new Response("missing user_id", { status: 400 });

    // 1) index_enabled + 現在の syncToken を取得
    const { data: syncState } = await supabase
      .from("calendar_sync_state")
      .select("calendar_name, index_enabled, next_sync_token, person")
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
    const person: string = syncState.person || calendar_name;
    let syncToken: string | null = syncState.next_sync_token ?? null;

    // 2) OAuth refresh_token 取得
    const { data: tok } = await supabase
      .from("google_oauth_tokens")
      .select("refresh_token")
      .eq("user_id", user_id)
      .maybeSingle();

    if (!tok?.refresh_token) return new Response("no refresh_token for user", { status: 400 });

    // 3) アクセストークン更新
    const { access_token } = await refreshAccessToken(
      tok.refresh_token,
      GOOGLE_CLIENT_ID,
      GOOGLE_CLIENT_SECRET,
    );

    // 4) Google Calendar API からデルタ取得
    const daysPast   = Number(url.searchParams.get("days_past")   ?? "180");
    const daysFuture = Number(url.searchParams.get("days_future") ?? "365");

    const baseParams: Record<string, string> = {
      singleEvents: "true",
      showDeleted:  "true",
      maxResults:   "2500",
    };
    if (!syncToken) {
      const now     = new Date();
      const timeMin = new Date(now.getTime() - daysPast   * 86400_000);
      const timeMax = new Date(now.getTime() + daysFuture * 86400_000);
      baseParams.timeMin = timeMin.toISOString();
      baseParams.timeMax = timeMax.toISOString();
    } else {
      baseParams.syncToken = syncToken;
    }

    let pageToken: string | null = null;
    let fetched  = 0;
    let saved    = 0;
    let deleted  = 0;
    let failed   = 0;

    while (true) {
      const params = { ...baseParams };
      if (pageToken) params.pageToken = pageToken;

      let page: any;
      try {
        page = await listEventsPage(access_token, calendar_id, params);
      } catch (e: any) {
        const msg = String(e?.message ?? e);
        if (msg.includes('"code":410') || msg.includes("410")) {
          // syncToken 失効 → クリアして呼び直しを促す
          await supabase.from("calendar_sync_state").upsert({
            user_id,
            calendar_id,
            next_sync_token:   null,
            last_full_sync_at: new Date().toISOString(),
          }, { onConflict: "user_id,calendar_id" });
          return new Response(
            JSON.stringify({ ok: false, reason: "syncToken expired (410). cleared; rerun for full sync." }),
            { status: 409, headers: { "Content-Type": "application/json" } },
          );
        }
        throw e;
      }

      const items: any[] = Array.isArray(page.items) ? page.items : [];
      fetched += items.length;

      for (const ev of items) {
        const event_id = ev?.id;
        if (!event_id) continue;

        try {
          if (ev.status === "cancelled") {
            // キャンセル済み → 02_raw → 09_unified → 10_ix の順で削除
            const { data: rawRows } = await supabase
              .from(RAW_TABLE).select("id")
              .eq("event_id", event_id).eq("calendar_id", calendar_id);

            for (const raw of (rawRows ?? [])) {
              const { data: unifiedDocs } = await supabase
                .from(UNIFIED_TABLE).select("id")
                .eq("raw_id", raw.id).eq("raw_table", RAW_TABLE);
              for (const ud of (unifiedDocs ?? [])) {
                await supabase.from(INDEX_TABLE).delete().eq("doc_id", ud.id);
              }
              await supabase.from(UNIFIED_TABLE).delete()
                .eq("raw_id", raw.id).eq("raw_table", RAW_TABLE);
              await supabase.from(RAW_TABLE).delete().eq("id", raw.id);
            }
            deleted++;
          } else {
            // 追加 / 更新
            const { start_at, end_at } = parseStartEnd(ev);
            const payload = ev;

            // --- 02_gcal_01_raw に UPSERT ---
            const rawRow = {
              // 共通
              person,
              source:             "GOOGLE_CALENDAR",
              category:           calendar_name,

              // 識別
              event_id,
              calendar_id,
              i_cal_uid:          payload.iCalUID              ?? null,
              etag:               payload.etag                 ?? null,
              sequence:           payload.sequence             ?? null,

              // 基本情報
              summary:            payload.summary              ?? null,
              description:        payload.description          ?? null,
              location:           payload.location             ?? null,
              status:             payload.status               ?? null,
              visibility:         payload.visibility           ?? null,
              transparency:       payload.transparency         ?? null,
              color_id:           payload.colorId              ?? null,

              // 日時
              start_raw:          payload.start                ?? null,
              end_raw:            payload.end                  ?? null,
              is_all_day:         !!(payload.start?.date && !payload.start?.dateTime),
              start_at,
              end_at,
              end_time_unspecified: payload.endTimeUnspecified ?? null,
              original_start_time:  payload.originalStartTime ?? null,

              // 作成・更新
              created_at:         payload.created              ?? null,
              updated_at:         payload.updated              ?? null,

              // 主催者・作成者
              creator_email:      payload.creator?.email        ?? null,
              creator_name:       payload.creator?.displayName  ?? null,
              organizer_email:    payload.organizer?.email      ?? null,
              organizer_name:     payload.organizer?.displayName ?? null,

              // 参加者
              attendees:          payload.attendees             ?? null,
              attendees_omitted:  payload.attendeesOmitted      ?? null,

              // ゲスト権限
              guests_can_invite_others:     payload.guestsCanInviteOthers    ?? null,
              guests_can_modify:            payload.guestsCanModify          ?? null,
              guests_can_see_other_guests:  payload.guestsCanSeeOtherGuests  ?? null,
              anyone_can_add_self:          payload.anyoneCanAddSelf         ?? null,

              // 繰り返し
              recurrence:         payload.recurrence            ?? null,
              recurring_event_id: payload.recurringEventId      ?? null,

              // URL・会議
              source_url:         payload.htmlLink              ?? null,
              hangout_link:       payload.hangoutLink           ?? null,
              conference_data:    payload.conferenceData        ?? null,

              // 添付ファイル
              attachments:        payload.attachments           ?? null,

              // 通知
              reminders:          payload.reminders             ?? null,

              // イベント種別
              event_type:                    payload.eventType                    ?? null,
              focus_time_properties:         payload.focusTimeProperties          ?? null,
              out_of_office_properties:      payload.outOfOfficeProperties        ?? null,
              working_location_properties:   payload.workingLocationProperties    ?? null,

              // カスタム・その他
              extended_properties: payload.extendedProperties   ?? null,
              source_title:        payload.source?.title        ?? null,
              private_copy:        payload.privateCopy          ?? null,
              locked:              payload.locked               ?? null,
            };

            const { data: upsertedRaw, error: rawErr } = await supabase
              .from(RAW_TABLE)
              .upsert(rawRow, { onConflict: "event_id,calendar_id" })
              .select("id").single();
            if (rawErr) throw new Error(`02_gcal_01_raw upsert: ${rawErr.message}`);
            const raw_id = upsertedRaw.id;

            // --- 09_unified_documents に UPSERT ---
            const ui_data    = buildGcalUiData(payload, start_at);
            const unifiedDoc = {
              raw_id,
              raw_table:   RAW_TABLE,
              person,
              source:      "GOOGLE_CALENDAR",
              category:    calendar_name,
              title:       payload.summary              ?? null,
              file_url:    payload.htmlLink              ?? null,
              from_email:  payload.organizer?.email      ?? null,
              from_name:   payload.organizer?.displayName ?? null,
              snippet:     null,
              post_at:     null,
              start_at,
              end_at,
              location:    payload.location              ?? null,
              due_date:    null,
              post_type:   null,
              ui_data,
              meta: {
                attendees:          payload.attendees             ?? null,
                recurrence:         payload.recurrence            ?? null,
                recurring_event_id: payload.recurringEventId      ?? null,
                creator_email:      payload.creator?.email        ?? null,
                creator_name:       payload.creator?.displayName  ?? null,
                visibility:         payload.visibility            ?? null,
                calendar_id,
              },
            };

            const { data: existingUnified } = await supabase
              .from(UNIFIED_TABLE).select("id")
              .eq("raw_id", raw_id).eq("raw_table", RAW_TABLE).maybeSingle();

            let doc_id: string;
            if (existingUnified) {
              doc_id = existingUnified.id;
              await supabase.from(UNIFIED_TABLE).update(unifiedDoc).eq("id", doc_id);
            } else {
              const { data: inserted, error: insErr } = await supabase
                .from(UNIFIED_TABLE).insert(unifiedDoc).select("id").single();
              if (insErr) throw new Error(`09_unified_documents insert: ${insErr.message}`);
              doc_id = inserted.id;
            }

            // --- 10_ix_search_index に embedding 保存 ---
            const chunk_text = buildChunkText(payload, start_at, end_at);
            if (chunk_text.trim()) {
              const embedding = await generateEmbedding(chunk_text, OPENAI_API_KEY);
              await supabase.from(INDEX_TABLE).delete().eq("doc_id", doc_id);
              const { error: chunkErr } = await supabase.from(INDEX_TABLE).insert({
                doc_id,
                person,
                source:       "GOOGLE_CALENDAR",
                category:     calendar_name,
                chunk_index:  0,
                chunk_text,
                chunk_type:   "calendar_event",
                chunk_weight: 1.3,
                embedding,
              });
              if (chunkErr) throw new Error(`10_ix_search_index insert: ${chunkErr.message}`);
            }

            saved++;
          }
        } catch (e: any) {
          failed++;
          console.error(`failed event ${event_id}: ${e?.message}`);
        }
      }

      pageToken = page.nextPageToken ?? null;

      if (!pageToken) {
        const nextSyncToken = page.nextSyncToken ?? null;
        if (nextSyncToken) {
          await supabase.from("calendar_sync_state").upsert({
            user_id,
            calendar_id,
            next_sync_token:   nextSyncToken,
            last_full_sync_at: syncToken ? null : new Date().toISOString(),
          }, { onConflict: "user_id,calendar_id" });
        }

        return new Response(JSON.stringify({
          ok: true,
          calendar_id,
          calendar_name,
          mode:    syncToken ? "delta" : "full-window",
          fetched,
          saved,
          deleted,
          failed,
        }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
    }
  } catch (e: any) {
    console.error(`[calendar-index-sync] fatal:`, e?.message ?? e);
    return new Response(String(e?.message ?? e), { status: 500 });
  }
});
