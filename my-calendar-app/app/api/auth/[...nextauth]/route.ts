import NextAuth, { NextAuthOptions } from "next-auth";
import GoogleProvider from "next-auth/providers/google";

async function refreshAccessToken(refreshToken: string): Promise<{
  accessToken: string;
  expiresAt: number;
  refreshToken: string;
} | null> {
  const res = await fetch("https://oauth2.googleapis.com/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      client_id:     process.env.GOOGLE_CLIENT_ID!,
      client_secret: process.env.GOOGLE_CLIENT_SECRET!,
      grant_type:    "refresh_token",
      refresh_token: refreshToken,
    }),
  });
  if (!res.ok) return null;
  const data = await res.json();
  return {
    accessToken:  data.access_token,
    expiresAt:    Math.floor(Date.now() / 1000) + data.expires_in,
    refreshToken: data.refresh_token ?? refreshToken,
  };
}

export const authOptions: NextAuthOptions = {
  providers: [
    GoogleProvider({
      clientId: process.env.GOOGLE_CLIENT_ID!,
      clientSecret: process.env.GOOGLE_CLIENT_SECRET!,
      authorization: {
        params: {
          scope: "openid email profile https://www.googleapis.com/auth/calendar",
          prompt: "consent",
          access_type: "offline",
          response_type: "code",
        },
      },
    }),
  ],
  callbacks: {
    async jwt({ token, account }) {
      // 初回ログイン時
      if (account) {
        token.accessToken  = account.access_token;
        token.refreshToken = account.refresh_token;
        token.expiresAt    = account.expires_at;
        return token;
      }
      // トークンがまだ有効（30秒マージン）
      if (Date.now() / 1000 < (token.expiresAt as number) - 30) {
        return token;
      }
      // リフレッシュ
      const refreshed = await refreshAccessToken(token.refreshToken as string);
      if (!refreshed) return { ...token, error: "RefreshTokenError" };
      return {
        ...token,
        accessToken:  refreshed.accessToken,
        refreshToken: refreshed.refreshToken,
        expiresAt:    refreshed.expiresAt,
      };
    },
    async session({ session, token }) {
      session.accessToken = token.accessToken as string | undefined;
      if (token.error) session.error = token.error as string;
      return session;
    },
  },
};

const handler = NextAuth(authOptions);
export { handler as GET, handler as POST };
