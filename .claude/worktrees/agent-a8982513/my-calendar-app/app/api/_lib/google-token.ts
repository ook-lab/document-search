let cache: { accessToken: string; expiresAt: number } | null = null;

export async function getAccessToken(): Promise<string> {
  if (cache && Date.now() / 1000 < cache.expiresAt - 30) {
    return cache.accessToken;
  }
  const res = await fetch("https://oauth2.googleapis.com/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      client_id:     process.env.GOOGLE_CLIENT_ID!,
      client_secret: process.env.GOOGLE_CLIENT_SECRET!,
      refresh_token: process.env.GOOGLE_REFRESH_TOKEN!,
      grant_type:    "refresh_token",
    }),
  });
  if (!res.ok) throw new Error("token refresh failed");
  const data = await res.json();
  cache = {
    accessToken: data.access_token,
    expiresAt:   Math.floor(Date.now() / 1000) + data.expires_in,
  };
  return cache.accessToken;
}
