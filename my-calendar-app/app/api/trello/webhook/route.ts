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

// リストIDからリスト名・ボード情報をDBで引く。未登録なら自動登録する
async function getListInfo(listId: string | null | undefined, boardId?: string | null): Promise<{
  listName: string | null;
  boardId: string | null;
  boardName: string | null;
}> {
  if (!listId) return { listName: null, boardId: null, boardName: null };
  try {
    const listRes = await fetch(
      `${SUPABASE_URL}/rest/v1/trello_lists?list_id=eq.${listId}&select=list_name,board_id`,
      { headers: sbHeaders() }
    );
    const listRows = await listRes.json();
    let listName: string | null = listRows[0]?.list_name ?? null;
    let resolvedBoardId: string | null = listRows[0]?.board_id ?? boardId ?? null;

    if (!listName && TRELLO_KEY && TRELLO_TOKEN) {
      try {
        const lr = await fetch(
          `https://api.trello.com/1/lists/${listId}?key=${TRELLO_KEY}&token=${TRELLO_TOKEN}&fields=name,idBoard,pos`
        );
        if (lr.ok) {
          const lj = await lr.json();
          listName = lj.name ?? null;
          if (!resolvedBoardId) resolvedBoardId = lj.idBoard ?? null;
          await fetch(`${SUPABASE_URL}/rest/v1/trello_lists?on_conflict=list_id`, {
            method: "POST",
            headers: { ...sbHeaders(), "Prefer": "resolution=merge-duplicates" },
            body: JSON.stringify({ board_id: resolvedBoardId ?? "", list_id: listId, list_name: listName, list_pos: lj.pos ?? 0 }),
          });
        }
      } catch { /* ignore */ }
    }

    let boardName: string | null = null;
    if (resolvedBoardId) {
      const boardRes = await fetch(
        `${SUPABASE_URL}/rest/v1/trello_boards?board_id=eq.${resolvedBoardId}&select=board_name`,
        { headers: sbHeaders() }
      );
      const boardRows = await boardRes.json();
      boardName = boardRows[0]?.board_name ?? null;
    }
    return { listName, boardId: resolvedBoardId, boardName };
  } catch {
    return { listName: null, boardId: null, boardName: null };
  }
}

