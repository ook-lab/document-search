/**
 * Supabase tasks → Trello カード同期
 * POST /api/trello/sync  → trello_card_id が null のタスクをTrelloに新規作成
 *
 * 環境変数:
 *   TRELLO_API_KEY
 *   TRELLO_TOKEN
 *   TRELLO_LIST_TODO   「ToDo」リストID
 *   TRELLO_LIST_DOING  「Doing」リストID
 *   TRELLO_LIST_DONE   「Done」リストID
 */

const SUPABASE_URL  = process.env.SUPABASE_URL!;
const SUPABASE_KEY  = process.env.SUPABASE_SERVICE_ROLE_KEY!;
const OWNER_EMAIL   = process.env.CALENDAR_OWNER_EMAIL ?? "ookubo.y@workspace-o.com";
const TRELLO_KEY    = process.env.TRELLO_API_KEY!;
const TRELLO_TOKEN  = process.env.TRELLO_TOKEN!;

const STATUS_LIST: Record<string, string> = {
  todo:  process.env.TRELLO_LIST_TODO  ?? "",
  doing: process.env.TRELLO_LIST_DOING ?? "",
  done:  process.env.TRELLO_LIST_DONE  ?? "",
};

function sbHeaders() {
  return {
    "apikey": SUPABASE_KEY,
    "Authorization": `Bearer ${SUPABASE_KEY}`,
    "Content-Type": "application/json",
    "Prefer": "return=representation",
  };
}

export async function POST() {
  try {
    if (!TRELLO_KEY || !TRELLO_TOKEN) {
      return Response.json({ error: "TRELLO_API_KEY / TRELLO_TOKEN が未設定" }, { status: 500 });
    }

    // trello_card_id が null のタスクだけ取得
    const res = await fetch(
      `${SUPABASE_URL}/rest/v1/tasks?owner_email=eq.${encodeURIComponent(OWNER_EMAIL)}&trello_card_id=is.null`,
      { headers: sbHeaders() }
    );
    const tasks: Record<string, unknown>[] = await res.json();

    const results = [];
    for (const task of tasks) {
      const listId = STATUS_LIST[task.status as string] ?? STATUS_LIST.todo;
      if (!listId) continue;

      const params = new URLSearchParams({
        key:    TRELLO_KEY,
        token:  TRELLO_TOKEN,
        idList: listId,
        name:   task.title as string,
        desc:   (task.description as string) ?? "",
        ...(task.due_date ? { due: `${task.due_date}T00:00:00.000Z` } : {}),
      });

      const cardRes = await fetch(`https://api.trello.com/1/cards?${params}`, { method: "POST" });
      if (!cardRes.ok) {
        console.error("[trello sync] card create failed:", await cardRes.text());
        continue;
      }
      const card = await cardRes.json();

      // Supabase に trello_card_id を保存
      await fetch(
        `${SUPABASE_URL}/rest/v1/tasks?id=eq.${task.id}`,
        {
          method: "PATCH",
          headers: sbHeaders(),
          body: JSON.stringify({
            trello_card_id: card.id,
            trello_list_id: listId,
            updated_at: new Date().toISOString(),
          }),
        }
      );
      results.push({ taskId: task.id, trelloCardId: card.id });
    }

    return Response.json({ synced: results.length, results });
  } catch (e) {
    console.error("[trello sync] exception:", String(e));
    return Response.json({ error: String(e) }, { status: 500 });
  }
}
