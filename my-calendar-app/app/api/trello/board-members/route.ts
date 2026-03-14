import { getServerSession } from "next-auth";
import { authOptions } from "../../_lib/auth-options";
import { getTrelloToken } from "../../_lib/trello-token";

const TRELLO_KEY = process.env.TRELLO_API_KEY ?? "";

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const boardId = searchParams.get("boardId");
  if (!boardId || !TRELLO_KEY) return Response.json([]);

  try {
    const session = await getServerSession(authOptions);
    const email = session?.user?.email;
    if (!email) return Response.json([]);

    const trelloToken = await getTrelloToken(email);
    if (!trelloToken) return Response.json([]);

    const res = await fetch(
      `https://api.trello.com/1/boards/${boardId}/members?key=${TRELLO_KEY}&token=${trelloToken}&fields=id,fullName,username`
    );
    if (!res.ok) return Response.json([]);
    return Response.json(await res.json());
  } catch {
    return Response.json([]);
  }
}
