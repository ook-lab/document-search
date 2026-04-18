import { getAccessToken } from "../../_lib/google-token";

const BASE = "https://www.googleapis.com/calendar/v3";

async function authHeaders() {
  const token = await getAccessToken();
  return { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };
}

// カレンダーのACL一覧からemailのruleIdを取得
async function findAclRuleId(calendarId: string, email: string): Promise<string | null> {
  const headers = await authHeaders();
  const res = await fetch(`${BASE}/calendars/${encodeURIComponent(calendarId)}/acl`, { headers });
  if (!res.ok) return null;
  const data = await res.json();
  const rule = (data.items ?? []).find((r: Record<string, unknown>) => {
    const scope = r.scope as Record<string, string> | undefined;
    return scope?.value === email;
  });
  return rule ? (rule.id as string) : null;
}

// カレンダーにACLを追加
async function addAcl(calendarId: string, email: string): Promise<void> {
  const headers = await authHeaders();
  const res = await fetch(`${BASE}/calendars/${encodeURIComponent(calendarId)}/acl?sendNotifications=true`, {
    method: "POST",
    headers,
    body: JSON.stringify({ role: "writer", scope: { type: "user", value: email } }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(`ACL設定失敗 (${calendarId}): ${err?.error?.message ?? res.status}`);
  }
}

// カレンダーのACLを削除
async function removeAcl(calendarId: string, email: string): Promise<void> {
  const ruleId = await findAclRuleId(calendarId, email);
  if (!ruleId) return;
  const headers = await authHeaders();
  await fetch(`${BASE}/calendars/${encodeURIComponent(calendarId)}/acl/${encodeURIComponent(ruleId)}`, {
    method: "DELETE",
    headers,
  });
}

// カレンダーを作成して新しいIDを返す
async function createCalendar(summary: string): Promise<string> {
  const headers = await authHeaders();
  const res = await fetch(`${BASE}/calendars`, {
    method: "POST",
    headers,
    body: JSON.stringify({ summary }),
  });
  const data = await res.json();
  return data.id as string;
}

// GET /api/calendar/share?ids=id1,id2,...
// 各カレンダーの共有メールアドレス一覧を返す
export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const ids = searchParams.get("ids")?.split(",").filter(Boolean) ?? [];
  if (ids.length === 0) return Response.json({});

  const headers = await authHeaders();
  const result: Record<string, string[]> = {};

  await Promise.all(ids.map(async (id) => {
    const res = await fetch(`${BASE}/calendars/${encodeURIComponent(id)}/acl`, { headers });
    if (!res.ok) { result[id] = []; return; }
    const data = await res.json();
    result[id] = (data.items ?? [])
      .map((r: Record<string, unknown>) => (r.scope as Record<string, string>)?.value ?? "")
      .filter((v: string) => v && !v.includes("@group.calendar.google.com"));
  }));

  return Response.json(result);
}

// POST /api/calendar/share
// body: { baseId, baseName, penId?, arcId?, memberEmail }
// _pen/_arc を作成（なければ）してすべてを共有
export async function POST(req: Request) {
  try {
    const { baseId, baseName, penId, arcId, memberEmail } = await req.json();
    if (!baseId || !baseName || !memberEmail)
      return Response.json({ error: "baseId, baseName, memberEmail required" }, { status: 400 });

    let newPenId = penId ?? null;
    let newArcId = arcId ?? null;

    if (!newPenId) {
      newPenId = await createCalendar(`${baseName}_pen`);
    }
    if (!newArcId) {
      newArcId = await createCalendar(`${baseName}_arc`);
    }

    await Promise.all([
      addAcl(baseId,   memberEmail),
      addAcl(newPenId, memberEmail),
      addAcl(newArcId, memberEmail),
    ]);

    return Response.json({ penId: newPenId, arcId: newArcId });
  } catch (e) {
    return Response.json({ error: String(e) }, { status: 500 });
  }
}

// DELETE /api/calendar/share
// body: { baseId, penId?, arcId?, memberEmail }
// 共有を解除
export async function DELETE(req: Request) {
  try {
    const { baseId, penId, arcId, memberEmail } = await req.json();
    if (!baseId || !memberEmail)
      return Response.json({ error: "baseId, memberEmail required" }, { status: 400 });

    const targets = [baseId, penId, arcId].filter(Boolean) as string[];
    await Promise.all(targets.map((id) => removeAcl(id, memberEmail)));

    return Response.json({ ok: true });
  } catch (e) {
    return Response.json({ error: String(e) }, { status: 500 });
  }
}
