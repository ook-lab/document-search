/**
 * POST /api/trello/sync?boardId=xxx
 *   → Trello の指定ボード全カードを Supabase tasks に upsert
 * POST /api/trello/sync
 *   → 登録済み全ボードを同期
 *
 * 同期内容:
 *   - 全リスト（open + archived）を取得してtrello_listsを更新
 *   - 全カード（archived含む）をtasksにupsert
 *   - アーカイブされたリストのカードはarchived=trueとして扱う
 *   - Trelloに存在しないカード（削除済み）をSupabaseから削除
 */

const SUPABASE_URL = process.env.SUPABASE_URL!;
const SUPABASE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY!;
const OWNER_EMAIL  = process.env.CALENDAR_OWNER_EMAIL ?? "ookubo.y@workspace-o.com";
const TRELLO_KEY   = process.env.TRELLO_API_KEY ?? "";
const TRELLO_TOKEN = process.env.TRELLO_TOKEN ?? "";

function sbHeaders() {
  return {
    "apikey": SUPABASE_KEY,
    "Authorization": `Bearer ${SUPABASE_KEY}`,
    "Content-Type": "application/json",
    "Prefer": "return=representation",
  };
}

async function getMemberNames(memberIds: string[]): Promise<string[]> {
  const names: string[] = [];
  for (const id of memberIds) {
    try {
      const res = await fetch(
        `https://api.trello.com/1/members/${id}?key=${TRELLO_KEY}&token=${TRELLO_TOKEN}&fields=fullName`
      );
      if (res.ok) {
        const j = await res.json();
        if (j.fullName) names.push(j.fullName);
      }
    } catch { /* ignore */ }
  }
  return names;
}

async function getChecklistProgress(cardId: string): Promise<{ total: number; done: number }> {
  try {
    const res = await fetch(
      `https://api.trello.com/1/cards/${cardId}/checklists?key=${TRELLO_KEY}&token=${TRELLO_TOKEN}`
    );
    if (!res.ok) return { total: 0, done: 0 };
    const checklists = await res.json();
    let total = 0, done = 0;
    for (const cl of checklists) {
      for (const item of cl.checkItems ?? []) {
        total++;
        if (item.state === "complete") done++;
      }
    }
    return { total, done };
  } catch {
    return { total: 0, done: 0 };
  }
}

async function syncBoard(boardId: string): Promise<{ synced: number; errors: number }> {
  // ボード名をSupabaseから取得
  const boardRes = await fetch(
    `${SUPABASE_URL}/rest/v1/trello_boards?board_id=eq.${boardId}&select=board_name`,
    { headers: sbHeaders() }
  );
  const boardRows = await boardRes.json();
  const boardName: string = boardRows[0]?.board_name ?? "";

  // 全リスト取得（open + archived）
  const listsRes = await fetch(
    `https://api.trello.com/1/boards/${boardId}/lists?key=${TRELLO_KEY}&token=${TRELLO_TOKEN}&filter=all&fields=id,name,pos,closed`
  );
  if (!listsRes.ok) return { synced: 0, errors: 1 };
  const trelloLists: { id: string; name: string; pos: number; closed: boolean }[] = await listsRes.json();

  // オープンリストのみSupabaseにupsert
  const openLists = trelloLists.filter(l => !l.closed);
  if (openLists.length > 0) {
    await fetch(`${SUPABASE_URL}/rest/v1/trello_lists?on_conflict=list_id`, {
      method: "POST",
      headers: { ...sbHeaders(), "Prefer": "resolution=merge-duplicates" },
      body: JSON.stringify(
        openLists.map(l => ({ board_id: boardId, list_id: l.id, list_name: l.name, list_pos: l.pos ?? 0 }))
      ),
    });
  }

  // アーカイブされたリストをSupabaseから削除
  const closedLists = trelloLists.filter(l => l.closed);
  for (const l of closedLists) {
    await fetch(`${SUPABASE_URL}/rest/v1/trello_lists?list_id=eq.${l.id}`, {
      method: "DELETE", headers: sbHeaders()
    });
  }

  // アーカイブされたリストのIDセット・リスト名マップ
  const closedListIds = new Set(closedLists.map(l => l.id));
  const listNameMap = new Map(trelloLists.map(l => [l.id, l.name]));

  // ボードの全カード取得（アーカイブされたカード・アーカイブされたリストのカードも含む）
  const allCardsRes = await fetch(
    `https://api.trello.com/1/boards/${boardId}/cards?key=${TRELLO_KEY}&token=${TRELLO_TOKEN}&filter=all&fields=id,name,desc,due,dueComplete,idMembers,labels,closed,idList`
  );
  if (!allCardsRes.ok) return { synced: 0, errors: 1 };
  const allCards: {
    id: string; name: string; desc: string; due: string | null;
    dueComplete: boolean; idMembers: string[];
    labels: { id: string; name: string; color: string }[];
    closed: boolean; idList: string;
  }[] = await allCardsRes.json();

  let synced = 0;
  let errors = 0;
  const now = new Date().toISOString();
  const trelloCardIds: string[] = [];

  for (const card of allCards) {
    trelloCardIds.push(card.id);
    try {
      const assignees = await getMemberNames(card.idMembers ?? []);
      const { total, done } = await getChecklistProgress(card.id);
      const labels = (card.labels ?? []).map(l => ({ id: l.id, name: l.name, color: l.color }));
      const due = card.due ? card.due.slice(0, 10) : null;
      // アーカイブされたカード OR アーカイブされたリストのカードはarchived扱い
      const isArchived = card.closed || closedListIds.has(card.idList);
      const listName = listNameMap.get(card.idList) ?? null;

      await fetch(`${SUPABASE_URL}/rest/v1/tasks?on_conflict=trello_card_id,owner_email`, {
        method: "POST",
        headers: { ...sbHeaders(), "Prefer": "resolution=merge-duplicates" },
        body: JSON.stringify({
          owner_email:     OWNER_EMAIL,
          card_name:       card.name || "(無題)",
          description:     card.desc || null,
          due_date:        due,
          due_complete:    card.dueComplete ?? false,
          assignees:       assignees,
          labels:          labels,
          checklist_total: total,
          checklist_done:  done,
          trello_card_id:  card.id,
          trello_list_id:  card.idList,
          list_name:       listName,
          board_id:        boardId,
          board_name:      boardName,
          archived:        isArchived,
          source:          "trello",
          sync_updated_at: now,
        }),
      });
      synced++;
    } catch {
      errors++;
    }
  }

  // Supabaseに存在するがTrelloに存在しないカード（削除済み）を削除
  // Supabaseのそのボードのtrelloカード一覧を取得
  const sbCardsRes = await fetch(
    `${SUPABASE_URL}/rest/v1/tasks?board_id=eq.${boardId}&owner_email=eq.${encodeURIComponent(OWNER_EMAIL)}&source=eq.trello&select=trello_card_id`,
    { headers: { ...sbHeaders(), "Prefer": "count=none" } }
  );
  const sbCards: { trello_card_id: string | null }[] = await sbCardsRes.json();

  const trelloCardIdSet = new Set(trelloCardIds);
  for (const sbCard of sbCards) {
    if (!sbCard.trello_card_id) continue;
    if (!trelloCardIdSet.has(sbCard.trello_card_id)) {
      // Trelloに存在しない → 削除
      await fetch(
        `${SUPABASE_URL}/rest/v1/tasks?trello_card_id=eq.${sbCard.trello_card_id}&owner_email=eq.${encodeURIComponent(OWNER_EMAIL)}`,
        { method: "DELETE", headers: sbHeaders() }
      );
    }
  }

  return { synced, errors };
}

