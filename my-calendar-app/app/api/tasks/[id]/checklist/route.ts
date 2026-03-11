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

async function getTaskRow(taskId: string): Promise<{ checklists: Checklist[]; checklist_total: number; checklist_done: number } | null> {
  const res = await fetch(
    `${SUPABASE_URL}/rest/v1/tasks?id=eq.${taskId}&owner_email=eq.${encodeURIComponent(OWNER_EMAIL)}&select=checklists,checklist_total,checklist_done`,
    { headers: sbHeaders() }
  );
  const rows = await res.json();
  return rows[0] ?? null;
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

// POST: チェックリスト作成
export async function POST(req: Request, { params }: { params: Promise<{ id: string }> }) {
  try {
    const { id } = await params;
    const { name, trelloCardId } = await req.json();
    if (!name || !trelloCardId) return Response.json({ error: "name, trelloCardId required" }, { status: 400 });

    // Trelloにチェックリスト作成
    let newChecklist: Checklist | null = null;
    if (TRELLO_KEY && TRELLO_TOKEN) {
      const res = await fetch(
        `https://api.trello.com/1/checklists?key=${TRELLO_KEY}&token=${TRELLO_TOKEN}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ idCard: trelloCardId, name }),
        }
      );
      if (res.ok) {
        const j = await res.json();
        newChecklist = { id: j.id, name: j.name, checkItems: [] };
      }
    }
    if (!newChecklist) return Response.json({ error: "Trello API failed" }, { status: 500 });

    // Supabase更新
    const row = await getTaskRow(id);
    const updated: Checklist[] = [...(row?.checklists ?? []), newChecklist];
    const { total, done } = calcProgress(updated);
    await patchTask(id, { checklists: updated, checklist_total: total, checklist_done: done });

    return Response.json(newChecklist);
  } catch (e) {
    return Response.json({ error: String(e) }, { status: 500 });
  }
}

// DELETE: チェックリスト削除 (?checklistId=xxx)
export async function DELETE(req: Request, { params }: { params: Promise<{ id: string }> }) {
  try {
    const { id } = await params;
    const { searchParams } = new URL(req.url);
    const checklistId = searchParams.get("checklistId");
    if (!checklistId) return Response.json({ error: "checklistId required" }, { status: 400 });

    // Trelloから削除
    if (TRELLO_KEY && TRELLO_TOKEN) {
      await fetch(`https://api.trello.com/1/checklists/${checklistId}?key=${TRELLO_KEY}&token=${TRELLO_TOKEN}`, { method: "DELETE" });
    }

    // Supabase更新
    const row = await getTaskRow(id);
    const updated = (row?.checklists ?? []).filter(cl => cl.id !== checklistId);
    const { total, done } = calcProgress(updated);
    await patchTask(id, { checklists: updated, checklist_total: total, checklist_done: done });

    return Response.json({ ok: true });
  } catch (e) {
    return Response.json({ error: String(e) }, { status: 500 });
  }
}
