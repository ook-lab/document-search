// GET /api/calendar/members
// 環境変数から家族メンバー一覧を返す
export async function GET() {
  const members = [
    { key: "mama",  label: "ママ",   email: process.env.SHARE_MEMBER_MAMA },
    { key: "ema",   label: "絵麻",   email: process.env.SHARE_MEMBER_EMA },
    { key: "ikuya", label: "育哉",   email: process.env.SHARE_MEMBER_IKUYA },
    { key: "test",  label: "テスト", email: process.env.SHARE_MEMBER_TEST },
  ].filter((m) => m.email);
  return Response.json(members);
}