// Trello webhookを再登録（既存を削除して新規作成）
async function reRegisterWebhook(boardId: string, callbackURL: string) {
  try {
    // 既存webhookを取得
    const listRes = await fetch(
      `https://api.trello.com/1/tokens/${TRELLO_TOKEN}/webhooks?key=${TRELLO_KEY}&token=${TRELLO_TOKEN}`
    );
    if (listRes.ok) {
      const existing: { id: string; idModel: string }[] = await listRes.json();
      // このボード向けのwebhookを削除
      for (const wh of existing) {
        if (wh.idModel === boardId) {
          await fetch(
            `https://api.trello.com/1/webhooks/${wh.id}?key=${TRELLO_KEY}&token=${TRELLO_TOKEN}`,
            { method: "DELETE" }
          );
        }
      }
    }
    // 新規作成
    await fetch(
      `https://api.trello.com/1/webhooks?key=${TRELLO_KEY}&token=${TRELLO_TOKEN}&callbackURL=${encodeURIComponent(callbackURL)}&idModel=${boardId}`,
      { method: "POST" }
    );
  } catch { /* ignore */ }
}

export async function POST(req: Request) {
  if (!TRELLO_KEY || !TRELLO_TOKEN) {
    return Response.json({ error: "TRELLO_API_KEY / TRELLO_TOKEN 未設定" }, { status: 500 });
  }

  const callbackURL = `${process.env.NEXT_PUBLIC_BASE_URL ?? "https://my-calendar-app-983922127476.asia-northeast1.run.app"}/api/trello/webhook`;

  try {
    const { searchParams } = new URL(req.url);
    const boardId = searchParams.get("boardId");

    if (boardId) {
      await reRegisterWebhook(boardId, callbackURL);
      const result = await syncBoard(boardId);
      return Response.json({ boards: 1, ...result });
    }

    // 全ボード同期
    const boardsRes = await fetch(
      `${SUPABASE_URL}/rest/v1/trello_boards?owner_email=eq.${encodeURIComponent(OWNER_EMAIL)}&select=board_id`,
      { headers: sbHeaders() }
    );
    const boards: { board_id: string }[] = await boardsRes.json();

    let totalSynced = 0, totalErrors = 0;
    for (const b of boards) {
      await reRegisterWebhook(b.board_id, callbackURL);
      const r = await syncBoard(b.board_id);
      totalSynced += r.synced;
      totalErrors += r.errors;
    }

    return Response.json({ boards: boards.length, synced: totalSynced, errors: totalErrors });
  } catch (e) {
    console.error("[trello sync] exception:", String(e));
    return Response.json({ error: String(e) }, { status: 500 });
  }
}
