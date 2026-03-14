import { getTrelloToken } from "../../_lib/trello-token";

const SUPABASE_URL = process.env.SUPABASE_URL!;
const SUPABASE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY!;
const TRELLO_KEY   = process.env.TRELLO_API_KEY ?? "";

function sbHeaders() {
  return {
    "apikey": SUPABASE_KEY,
    "Authorization": `Bearer ${SUPABASE_KEY}`,
    "Content-Type": "application/json",
    "Prefer": "return=representation",
  };
}

// ボードIDからオーナーのemailとTrelloトークンを取得
async function getBoardOwner(boardId: string): Promise<{ email: string; token: string } | null> {
  try {
    const res = await fetch(
      `${SUPABASE_URL}/rest/v1/trello_boards?board_id=eq.${boardId}&select=owner_email`,
      { headers: sbHeaders() }
    );
    const rows = await res.json();
    const email = rows[0]?.owner_email;
    if (!email) return null;
    const token = await getTrelloToken(email);
    if (!token) return null;
    return { email, token };
  } catch {
    return null;
  }
}

async function getListInfo(listId: string | null | undefined, boardId: string | null | undefined, trelloToken: string): Promise<{
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

    if (!listName && TRELLO_KEY && trelloToken) {
      try {
        const lr = await fetch(
          `https://api.trello.com/1/lists/${listId}?key=${TRELLO_KEY}&token=${trelloToken}&fields=name,idBoard,pos`
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

async function getMemberNames(memberIds: string[], trelloToken: string): Promise<string[]> {
  if (!TRELLO_KEY || !trelloToken || memberIds.length === 0) return [];
  const names: string[] = [];
  for (const id of memberIds) {
    try {
      const res = await fetch(
        `https://api.trello.com/1/members/${id}?key=${TRELLO_KEY}&token=${trelloToken}&fields=fullName`
      );
      if (res.ok) {
        const j = await res.json();
        if (j.fullName) names.push(j.fullName);
      }
    } catch { /* ignore */ }
  }
  return names;
}

async function getCardAssignees(cardId: string, trelloToken: string): Promise<string[]> {
  if (!TRELLO_KEY || !trelloToken) return [];
  try {
    const res = await fetch(
      `https://api.trello.com/1/cards/${cardId}/members?key=${TRELLO_KEY}&token=${trelloToken}&fields=fullName`
    );
    if (!res.ok) return [];
    const members: { fullName: string }[] = await res.json();
    return members.map(m => m.fullName).filter(Boolean);
  } catch {
    return [];
  }
}

async function getChecklistProgress(cardId: string, trelloToken: string): Promise<{ total: number; done: number }> {
  if (!TRELLO_KEY || !trelloToken) return { total: 0, done: 0 };
  try {
    const res = await fetch(
      `https://api.trello.com/1/cards/${cardId}/checklists?key=${TRELLO_KEY}&token=${trelloToken}`
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

async function getFullChecklists(cardId: string, trelloToken: string): Promise<{id: string; name: string; checkItems: {id: string; name: string; state: string}[]}[]> {
  if (!TRELLO_KEY || !trelloToken) return [];
  try {
    const res = await fetch(
      `https://api.trello.com/1/cards/${cardId}/checklists?key=${TRELLO_KEY}&token=${trelloToken}&checkItems=all&checkItem_fields=id,name,state`
    );
    if (!res.ok) return [];
    const raw = await res.json();
    return raw.map((cl: {id: string; name: string; checkItems: {id: string; name: string; state: string}[]}) => ({
      id: cl.id, name: cl.name,
      checkItems: (cl.checkItems ?? []).map((item) => ({ id: item.id, name: item.name, state: item.state })),
    }));
  } catch {
    return [];
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
    // Trello上での実際の操作時刻（ガード用）
    const actionDate: string = action.date ?? now;

    // ボードIDを特定してオーナーのトークンを取得
    const boardId = data?.board?.id ?? data?.card?.idBoard ?? null;
    const owner = boardId ? await getBoardOwner(boardId) : null;
    const ownerEmail  = owner?.email  ?? null;
    const trelloToken = owner?.token  ?? "";

    // ── ボード系イベント ──────────────────────────────────
    if (type === "updateBoard") {
      const board = data?.board;
      if (board?.id && board?.closed === true && ownerEmail) {
        await fetch(
          `${SUPABASE_URL}/rest/v1/tasks?board_id=eq.${board.id}&owner_email=eq.${encodeURIComponent(ownerEmail)}&sync_updated_at=lt.${encodeURIComponent(actionDate)}`,
          { method: "PATCH", headers: sbHeaders(), body: JSON.stringify({ archived: true, sync_updated_at: now }) }
        );
      }
      return Response.json({ ok: true });
    }

    // ── リスト系イベント ──────────────────────────────────
    if (type === "updateList") {
      const list = data?.list;
      if (list?.id && ownerEmail) {
        if (list.closed === true) {
          await fetch(`${SUPABASE_URL}/rest/v1/trello_lists?list_id=eq.${list.id}`, { method: "DELETE", headers: sbHeaders() });
          await fetch(
            `${SUPABASE_URL}/rest/v1/tasks?trello_list_id=eq.${list.id}&owner_email=eq.${encodeURIComponent(ownerEmail)}&sync_updated_at=lt.${encodeURIComponent(actionDate)}`,
            { method: "PATCH", headers: sbHeaders(), body: JSON.stringify({ archived: true, sync_updated_at: now }) }
          );
        } else {
          await fetch(`${SUPABASE_URL}/rest/v1/trello_lists?list_id=eq.${list.id}`,
            { method: "PATCH", headers: sbHeaders(), body: JSON.stringify({ list_name: list.name }) }
          );
          await fetch(
            `${SUPABASE_URL}/rest/v1/tasks?trello_list_id=eq.${list.id}&owner_email=eq.${encodeURIComponent(ownerEmail)}&sync_updated_at=lt.${encodeURIComponent(actionDate)}`,
            { method: "PATCH", headers: sbHeaders(), body: JSON.stringify({ list_name: list.name, sync_updated_at: now }) }
          );
        }
      }
      return Response.json({ ok: true });
    }

    if (type === "createList") {
      const list = data?.list;
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
    if (!card || !ownerEmail) return Response.json({ ok: true });

    const trelloCardId = card.id as string;

    if (CHECKLIST_EVENT_TYPES.has(type)) {
      const checklists = await getFullChecklists(trelloCardId, trelloToken);
      let total = 0, done = 0;
      for (const cl of checklists) {
        for (const item of cl.checkItems ?? []) { total++; if (item.state === "complete") done++; }
      }
      await fetch(
        `${SUPABASE_URL}/rest/v1/tasks?trello_card_id=eq.${trelloCardId}&owner_email=eq.${encodeURIComponent(ownerEmail)}&sync_updated_at=lt.${encodeURIComponent(actionDate)}`,
        { method: "PATCH", headers: sbHeaders(), body: JSON.stringify({ checklist_total: total, checklist_done: done, checklists, sync_updated_at: now }) }
      );
      return Response.json({ ok: true });
    }

    if (type === "addMemberToCard" || type === "removeMemberFromCard") {
      const assignees = await getCardAssignees(trelloCardId, trelloToken);
      await fetch(
        `${SUPABASE_URL}/rest/v1/tasks?trello_card_id=eq.${trelloCardId}&owner_email=eq.${encodeURIComponent(ownerEmail)}&sync_updated_at=lt.${encodeURIComponent(actionDate)}`,
        { method: "PATCH", headers: sbHeaders(), body: JSON.stringify({ assignees, sync_updated_at: now }) }
      );
      return Response.json({ ok: true });
    }

    const rawListId = data.listAfter?.id ?? data.list?.id ?? null;
    const { listName, boardId: resolvedBoardId, boardName } = await getListInfo(rawListId, boardId, trelloToken);

    const cardName    = card.name as string | undefined;
    const due         = card.due ? (card.due as string).slice(0, 10) : null;
    const dueComplete = card.dueComplete === true;
    const description = card.desc as string | undefined;
    const isClosed    = card.closed === true;
    const memberIds: string[] = card.idMembers ?? [];
    const assignees   = await getMemberNames(memberIds, trelloToken);
    const labels      = (card.labels ?? []).map((l: { id: string; name: string; color: string }) => ({ id: l.id, name: l.name, color: l.color }));

    if (type === "createCard" || type === "copyCard") {
      const chk = await fetch(
        `${SUPABASE_URL}/rest/v1/tasks?trello_card_id=eq.${trelloCardId}&owner_email=eq.${encodeURIComponent(ownerEmail)}&select=id`,
        { headers: sbHeaders() }
      );
      if ((await chk.json()).length > 0) return Response.json({ ok: true });

      const { total, done } = await getChecklistProgress(trelloCardId, trelloToken);
      await fetch(`${SUPABASE_URL}/rest/v1/tasks`, {
        method: "POST", headers: sbHeaders(),
        body: JSON.stringify({
          owner_email: ownerEmail, card_name: cardName ?? "(無題)", description: description || null,
          due_date: due, due_complete: dueComplete, assignees, labels, checklist_total: total, checklist_done: done,
          trello_card_id: trelloCardId, trello_list_id: rawListId, list_name: listName,
          board_id: resolvedBoardId, board_name: boardName, archived: isClosed, source: "trello", sync_updated_at: now,
        }),
      });

    } else if (type === "updateCard" || type === "moveCardToBoard" || type === "moveCardFromBoard") {
      const existing = await fetch(
        `${SUPABASE_URL}/rest/v1/tasks?trello_card_id=eq.${trelloCardId}&owner_email=eq.${encodeURIComponent(ownerEmail)}&select=id`,
        { headers: sbHeaders() }
      );
      const prev = (await existing.json())[0] ?? null;

      const patch: Record<string, unknown> = {
        sync_updated_at: now, archived: isClosed, due_complete: dueComplete,
        labels, assignees, due_date: due,
      };
      if (rawListId)                 patch.trello_list_id = rawListId;
      if (listName)                  patch.list_name      = listName;
      if (resolvedBoardId)           patch.board_id       = resolvedBoardId;
      if (boardName)                 patch.board_name     = boardName;
      if (cardName !== undefined)    patch.card_name      = cardName;
      if (description !== undefined) patch.description    = description || null;

      if (prev) {
        await fetch(
          `${SUPABASE_URL}/rest/v1/tasks?trello_card_id=eq.${trelloCardId}&owner_email=eq.${encodeURIComponent(ownerEmail)}&sync_updated_at=lt.${encodeURIComponent(actionDate)}`,
          { method: "PATCH", headers: sbHeaders(), body: JSON.stringify(patch) }
        );
      } else {
        const { total, done } = await getChecklistProgress(trelloCardId, trelloToken);
        await fetch(`${SUPABASE_URL}/rest/v1/tasks`, {
          method: "POST", headers: sbHeaders(),
          body: JSON.stringify({
            owner_email: ownerEmail, card_name: cardName ?? "(無題)", description: description || null,
            due_date: due, due_complete: dueComplete, assignees, labels, checklist_total: total, checklist_done: done,
            trello_card_id: trelloCardId, trello_list_id: rawListId, list_name: listName,
            board_id: resolvedBoardId, board_name: boardName, archived: isClosed, source: "trello", sync_updated_at: now,
          }),
        });
      }

    } else if (type === "deleteCard") {
      await fetch(
        `${SUPABASE_URL}/rest/v1/tasks?trello_card_id=eq.${trelloCardId}&owner_email=eq.${encodeURIComponent(ownerEmail)}`,
        { method: "DELETE", headers: sbHeaders() }
      );
    }

    return Response.json({ ok: true });
  } catch (e) {
    console.error("[trello webhook] exception:", String(e));
    return Response.json({ error: String(e) }, { status: 500 });
  }
}
