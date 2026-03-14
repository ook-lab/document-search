/**
 * GET /api/trello/lists?boardId=xxx  → リスト一覧
 * PUT /api/trello/lists              → リストのstatus変更
 *   body: { listId, status }
 */

const SUPABASE_URL = process.env.SUPABASE_URL!;
const SUPABASE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY!;

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
      ? `${SUPABASE_URL}/rest/v1/trello_lists?board_id=eq.${boardId}&order=created_at`
      : `${SUPABASE_URL}/rest/v1/trello_lists?order=created_at`;
    const res = await fetch(url, { headers: sbHeaders() });
    return Response.json(await res.json());
  } catch (e) {
    return Response.json([], { status: 200 });
  }
}

export async function PUT(req: Request) {
  try {
    const { listId, status } = await req.json();
    if (!listId || !status) return Response.json({ error: "listId, status required" }, { status: 400 });

    const res = await fetch(
      `${SUPABASE_URL}/rest/v1/trello_lists?list_id=eq.${listId}`,
      {
        method: "PATCH",
        headers: sbHeaders(),
        body: JSON.stringify({ status }),
      }
    );
    return Response.json(await res.json());
  } catch (e) {
    console.error("[trello lists PUT] exception:", String(e));
    return Response.json({ error: String(e) }, { status: 500 });
  }
}
