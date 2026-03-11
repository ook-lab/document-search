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

// カードの基本フィールドをTrelloに反映
async function pushCardToTrello(trelloCardId: string, fields: {
  name?: string;
  desc?: string;
  due?: string | null;
  dueComplete?: boolean;
  closed?: boolean;
  idList?: string;
}) {
  if (!TRELLO_KEY || !TRELLO_TOKEN || !trelloCardId) return;
  const body: Record<string, unknown> = {};
  if (fields.name        !== undefined) body.name        = fields.name;
  if (fields.desc        !== undefined) body.desc        = fields.desc ?? "";
  if (fields.due         !== undefined) body.due         = fields.due ?? null;
  if (fields.dueComplete !== undefined) body.dueComplete = fields.dueComplete;
  if (fields.closed      !== undefined) body.closed      = fields.closed;
  if (fields.idList      !== undefined) body.idList      = fields.idList;
  if (Object.keys(body).length === 0) return;
  try {
    await fetch(
      `https://api.trello.com/1/cards/${trelloCardId}?key=${TRELLO_KEY}&token=${TRELLO_TOKEN}`,
      { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }
    );
  } catch { /* ignore */ }
}

// ラベルをTrelloと同期（差分のみadd/remove）
async function syncLabelsToTrello(trelloCardId: string, newLabelIds: string[]) {
  if (!TRELLO_KEY || !TRELLO_TOKEN || !trelloCardId) return;
  try {
    const res = await fetch(
      `https://api.trello.com/1/cards/${trelloCardId}?key=${TRELLO_KEY}&token=${TRELLO_TOKEN}&fields=idLabels`
    );
    if (!res.ok) return;
    const card = await res.json();
    const currentIds: string[] = card.idLabels ?? [];
    const newSet     = new Set(newLabelIds);
    const currentSet = new Set(currentIds);
    for (const id of newSet)     if (!currentSet.has(id))
      await fetch(`https://api.trello.com/1/cards/${trelloCardId}/idLabels?key=${TRELLO_KEY}&token=${TRELLO_TOKEN}&value=${id}`, { method: "POST" });
    for (const id of currentSet) if (!newSet.has(id))
      await fetch(`https://api.trello.com/1/cards/${trelloCardId}/idLabels/${id}?key=${TRELLO_KEY}&token=${TRELLO_TOKEN}`, { method: "DELETE" });
  } catch { /* ignore */ }
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

    // Supabase更新
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

    // Trelloに反映（trelloCardIdがある場合のみ）
    const trelloCardId = body.trelloCardId as string | undefined;
    if (trelloCardId) {
      const trelloFields: Parameters<typeof pushCardToTrello>[1] = {};
      if (body.cardName    !== undefined) trelloFields.name        = body.cardName;
      if (body.description !== undefined) trelloFields.desc        = body.description;
      if (body.dueDate     !== undefined) trelloFields.due         = body.dueDate || null;
      if (body.dueComplete !== undefined) trelloFields.dueComplete = body.dueComplete;
      if (body.archived    !== undefined) trelloFields.closed      = body.archived;
      if (body.trelloListId !== undefined) trelloFields.idList     = body.trelloListId;
      if (Object.keys(trelloFields).length > 0) await pushCardToTrello(trelloCardId, trelloFields);
      if (body.labels !== undefined) {
        const labelIds = (body.labels as { id: string }[]).map(l => l.id);
        await syncLabelsToTrello(trelloCardId, labelIds);
      }
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
