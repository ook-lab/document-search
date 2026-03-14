import { getServerSession } from "next-auth";
import { authOptions } from "./auth-options";

// セッションからアクセストークンを取得
export async function getAccessToken(): Promise<string> {
  const session = await getServerSession(authOptions);
  if (session?.accessToken) return session.accessToken;
  throw new Error("Not authenticated");
}
