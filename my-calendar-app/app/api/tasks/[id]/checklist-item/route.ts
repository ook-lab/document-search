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

type CheckItem = { id: string; name: string; state: "complete" | "incomplete" };
type Checklist = { id: string; name: string; checkItems: CheckItem[] };

async function getChecklists(taskId: string): Promise<Checklist[]> {
  const res = await fetch(
    `${SUPABASE_URL}/rest/v1/tasks?id=eq.${taskId}&owner_email=eq.${encodeURIComponent(OWNER_EMAIL)}&select=checklists`,
    { headers: sbHeaders() }
  );
  const rows = await res.json();
  return rows[0]?.checklists ?? [];
}

function calcProgress(checklists: Checklist[]): { total: number; done: number } {
  let total = 0, done = 0;
  for (const cl of checklists) {
    for (const item of cl.checkItems ?? []) {
      total++;
      if (item.state === "complete") done++;
    }
  }
  return { total, done };
}

async function patchTask(taskId: string, patch: Record<string, unknown>) {
  await fetch(
    `${SUPABASE_URL}/rest/v1/tasks?id=eq.${taskId}&owner_email=eq.${encodeURIComponent(OWNER_EMAIL)}`,
    { method: "PATCH", headers: sbHeaders(), body: JSON.stringify(patch) }
  );
}

// PUT: アイテムの状態切り替え
export async function PUT(req: Request, { params }: { params: Promise<{ id: string }> }) {
  try {
    const { id } = await params;
    const { checklistId, checkItemId, state, trelloCardId } = await req.json();

    // Trello API
    if (TRELLO_KEY && TRELLO_TOKEN && trelloCardId && checkItemId) {
      await fetch(
        `https://api.trello.com/1/cards/${trelloCardId}/checkItem/${checkItemId}?key=${TRELLO_KEY}&token=${TRELLO_TOKEN}`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ state }),
        }
      );
    }

    // Supabase更新
    const current = await getChecklists(id);
    const updated = current.map(cl =>
      cl.id !== checklistId ? cl : {
        ...cl,
        checkItems: cl.checkItems.map(item =>
          item.id === checkItemId ? { ...item, state } : item
        ),
      }
    );
    const { total, done } = calcProgress(updated);
    await patchTask(id, { checklists: updated, checklist_total: total, checklist_done: done });

    return Response.json({ ok: true, checklist_total: total, checklist_done: done });
  } catch (e) {
    return Response.json({ error: String(e) }, { status: 500 });
  }
}

// POST: アイテム追加
export async function POST(req: Request, { params }: { params: Promise<{ id: string }> }) {
  try {
    const { id } = await params;
    const { checklistId, name, trelloCardId } = await req.json();
    if (!checklistId || !name) return Response.json({ error: "checklistId, name required" }, { status: 400 });

    // Trello APIでアイテム作成
    let newItem: CheckItem | null = null;
    if (TRELLO_KEY && TRELLO_TOKEN) {
      const res = await fetch(
        `https://api.trello.com/1/checklists/${checklistId}/checkItems?key=${TRELLO_KEY}&token=${TRELLO_TOKEN}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name }),
        }
      );
      if (res.ok) {
        const j = await res.json();
        newItem = { id: j.id, name: j.name, state: "incomplete" };
      }
    }
    if (!newItem) return Response.json({ error: "Trello API failed" }, { status: 500 });

    // Supabase更新
    const current = await getChecklists(id);
    const updated = current.map(cl =>
      cl.id !== checklistId ? cl : { ...cl, checkItems: [...cl.checkItems, newItem!] }
    );
    const { total, done } = calcProgress(updated);
    await patchTask(id, { checklists: updated, checklist_total: total, checklist_done: done });

    return Response.json(newItem);
  } catch (e) {
    return Response.json({ error: String(e) }, { status: 500 });
  }
}

// DELETE: アイテム削除 (?checklistId=xxx&checkItemId=yyy&trelloCardId=zzz)
export async function DELETE(req: Request, { params }: { params: Promise<{ id: string }> }) {
  try {
    const { id } = await params;
    const { searchParams } = new URL(req.url);
    const checklistId  = searchParams.get("checklistId");
    const checkItemId  = searchParams.get("checkItemId");
    const trelloCardId = searchParams.get("trelloCardId");
    if (!checklistId || !checkItemId) return Response.json({ error: "params required" }, { status: 400 });

    // Trello API
    if (TRELLO_KEY && TRELLO_TOKEN && trelloCardId) {
      await fetch(
        `https://api.trello.com/1/cards/${trelloCardId}/checkItem/${checkItemId}?key=${TRELLO_KEY}&token=${TRELLO_TOKEN}`,
        { method: "DELETE" }
      );
    }

    // Supabase更新
    const current = await getChecklists(id);
    const updated = current.map(cl =>
      cl.id !== checklistId ? cl : { ...cl, checkItems: cl.checkItems.filter(item => item.id !== checkItemId) }
    );
    const { total, done } = calcProgress(updated);
    await patchTask(id, { checklists: updated, checklist_total: total, checklist_done: done });

    return Response.json({ ok: true });
  } catch (e) {
    return Response.json({ error: String(e) }, { status: 500 });
  }
}
