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

export async function GET(req: Request) {
  try {
    const { searchParams } = new URL(req.url);
    const groupId = searchParams.get("groupId");

    let url = `${SUPABASE_URL}/rest/v1/tasks?owner_email=eq.${encodeURIComponent(OWNER_EMAIL)}&archived=eq.false&order=sort_order,due_date`;
    if (groupId) url += `&calendar_group_id=eq.${encodeURIComponent(groupId)}`;

    const res = await fetch(url, { headers: sbHeaders() });
    const text = await res.text();
    if (!res.ok) {
      console.error("[tasks GET] Supabase error:", res.status, text);
      return Response.json([], { status: 200 });
    }
    return Response.json(JSON.parse(text));
  } catch (e) {
    console.error("[tasks GET] exception:", String(e));
    return Response.json([], { status: 200 });
  }
}

export async function POST(req: Request) {
  try {
    const body = await req.json();
    const payload = {
      owner_email:       OWNER_EMAIL,
      card_name:         body.cardName,
      description:       body.description ?? null,
      due_date:          body.dueDate ?? null,
      due_complete:      body.dueComplete ?? false,
      assignees:         body.assignees ?? [],
      labels:            body.labels ?? [],
      checklist_total:   body.checklistTotal ?? 0,
      checklist_done:    body.checklistDone ?? 0,
      calendar_group_id: body.calendarGroupId ?? null,
      trello_card_id:    body.trelloCardId ?? null,
      trello_list_id:    body.trelloListId ?? null,
      list_name:         body.listName ?? null,
      board_id:          body.boardId ?? null,
      board_name:        body.boardName ?? null,
      sort_order:        body.sortOrder ?? 0,
    };
    const res = await fetch(`${SUPABASE_URL}/rest/v1/tasks`, {
      method: "POST",
      headers: sbHeaders(),
      body: JSON.stringify(payload),
    });
    const text = await res.text();
    if (!res.ok) {
      console.error("[tasks POST] Supabase error:", res.status, text);
      return Response.json({ error: text }, { status: 500 });
    }
    return Response.json(JSON.parse(text));
  } catch (e) {
    console.error("[tasks POST] exception:", String(e));
    return Response.json({ error: String(e) }, { status: 500 });
  }
}
