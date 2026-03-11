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

type MemberData = { id: string; name: string };

async function getMembersData(taskId: string): Promise<MemberData[]> {
  const res = await fetch(
    `${SUPABASE_URL}/rest/v1/tasks?id=eq.${taskId}&owner_email=eq.${encodeURIComponent(OWNER_EMAIL)}&select=members_data`,
    { headers: sbHeaders() }
  );
  const rows = await res.json();
  return rows[0]?.members_data ?? [];
}

async function patchTask(taskId: string, patch: Record<string, unknown>) {
  await fetch(
    `${SUPABASE_URL}/rest/v1/tasks?id=eq.${taskId}&owner_email=eq.${encodeURIComponent(OWNER_EMAIL)}`,
    { method: "PATCH", headers: sbHeaders(), body: JSON.stringify(patch) }
  );
}

// POST: メンバー追加
export async function POST(req: Request, { params }: { params: Promise<{ id: string }> }) {
  try {
    const { id } = await params;
    const { memberId, memberName, trelloCardId } = await req.json();
    if (!memberId || !memberName) return Response.json({ error: "memberId, memberName required" }, { status: 400 });

    // Trello API
    if (TRELLO_KEY && TRELLO_TOKEN && trelloCardId) {
      await fetch(
        `https://api.trello.com/1/cards/${trelloCardId}/idMembers?key=${TRELLO_KEY}&token=${TRELLO_TOKEN}&value=${memberId}`,
        { method: "POST" }
      );
    }

    // Supabase更新
    const current = await getMembersData(id);
    if (!current.some(m => m.id === memberId)) {
      const updated: MemberData[] = [...current, { id: memberId, name: memberName }];
      await patchTask(id, {
        members_data: updated,
        assignees: updated.map(m => m.name),
      });
    }

    return Response.json({ ok: true });
  } catch (e) {
    return Response.json({ error: String(e) }, { status: 500 });
  }
}

// DELETE: メンバー削除 (?memberId=xxx&trelloCardId=yyy)
export async function DELETE(req: Request, { params }: { params: Promise<{ id: string }> }) {
  try {
    const { id } = await params;
    const { searchParams } = new URL(req.url);
    const memberId     = searchParams.get("memberId");
    const trelloCardId = searchParams.get("trelloCardId");
    if (!memberId) return Response.json({ error: "memberId required" }, { status: 400 });

    // Trello API
    if (TRELLO_KEY && TRELLO_TOKEN && trelloCardId) {
      await fetch(
        `https://api.trello.com/1/cards/${trelloCardId}/idMembers/${memberId}?key=${TRELLO_KEY}&token=${TRELLO_TOKEN}`,
        { method: "DELETE" }
      );
    }

    // Supabase更新
    const current = await getMembersData(id);
    const updated = current.filter(m => m.id !== memberId);
    await patchTask(id, {
      members_data: updated,
      assignees: updated.map(m => m.name),
    });

    return Response.json({ ok: true });
  } catch (e) {
    return Response.json({ error: String(e) }, { status: 500 });
  }
}
