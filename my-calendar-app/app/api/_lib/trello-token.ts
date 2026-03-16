const SUPABASE_URL = process.env.SUPABASE_URL!;
const SUPABASE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY!;

function sbHeaders() {
  return {
    "apikey": SUPABASE_KEY,
    "Authorization": `Bearer ${SUPABASE_KEY}`,
    "Content-Type": "application/json",
  };
}

export async function getTrelloToken(email: string): Promise<string | null> {
  try {
    const res = await fetch(
      `${SUPABASE_URL}/rest/v1/user_trello_tokens?email=eq.${encodeURIComponent(email)}&select=token`,
      { headers: sbHeaders() }
    );
    if (!res.ok) return null;
    const rows = await res.json();
    return rows[0]?.token ?? null;
  } catch {
    return null;
  }
}

export async function saveTrelloToken(email: string, token: string): Promise<void> {
  const res = await fetch(`${SUPABASE_URL}/rest/v1/user_trello_tokens?on_conflict=email`, {
    method: "POST",
    headers: { ...sbHeaders(), "Prefer": "resolution=merge-duplicates,return=minimal" },
    body: JSON.stringify({ email, token, updated_at: new Date().toISOString() }),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`saveTrelloToken failed: ${res.status} ${text}`);
  }
}

export async function deleteTrelloToken(email: string): Promise<void> {
  await fetch(
    `${SUPABASE_URL}/rest/v1/user_trello_tokens?email=eq.${encodeURIComponent(email)}`,
    { method: "DELETE", headers: sbHeaders() }
  );
}