async function getMemberNames(memberIds: string[]): Promise<string[]> {
  if (!TRELLO_KEY || !TRELLO_TOKEN || memberIds.length === 0) return [];
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

// カードの現在のメンバー一覧をTrello APIから取得（removeMemberFromCard等で使用）
async function getCardAssignees(cardId: string): Promise<string[]> {
  if (!TRELLO_KEY || !TRELLO_TOKEN) return [];
  try {
    const res = await fetch(
      `https://api.trello.com/1/cards/${cardId}/members?key=${TRELLO_KEY}&token=${TRELLO_TOKEN}&fields=fullName`
    );
    if (!res.ok) return [];
    const members: { fullName: string }[] = await res.json();
    return members.map(m => m.fullName).filter(Boolean);
  } catch {
    return [];
  }
}

async function getChecklistProgress(cardId: string): Promise<{ total: number; done: number }> {
  if (!TRELLO_KEY || !TRELLO_TOKEN) return { total: 0, done: 0 };
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

export async function HEAD() {
  return new Response(null, { status: 200 });
}

// チェックリスト構成が変わるイベント（進捗を再計算する）
const CHECKLIST_EVENT_TYPES = new Set([
  "addChecklistToCard",
  "removeChecklistFromCard",
  "createCheckItem",
  "deleteCheckItem",
  "updateCheckItem",
  "updateCheckItemStateOnCard",
]);

export async function POST(req: Request) {
  try {
    const payload = await req.json();
    const action = payload.action;
    if (!action) return Response.json({ ok: true });

    const { type, data } = action;
    const now = new Date().toISOString();

    // ── ボード系イベント ──────────────────────────────────
    if (type === "updateBoard") {
      const board = data?.board;
      if (board?.id && board?.closed === true) {
        // ボードがアーカイブ→そのボードの全タスクをarchived化
        await fetch(
          `${SUPABASE_URL}/rest/v1/tasks?board_id=eq.${board.id}&owner_email=eq.${encodeURIComponent(OWNER_EMAIL)}`,
          { method: "PATCH", headers: sbHeaders(), body: JSON.stringify({ archived: true, sync_updated_at: now }) }
        );
      }
      return Response.json({ ok: true });
    }

    // ── リスト系イベント ──────────────────────────────────
    if (type === "updateList") {
      const list = data?.list;
      if (list?.id) {
        if (list.closed === true) {
          // リストがアーカイブ→trello_listsから削除、そのリストのタスクをarchived化
          await fetch(
            `${SUPABASE_URL}/rest/v1/trello_lists?list_id=eq.${list.id}`,
            { method: "DELETE", headers: sbHeaders() }
          );
          await fetch(
            `${SUPABASE_URL}/rest/v1/tasks?trello_list_id=eq.${list.id}&owner_email=eq.${encodeURIComponent(OWNER_EMAIL)}`,
            { method: "PATCH", headers: sbHeaders(), body: JSON.stringify({ archived: true, sync_updated_at: now }) }
          );
        } else {
          // リスト名変更・復元→trello_listsを更新、そのリストのタスクのlist_nameも更新
          await fetch(
            `${SUPABASE_URL}/rest/v1/trello_lists?list_id=eq.${list.id}`,
            { method: "PATCH", headers: sbHeaders(), body: JSON.stringify({ list_name: list.name }) }
          );
          await fetch(
            `${SUPABASE_URL}/rest/v1/tasks?trello_list_id=eq.${list.id}&owner_email=eq.${encodeURIComponent(OWNER_EMAIL)}`,
            { method: "PATCH", headers: sbHeaders(), body: JSON.stringify({ list_name: list.name, sync_updated_at: now }) }
          );
        }
      }
      return Response.json({ ok: true });
    }

    if (type === "createList") {
      const list = data?.list;
      const boardId = data?.board?.id ?? null;
      if (list?.id && boardId) {
        await fetch(`${SUPABASE_URL}/rest/v1/trello_lists?on_conflict=list_id`, {
          method: "POST",
          headers: { ...sbHeaders(), "Prefer": "resolution=merge-duplicates" },
          body: JSON.stringify({ board_id: boardId, list_id: list.id, list_name: list.name, list_pos: list.pos ?? 0 }),
        });
      }
      return Response.json({ ok: true });
    }

    // ── カード系イベント ──────────────────────────────────
    const card = data?.card;
    if (!card) return Response.json({ ok: true });

    const trelloCardId = card.id as string;

    // チェックリスト系イベント（進捗を再計算してDBを更新）
    if (CHECKLIST_EVENT_TYPES.has(type)) {
      const { total, done } = await getChecklistProgress(trelloCardId);
      await fetch(
        `${SUPABASE_URL}/rest/v1/tasks?trello_card_id=eq.${trelloCardId}&owner_email=eq.${encodeURIComponent(OWNER_EMAIL)}`,
        {
          method: "PATCH", headers: sbHeaders(),
          body: JSON.stringify({ checklist_total: total, checklist_done: done, sync_updated_at: now }),
        }
      );
      return Response.json({ ok: true });
    }

    // メンバー変更（Trello APIから最新メンバー一覧を取得して反映）
    if (type === "addMemberToCard" || type === "removeMemberFromCard") {
      const assignees = await getCardAssignees(trelloCardId);
      await fetch(
        `${SUPABASE_URL}/rest/v1/tasks?trello_card_id=eq.${trelloCardId}&owner_email=eq.${encodeURIComponent(OWNER_EMAIL)}`,
        {
          method: "PATCH", headers: sbHeaders(),
          body: JSON.stringify({ assignees, sync_updated_at: now }),
        }
      );
      return Response.json({ ok: true });
    }

    const rawListId = data.listAfter?.id ?? data.list?.id ?? null;
    const boardId   = data.board?.id ?? null;

    const { listName, boardId: resolvedBoardId, boardName } = await getListInfo(rawListId, boardId);

    const cardName    = card.name as string | undefined;
    const due         = card.due ? (card.due as string).slice(0, 10) : null;
    const dueComplete = card.dueComplete === true;
    const description = card.desc as string | undefined;
    const isClosed    = card.closed === true;

    const memberIds: string[] = card.idMembers ?? [];
    // 空配列も正しく反映（全員削除のケース）
    const assignees = await getMemberNames(memberIds);

    // ラベル
    const labels = (card.labels ?? []).map((l: { id: string; name: string; color: string }) => ({
      id: l.id, name: l.name, color: l.color,
    }));

    if (type === "createCard" || type === "copyCard") {
      // 重複チェック
      const chk = await fetch(
        `${SUPABASE_URL}/rest/v1/tasks?trello_card_id=eq.${trelloCardId}&owner_email=eq.${encodeURIComponent(OWNER_EMAIL)}&select=id`,
        { headers: sbHeaders() }
      );
      const rows = await chk.json();
      if (rows.length > 0) return Response.json({ ok: true });

      const { total, done } = await getChecklistProgress(trelloCardId);

      await fetch(`${SUPABASE_URL}/rest/v1/tasks`, {
        method: "POST",
        headers: sbHeaders(),
        body: JSON.stringify({
          owner_email:       OWNER_EMAIL,
          card_name:         cardName ?? "(無題)",
          description:       description || null,
          due_date:          due,
          due_complete:      dueComplete,
          assignees:         assignees,
          labels:            labels,
          checklist_total:   total,
          checklist_done:    done,
          trello_card_id:    trelloCardId,
          trello_list_id:    rawListId,
          list_name:         listName,
          board_id:          resolvedBoardId,
          board_name:        boardName,
          archived:          isClosed,
          source:            "trello",
          sync_updated_at:   now,
        }),
      });

    } else if (type === "updateCard" || type === "moveCardToBoard" || type === "moveCardFromBoard") {
      const existing = await fetch(
        `${SUPABASE_URL}/rest/v1/tasks?trello_card_id=eq.${trelloCardId}&owner_email=eq.${encodeURIComponent(OWNER_EMAIL)}&select=id`,
        { headers: sbHeaders() }
      );
      const existingRows = await existing.json();
      const prev = existingRows[0] ?? null;

      const patch: Record<string, unknown> = {
        sync_updated_at: now,
        archived:        isClosed,
        due_complete:    dueComplete,
        labels:          labels,
        assignees:       assignees,  // 空配列でも必ず更新（全員削除を反映）
        due_date:        due,
      };
      if (rawListId)                 patch.trello_list_id = rawListId;
      if (listName)                  patch.list_name      = listName;
      if (resolvedBoardId)           patch.board_id       = resolvedBoardId;
      if (boardName)                 patch.board_name     = boardName;
      if (cardName !== undefined)    patch.card_name      = cardName;
      if (description !== undefined) patch.description    = description || null;

      if (prev) {
        await fetch(
          `${SUPABASE_URL}/rest/v1/tasks?trello_card_id=eq.${trelloCardId}&owner_email=eq.${encodeURIComponent(OWNER_EMAIL)}`,
          { method: "PATCH", headers: sbHeaders(), body: JSON.stringify(patch) }
        );
      } else {
        // DBに存在しない場合は新規作成（webhook missed時の救済）
        const { total, done } = await getChecklistProgress(trelloCardId);
        await fetch(`${SUPABASE_URL}/rest/v1/tasks`, {
          method: "POST",
          headers: sbHeaders(),
          body: JSON.stringify({
            owner_email: OWNER_EMAIL, card_name: cardName ?? "(無題)",
            description: description || null, due_date: due, due_complete: dueComplete,
            assignees, labels, checklist_total: total, checklist_done: done,
            trello_card_id: trelloCardId, trello_list_id: rawListId,
            list_name: listName, board_id: resolvedBoardId, board_name: boardName,
            archived: isClosed, source: "trello", sync_updated_at: now,
          }),
        });
      }

    } else if (type === "deleteCard") {
      await fetch(
        `${SUPABASE_URL}/rest/v1/tasks?trello_card_id=eq.${trelloCardId}&owner_email=eq.${encodeURIComponent(OWNER_EMAIL)}`,
        { method: "DELETE", headers: sbHeaders() }
      );
    }

    return Response.json({ ok: true });
  } catch (e) {
    console.error("[trello webhook] exception:", String(e));
    return Response.json({ error: String(e) }, { status: 500 });
  }
}
