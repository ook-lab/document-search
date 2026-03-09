"""
日次レポート生成エンジン

Supabase (09_unified_documents + unified_search_v2) から
8ページの日次レポートを生成する。

Pages 1-7: 今日〜6日後の1日1ページ
Page  8  : 今後2週間以内の未完了タスク
"""
import json
from datetime import date, timedelta, datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
GEMINI_MODEL = "gemini-2.5-flash-lite-preview-06-17"
WEEKDAYS_JP = ["日", "月", "火", "水", "木", "金", "土"]


def _weekday_jp(d: date) -> str:
    # Python isoweekday: Mon=1, Sun=7 → JP: Sun=0, Mon=1, ..., Sat=6
    return WEEKDAYS_JP[d.isoweekday() % 7]


def _jst_day_to_utc_range(target: date) -> tuple[str, str]:
    """JST の1日分を UTC ISO文字列の範囲に変換"""
    start = datetime(target.year, target.month, target.day, 0, 0, 0, tzinfo=JST)
    end   = datetime(target.year, target.month, target.day, 23, 59, 59, tzinfo=JST)
    return (
        start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
    )


def _parse_ui(ui_raw) -> dict:
    if isinstance(ui_raw, str):
        try:
            return json.loads(ui_raw)
        except Exception:
            return {}
    return ui_raw or {}


