/**
 * GET  /api/trello/boards          → ボード一覧
 * POST /api/trello/boards          → ボード登録
 * DELETE /api/trello/boards?boardId=xxx → ボード削除
 *
 * ボード登録時、TrelloAPIでリスト一覧を自動取得してtrello_listsにも登録する
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

export async function GET() {
  try {
    const res = await fetch(
      `${SUPABASE_URL}/rest/v1/trello_boards?owner_email=eq.${encodeURIComponent(OWNER_EMAIL)}&order=created_at`,
      { headers: sbHeaders() }
    );
    const boards = await res.json();

    // 各ボードのリスト一覧も付与
    const listsRes = await fetch(
      `${SUPABASE_URL}/rest/v1/trello_lists?order=list_pos`,
      { headers: sbHeaders() }
    );
    const lists = await listsRes.json();

    return Response.json({ boards, lists });
  } catch (e) {
    console.error("[trello boards GET] exception:", String(e));
    return Response.json({ boards: [], lists: [] });
  }
}

export async function POST(req: Request) {
  try {
    const { boardId } = await req.json();
    if (!boardId) return Response.json({ error: "boardId required" }, { status: 400 });

    if (!TRELLO_KEY || !TRELLO_TOKEN) {
      return Response.json({ error: "TRELLO_API_KEY / TRELLO_TOKEN 未設定" }, { status: 500 });
    }

    // Trello APIでボード情報取得
    const boardRes = await fetch(
      `https://api.trello.com/1/boards/${boardId}?key=${TRELLO_KEY}&token=${TRELLO_TOKEN}&fields=id,name`
    );
    if (!boardRes.ok) {
      return Response.json({ error: "Trelloボードが見つかりません" }, { status: 404 });
    }
    const boardInfo = await boardRes.json();

    // Trello APIでリスト一覧取得
    const listsRes = await fetch(
      `https://api.trello.com/1/boards/${boardId}/lists?key=${TRELLO_KEY}&token=${TRELLO_TOKEN}&fields=id,name&filter=open`
    );
    const trelloLists = await listsRes.json();

    // Trello webhookを自動登録
    const callbackURL = `${process.env.NEXT_PUBLIC_BASE_URL ?? "https://my-calendar-app-983922127476.asia-northeast1.run.app"}/api/trello/webhook`;
    await fetch(
      `https://api.trello.com/1/webhooks?key=${TRELLO_KEY}&token=${TRELLO_TOKEN}&callbackURL=${encodeURIComponent(callbackURL)}&idModel=${boardInfo.id}`,
      { method: "POST" }
    );

    // ボードをSupabaseに保存
    const boardSave = await fetch(`${SUPABASE_URL}/rest/v1/trello_boards`, {
      method: "POST",
      headers: { ...sbHeaders(), "Prefer": "return=representation,resolution=merge-duplicates", "on-conflict": "board_id" },
      body: JSON.stringify({
        owner_email: OWNER_EMAIL,
        board_id:    boardInfo.id,
        board_name:  boardInfo.name,
      }),
    });
    const savedBoard = await boardSave.json();

    // リストをSupabaseに保存
    if (trelloLists.length > 0) {
      await fetch(`${SUPABASE_URL}/rest/v1/trello_lists`, {
        method: "POST",
        headers: { ...sbHeaders(), "Prefer": "return=representation,resolution=merge-duplicates", "on-conflict": "list_id" },
        body: JSON.stringify(
          trelloLists.map((l: { id: string; name: string; pos: number }) => ({
            board_id:  boardInfo.id,
            list_id:   l.id,
            list_name: l.name,
            list_pos:  l.pos ?? 0,
          }))
        ),
      });
    }

    return Response.json({ board: savedBoard, lists: trelloLists });
  } catch (e) {
    console.error("[trello boards POST] exception:", String(e));
    return Response.json({ error: String(e) }, { status: 500 });
  }
}

export async function DELETE(req: Request) {
  try {
    const { searchParams } = new URL(req.url);
    const boardId = searchParams.get("boardId");
    if (!boardId) return Response.json({ error: "boardId required" }, { status: 400 });

    // リストを先に削除
    await fetch(
      `${SUPABASE_URL}/rest/v1/trello_lists?board_id=eq.${boardId}`,
      { method: "DELETE", headers: sbHeaders() }
    );
    // ボードを削除
    await fetch(
      `${SUPABASE_URL}/rest/v1/trello_boards?board_id=eq.${boardId}&owner_email=eq.${encodeURIComponent(OWNER_EMAIL)}`,
      { method: "DELETE", headers: sbHeaders() }
    );
    return Response.json({ ok: true });
  } catch (e) {
    console.error("[trello boards DELETE] exception:", String(e));
    return Response.json({ error: String(e) }, { status: 500 });
  }
}

// リスト名からstatusを推測
function guessStatus(name: string): "todo" | "doing" | "done" {
  if (name.includes("完了") || name.includes("おわり") || name.toLowerCase().includes("done")) return "done";
  if (name.includes("実行") || name.includes("進行") || name.includes("やってる") || name.includes("今日") || name.toLowerCase().includes("doing") || name.toLowerCase().includes("today")) return "doing";
  return "todo";
}
