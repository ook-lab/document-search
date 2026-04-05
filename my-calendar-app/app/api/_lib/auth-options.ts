import { NextAuthOptions } from "next-auth";
import GoogleProvider from "next-auth/providers/google";

export const authOptions: NextAuthOptions = {
  providers: [
    GoogleProvider({
      clientId:     process.env.GOOGLE_CLIENT_ID!,
      clientSecret: process.env.GOOGLE_CLIENT_SECRET!,
      authorization: {
        params: {
          scope:       "openid email profile https://www.googleapis.com/auth/calendar",
          access_type: "offline",
          prompt:      "consent",
        },
      },
    }),
  ],
  callbacks: {
    async jwt({ token, account }) {
      // 初回サインイン時
      if (account) {
        return {
          ...token,
          accessToken:  account.access_token,
          refreshToken: account.refresh_token,
          expiresAt:    account.expires_at, // Unix秒
        };
      }
      // トークン有効期限前なら返す
      if (Date.now() / 1000 < (token.expiresAt as number) - 30) {
        return token;
      }
      // 期限切れ → リフレッシュ
      try {
        const res = await fetch("https://oauth2.googleapis.com/token", {
          method: "POST",
          headers: { "Content-Type": "application/x-www-form-urlencoded" },
          body: new URLSearchParams({
            client_id:     process.env.GOOGLE_CLIENT_ID!,
            client_secret: process.env.GOOGLE_CLIENT_SECRET!,
            refresh_token: token.refreshToken as string,
            grant_type:    "refresh_token",
          }),
        });
        const data = await res.json();
        if (!res.ok) throw data;
        return {
          ...token,
          accessToken: data.access_token,
          expiresAt:   Math.floor(Date.now() / 1000) + data.expires_in,
          refreshToken: data.refresh_token ?? token.refreshToken,
        };
      } catch {
        return { ...token, error: "RefreshAccessTokenError" };
      }
    },
    async session({ session, token }) {
      session.accessToken = token.accessToken as string | undefined;
      session.error       = token.error as string | undefined;
      return session;
    },
  },
};
