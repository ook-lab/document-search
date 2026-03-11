const TRELLO_KEY   = process.env.TRELLO_API_KEY ?? "";
const TRELLO_TOKEN = process.env.TRELLO_TOKEN ?? "";

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const boardId = searchParams.get("boardId");
  if (!boardId || !TRELLO_KEY || !TRELLO_TOKEN) return Response.json([]);
  try {
    const res = await fetch(
      `https://api.trello.com/1/boards/${boardId}/members?key=${TRELLO_KEY}&token=${TRELLO_TOKEN}&fields=id,fullName,username`
    );
    if (!res.ok) return Response.json([]);
    return Response.json(await res.json());
  } catch {
    return Response.json([]);
  }
}
