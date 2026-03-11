/**
 * GET /api/trello/lists?boardId=xxx  → リスト一覧
 * POST /api/trello/lists             → リスト作成（Trello + Supabase）
 * PUT /api/trello/lists              → リスト名変更（Trello + Supabase）
 *   body: { listId, name }
 */

const SUPABASE_URL = process.env.SUPABASE_URL!;
const SUPABASE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY!;
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

export async function GET(req: Request) {
  try {
    const { searchParams } = new URL(req.url);
    const boardId = searchParams.get("boardId");
    const url = boardId
      ? `${SUPABASE_URL}/rest/v1/trello_lists?board_id=eq.${boardId}&order=list_pos`
      : `${SUPABASE_URL}/rest/v1/trello_lists?order=list_pos`;
    const res = await fetch(url, { headers: sbHeaders() });
    return Response.json(await res.json());
  } catch {
    return Response.json([]);
  }
}

// POST: リスト作成
export async function POST(req: Request) {
  try {
    const { boardId, name } = await req.json();
    if (!boardId || !name) return Response.json({ error: "boardId, name required" }, { status: 400 });
    if (!TRELLO_KEY || !TRELLO_TOKEN) return Response.json({ error: "Trello credentials missing" }, { status: 500 });

    // Trelloにリスト作成
    const trelloRes = await fetch(
      `https://api.trello.com/1/lists?key=${TRELLO_KEY}&token=${TRELLO_TOKEN}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ idBoard: boardId, name, pos: "bottom" }),
      }
    );
    if (!trelloRes.ok) return Response.json({ error: "Trello API failed" }, { status: 500 });
    const trelloList = await trelloRes.json();

    // Supabaseに保存
    await fetch(`${SUPABASE_URL}/rest/v1/trello_lists?on_conflict=list_id`, {
      method: "POST",
      headers: { ...sbHeaders(), "Prefer": "resolution=merge-duplicates" },
      body: JSON.stringify({ board_id: boardId, list_id: trelloList.id, list_name: name, list_pos: trelloList.pos ?? 0 }),
    });

    return Response.json({ id: trelloList.id, boardId, listId: trelloList.id, listName: name });
  } catch (e) {
    return Response.json({ error: String(e) }, { status: 500 });
  }
}

// PUT: リスト名変更
export async function PUT(req: Request) {
  try {
    const { listId, name } = await req.json();
    if (!listId || !name) return Response.json({ error: "listId, name required" }, { status: 400 });

    // Trelloでリスト名変更
    if (TRELLO_KEY && TRELLO_TOKEN) {
      await fetch(
        `https://api.trello.com/1/lists/${listId}?key=${TRELLO_KEY}&token=${TRELLO_TOKEN}`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name }),
        }
      );
    }

    // Supabase更新
    const res = await fetch(
      `${SUPABASE_URL}/rest/v1/trello_lists?list_id=eq.${listId}`,
      { method: "PATCH", headers: sbHeaders(), body: JSON.stringify({ list_name: name }) }
    );
    return Response.json(await res.json());
  } catch (e) {
    return Response.json({ error: String(e) }, { status: 500 });
  }
}
