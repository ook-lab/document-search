import { getServerSession } from "next-auth";
import { authOptions } from "../../_lib/auth-options";

const TRELLO_KEY   = process.env.TRELLO_API_KEY ?? "";
const APP_BASE_URL = process.env.NEXT_PUBLIC_BASE_URL ?? "https://my-calendar-app-983922127476.asia-northeast1.run.app";

// GET /api/trello/auth → Trello OAuth ページへリダイレクト
export async function GET() {
  const session = await getServerSession(authOptions);
  if (!session?.user?.email) {
    return Response.json({ error: "Unauthorized" }, { status: 401 });
  }
  if (!TRELLO_KEY) {
    return Response.json({ error: "TRELLO_API_KEY 未設定" }, { status: 500 });
  }
  const callbackUrl = `${APP_BASE_URL}/trello-callback`;
  const trelloAuthUrl =
    `https://trello.com/1/authorize` +
    `?expiration=never` +
    `&name=FamilyCalendar` +
    `&scope=read%2Cwrite` +
    `&response_type=token` +
    `&key=${TRELLO_KEY}` +
    `&return_url=${encodeURIComponent(callbackUrl)}`;
  return Response.redirect(trelloAuthUrl);
}
