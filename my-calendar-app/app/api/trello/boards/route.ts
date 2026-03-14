/**
 * GET  /api/trello/boards          → ボード一覧（ログインユーザーのボード）
 * POST /api/trello/boards          → ボード登録（ユーザー自身のTrelloトークンで検証）
 * DELETE /api/trello/boards?boardId=xxx → ボード削除
 */

import { getServerSession } from "next-auth";
import { authOptions } from "../../_lib/auth-options";
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

async function getEmail(): Promise<string | null> {
  const session = await getServerSession(authOptions);
  return session?.user?.email ?? null;
}

export async function GET() {
  try {
    const email = await getEmail();
    if (!email) return Response.json({ boards: [], lists: [] }, { status: 401 });

    const res = await fetch(
      `${SUPABASE_URL}/rest/v1/trello_boards?owner_email=eq.${encodeURIComponent(email)}&order=created_at`,
      { headers: sbHeaders() }
    );
    const boards = await res.json();

    const boardIds: string[] = boards.map((b: { board_id: string }) => b.board_id);
    if (boardIds.length === 0) return Response.json({ boards: [], lists: [] });

    const listsRes = await fetch(
      `${SUPABASE_URL}/rest/v1/trello_lists?board_id=in.(${boardIds.map(encodeURIComponent).join(",")})&order=list_pos`,
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
    const email = await getEmail();
    if (!email) return Response.json({ error: "Not authenticated" }, { status: 401 });

    // ユーザー自身のTrelloトークンを使用
    const trelloToken = await getTrelloToken(email);
    if (!trelloToken) {
      return Response.json({ error: "Trelloに接続していません", needsAuth: true }, { status: 403 });
    }

    const { boardId } = await req.json();
    if (!boardId) return Response.json({ error: "boardId required" }, { status: 400 });

    if (!TRELLO_KEY) {
      return Response.json({ error: "TRELLO_API_KEY 未設定" }, { status: 500 });
    }

    // ユーザー自身のトークンでボード情報を取得（アクセス権がなければ失敗）
    const boardRes = await fetch(
      `https://api.trello.com/1/boards/${boardId}?key=${TRELLO_KEY}&token=${trelloToken}&fields=id,name`
    );
    if (!boardRes.ok) {
      return Response.json({ error: "Trelloボードが見つかりません（アクセス権がないか、IDが間違っています）" }, { status: 404 });
    }
    const boardInfo = await boardRes.json();

    // リスト一覧取得
    const listsRes = await fetch(
      `https://api.trello.com/1/boards/${boardId}/lists?key=${TRELLO_KEY}&token=${trelloToken}&fields=id,name&filter=open`
    );
    const trelloLists = await listsRes.json();

    // Webhook 登録
    const callbackURL = `${process.env.NEXT_PUBLIC_BASE_URL ?? "https://my-calendar-app-983922127476.asia-northeast1.run.app"}/api/trello/webhook`;
    await fetch(
      `https://api.trello.com/1/webhooks?key=${TRELLO_KEY}&token=${trelloToken}&callbackURL=${encodeURIComponent(callbackURL)}&idModel=${boardInfo.id}`,
      { method: "POST" }
    );

    // Supabaseにボードを保存
    const boardSave = await fetch(`${SUPABASE_URL}/rest/v1/trello_boards?on_conflict=board_id,owner_email`, {
      method: "POST",
      headers: { ...sbHeaders(), "Prefer": "return=representation,resolution=merge-duplicates" },
      body: JSON.stringify({ owner_email: email, board_id: boardInfo.id, board_name: boardInfo.name }),
    });
    const savedBoard = await boardSave.json();

    // Supabaseにリストを保存
    if (trelloLists.length > 0) {
      await fetch(`${SUPABASE_URL}/rest/v1/trello_lists?on_conflict=list_id`, {
        method: "POST",
        headers: { ...sbHeaders(), "Prefer": "return=representation,resolution=merge-duplicates" },
        body: JSON.stringify(
          trelloLists.map((l: { id: string; name: string; pos: number }) => ({
            board_id: boardInfo.id, list_id: l.id, list_name: l.name, list_pos: l.pos ?? 0,
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
    const email = await getEmail();
    if (!email) return Response.json({ error: "Not authenticated" }, { status: 401 });

    const { searchParams } = new URL(req.url);
    const boardId = searchParams.get("boardId");
    if (!boardId) return Response.json({ error: "boardId required" }, { status: 400 });

    await fetch(
      `${SUPABASE_URL}/rest/v1/trello_boards?board_id=eq.${boardId}&owner_email=eq.${encodeURIComponent(email)}`,
      { method: "DELETE", headers: sbHeaders() }
    );
    return Response.json({ ok: true });
  } catch (e) {
    console.error("[trello boards DELETE] exception:", String(e));
    return Response.json({ error: String(e) }, { status: 500 });
  }
}