class ReportGenerator:
    def __init__(self, db_client, llm_client):
        self.db  = db_client
        self.llm = llm_client

    # ─────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────

    def generate(self, base_date: Optional[date] = None) -> dict:
        """8ページレポートを生成して返す"""
        if base_date is None:
            base_date = datetime.now(JST).date()

        pages = []
        for i in range(7):
            target = base_date + timedelta(days=i)
            print(f"[INFO] ページ {i+1} 生成中: {target.isoformat()}")
            pages.append(self._build_day_page(target, page_no=i + 1))

        print("[INFO] ページ 8（未完了タスク）生成中")
        pages.append(self._build_incomplete_page(base_date))

        return {
            "base_date": base_date.isoformat(),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "pages": pages,
        }

    def save(self, report: dict) -> str:
        """daily_reports テーブルに upsert して id を返す"""
        result = (
            self.db.client.table("daily_reports")
            .upsert(
                {
                    "base_date": report["base_date"],
                    "report_json": report,
                    "generated_at": report["generated_at"],
                },
                on_conflict="base_date",
            )
            .execute()
        )
        return ((result.data or [{}])[0]).get("id", "")

    # ─────────────────────────────────────────────────────────
    # Day page builder
    # ─────────────────────────────────────────────────────────

    def _build_day_page(self, target: date, page_no: int) -> dict:
        wday  = _weekday_jp(target)
        title_map = {1: "今日", 2: "明日", 3: "明後日"}
        title = title_map.get(page_no) or f"{page_no}日後"

        # 1. 構造化データ（カレンダー予定 / due_date）
        events = self._get_structured(target)

        # 2. ベクトル検索（補足情報：持ち物・注意事項・課題説明など）
        vector_hits = self._vector_search(target, top_k=12)

        # 3. AI合成（Gemini Flash-lite）
        ai_page = self._synthesize_day(target, events, vector_hits)

        return {
            "page_no":  page_no,
            "date":     target.isoformat(),
            "weekday":  wday,
            "title":    title,
            "display":  f"{target.month}/{target.day}({wday})",
            **ai_page,
        }

    # ─────────────────────────────────────────────────────────
    # Structured search
    # ─────────────────────────────────────────────────────────

    def _get_structured(self, target: date) -> list[dict]:
        """09_unified_documents から当日 start_at / due_date に関係するデータを取得"""
        start_utc, end_utc = _jst_day_to_utc_range(target)
        target_str = target.isoformat()
        cols = "id,person,source,category,title,start_at,end_at,due_date,ui_data,meta"

        # ① start_at が当日 JST 範囲のもの（カレンダー予定）
        r1 = (
            self.db.client.table("09_unified_documents")
            .select(cols)
            .gte("start_at", start_utc)
            .lte("start_at", end_utc)
            .execute()
        )

        # ② due_date が当日のもの（課題・締切）
        r2 = (
            self.db.client.table("09_unified_documents")
            .select(cols)
            .eq("due_date", target_str)
            .execute()
        )

        # 重複除去（同じ ID は1件のみ）
        seen, rows = set(), []
        for row in (r1.data or []) + (r2.data or []):
            if row["id"] not in seen:
                seen.add(row["id"])
                rows.append(row)
        return rows

    # ─────────────────────────────────────────────────────────
    # Vector search
    # ─────────────────────────────────────────────────────────

    def _vector_search(self, target: date, top_k: int = 12) -> list[dict]:
        """unified_search_v2 RPC でベクトル検索（補足情報取得）"""
        query = (
            f"date: {target.isoformat()}\n"
            f"need: schedule homework submission items_to_bring notices exam"
        )
        try:
            embedding = self.llm.generate_embedding(query)
            result = self.db.client.rpc(
                "unified_search_v2",
                {
                    "query_text":      query,
                    "query_embedding": embedding,
                    "match_threshold": 0.25,
                    "match_count":     top_k,
                },
            ).execute()
            return result.data or []
        except Exception as e:
            print(f"[WARN] vector search failed ({target}): {e}")
            return []

    # ─────────────────────────────────────────────────────────
    # AI synthesis – 1 day → 1 Gemini call
    # ─────────────────────────────────────────────────────────

    def _synthesize_day(self, target: date, events: list, vector_hits: list) -> dict:
        """Gemini Flash-lite で1日分ページを合成"""

        def _compact_event(e: dict) -> dict:
            ui = _parse_ui(e.get("ui_data"))
            return {
                "title":    e.get("title"),
                "person":   e.get("person"),
                "source":   e.get("source"),
                "start":    (str(e.get("start_at") or ""))[:16],
                "end":      (str(e.get("end_at") or ""))[:16],
                "due":      str(e.get("due_date") or ""),
                "actions":  ui.get("actions", []),
                "notices":  ui.get("notices", []),
                "timeline": ui.get("timeline", []),
            }

        def _compact_hit(h: dict) -> dict:
            return {
                "title":  h.get("title"),
                "source": h.get("source"),
                "chunk":  (h.get("best_chunk_text") or "")[:300],
                "score":  round(h.get("combined_score") or 0, 3),
            }

        events_json = json.dumps(
            [_compact_event(e) for e in events], ensure_ascii=False
        )
        # スコア 0.3 以上のベクトルヒットのみ渡す
        hits = [h for h in vector_hits if (h.get("combined_score") or 0) >= 0.3]
        hits_json = json.dumps(
            [_compact_hit(h) for h in hits[:8]], ensure_ascii=False
        )

        date_str = f"{target.year}年{target.month}月{target.day}日({_weekday_jp(target)})"
        prompt = f"""あなたは家族のスケジュール管理アシスタントです。
以下のデータから {date_str} の1ページレポートを日本語で作成してください。

## カレンダー・構造化データ
{events_json}

## 関連ドキュメント（ベクトル検索結果）
{hits_json}

## 出力形式（JSON のみ・コードブロック不要）
{{
  "schedule": [
    {{"time": "HH:MM または 終日", "title": "タイトル", "person": "担当者", "note": "補足（任意）"}}
  ],
  "homework": [
    {{"subject": "科目", "task": "内容", "person": "担当者", "deadline": "期限（任意）"}}
  ],
  "items_to_bring": ["持ち物1"],
  "notices": ["注意事項1"],
  "summary": "この日の一言まとめ（50文字以内）"
}}

ルール:
- 重複する情報は1つにまとめる
- データがない項目は空配列 []
- 事実のみ記載し、推測で補完しない
- JSON のみ出力（説明文・マークダウン不要）"""

        empty = {
            "schedule": [], "homework": [],
            "items_to_bring": [], "notices": [],
            "summary": "この日の予定はありません",
        }
        try:
            import google.generativeai as genai
            from shared.common.config.settings import settings
            genai.configure(api_key=settings.GOOGLE_AI_API_KEY)
            model = genai.GenerativeModel(GEMINI_MODEL)
            resp = model.generate_content(
                prompt,
                generation_config=genai.GenerationConfig(
                    temperature=0.1,
                    response_mime_type="application/json",
                ),
            )
            return json.loads(resp.text)
        except Exception as e:
            print(f"[WARN] AI synthesis failed ({target}): {e}")
            return empty

    # ─────────────────────────────────────────────────────────
    # Page 8: Incomplete / upcoming tasks
    # ─────────────────────────────────────────────────────────

    def _build_incomplete_page(self, base_date: date) -> dict:
        """今後2週間以内に due_date があるタスクをまとめてページ8を生成"""
        horizon     = base_date + timedelta(days=13)
        today_str   = base_date.isoformat()
        horizon_str = horizon.isoformat()
        cols = "id,person,source,category,title,due_date,ui_data"

        rows = (
            self.db.client.table("09_unified_documents")
            .select(cols)
            .gte("due_date", today_str)
            .lte("due_date", horizon_str)
            .order("due_date", desc=False)
            .limit(60)
            .execute()
        ).data or []

        tasks = []
        for row in rows:
            ui      = _parse_ui(row.get("ui_data"))
            actions = ui.get("actions") or []
            if actions:
                for a in actions:
                    tasks.append({
                        "person":      row.get("person"),
                        "source":      row.get("source"),
                        "doc_title":   row.get("title"),
                        "task":        a.get("item") or a.get("text") or a.get("title"),
                        "description": a.get("description"),
                        "deadline":    a.get("deadline") or str(row.get("due_date") or ""),
                    })
            else:
                tasks.append({
                    "person":      row.get("person"),
                    "source":      row.get("source"),
                    "doc_title":   row.get("title"),
                    "task":        row.get("title"),
                    "description": None,
                    "deadline":    str(row.get("due_date") or ""),
                })

        return {
            "page_no": 8,
            "date":    None,
            "weekday": None,
            "title":   "未完了タスク",
            "display": "未完了タスク",
            "tasks":   tasks,
            "summary": f"今後2週間以内の期限タスク {len(tasks)} 件",
        }
