const SUPABASE_URL = process.env.SUPABASE_URL!;
const SUPABASE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY!;
const OWNER_EMAIL  = process.env.CALENDAR_OWNER_EMAIL ?? "ookubo.y@workspace-o.com";

function sbHeaders() {
  return {
    "apikey": SUPABASE_KEY,
    "Authorization": `Bearer ${SUPABASE_KEY}`,
    "Content-Type": "application/json",
    "Prefer": "return=representation",
  };
}

export async function PUT(req: Request, { params }: { params: Promise<{ id: string }> }) {
  try {
    const { id } = await params;
    const body = await req.json();
    const patch: Record<string, unknown> = { updated_at: new Date().toISOString() };
    if (body.cardName        !== undefined) patch.card_name         = body.cardName;
    if (body.description     !== undefined) patch.description       = body.description;
    if (body.dueDate         !== undefined) patch.due_date          = body.dueDate;
    if (body.dueComplete     !== undefined) patch.due_complete      = body.dueComplete;
    if (body.assignees       !== undefined) patch.assignees         = body.assignees;
    if (body.labels          !== undefined) patch.labels            = body.labels;
    if (body.checklistTotal  !== undefined) patch.checklist_total   = body.checklistTotal;
    if (body.checklistDone   !== undefined) patch.checklist_done    = body.checklistDone;
    if (body.calendarGroupId !== undefined) patch.calendar_group_id = body.calendarGroupId;
    if (body.trelloCardId    !== undefined) patch.trello_card_id    = body.trelloCardId;
    if (body.trelloListId    !== undefined) patch.trello_list_id    = body.trelloListId;
    if (body.listName        !== undefined) patch.list_name         = body.listName;
    if (body.boardId         !== undefined) patch.board_id          = body.boardId;
    if (body.boardName       !== undefined) patch.board_name        = body.boardName;
    if (body.sortOrder       !== undefined) patch.sort_order        = body.sortOrder;
    if (body.googleEventId   !== undefined) patch.google_event_id   = body.googleEventId;
    if (body.archived        !== undefined) patch.archived          = body.archived;

    const url = `${SUPABASE_URL}/rest/v1/tasks?id=eq.${id}&owner_email=eq.${encodeURIComponent(OWNER_EMAIL)}`;
    const res = await fetch(url, {
      method: "PATCH",
      headers: sbHeaders(),
      body: JSON.stringify(patch),
    });
    const text = await res.text();
    if (!res.ok) {
      console.error("[tasks PUT] Supabase error:", res.status, text);
      return Response.json({ error: text }, { status: 500 });
    }
    return Response.json(JSON.parse(text));
  } catch (e) {
    console.error("[tasks PUT] exception:", String(e));
    return Response.json({ error: String(e) }, { status: 500 });
  }
}

export async function DELETE(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  try {
    const { id } = await params;
    await fetch(
      `${SUPABASE_URL}/rest/v1/tasks?id=eq.${id}&owner_email=eq.${encodeURIComponent(OWNER_EMAIL)}`,
      { method: "DELETE", headers: sbHeaders() }
    );
    return Response.json({ ok: true });
  } catch (e) {
    console.error("[tasks DELETE] exception:", String(e));
    return Response.json({ error: String(e) }, { status: 500 });
  }
}
