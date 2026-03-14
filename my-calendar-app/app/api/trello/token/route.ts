import { getServerSession } from "next-auth";
import { authOptions } from "../../_lib/auth-options";
import { getTrelloToken, saveTrelloToken, deleteTrelloToken } from "../../_lib/trello-token";

// GET /api/trello/token → { connected: boolean }
export async function GET() {
  const session = await getServerSession(authOptions);
  const email = session?.user?.email;
  if (!email) return Response.json({ connected: false }, { status: 401 });
  const token = await getTrelloToken(email);
  return Response.json({ connected: token !== null });
}

// POST /api/trello/token  { token }  → トークンを保存
export async function POST(req: Request) {
  const session = await getServerSession(authOptions);
  const email = session?.user?.email;
  if (!email) return Response.json({ error: "Unauthorized" }, { status: 401 });
  const { token } = await req.json();
  if (!token) return Response.json({ error: "token required" }, { status: 400 });
  await saveTrelloToken(email, token);
  return Response.json({ ok: true });
}

// DELETE /api/trello/token → 接続解除
export async function DELETE() {
  const session = await getServerSession(authOptions);
  const email = session?.user?.email;
  if (!email) return Response.json({ error: "Unauthorized" }, { status: 401 });
  await deleteTrelloToken(email);
  return Response.json({ ok: true });
}
