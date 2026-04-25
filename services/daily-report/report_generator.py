"""
日次レポート生成エンジン（v2）

10_report_candidates を検索して 8ページの日次レポートを生成する。

Pages 1-7: 今日〜6日後の1日1ページ
Page  8  : 今後2週間以内の未完了タスク
"""
import json
from datetime import date, timedelta, datetime, timezone
from typing import Optional

# tzdata 不要・JST = UTC+9 固定
JST = timezone(timedelta(hours=9))
GEMINI_MODEL = "gemini-2.5-flash-lite"
WEEKDAYS_JP = ["日", "月", "火", "水", "木", "金", "土"]

TABLE = "10_report_candidates"


def _weekday_jp(d: date) -> str:
    return WEEKDAYS_JP[d.isoweekday() % 7]


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
            "base_date":    base_date.isoformat(),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "pages":        pages,
        }

    def save(self, report: dict) -> str:
        """11_daily_reports テーブルに upsert して id を返す"""
        result = (
            self.db.client.table("11_daily_reports")
            .upsert(
                {
                    "base_date":    report["base_date"],
                    "report_json":  report,
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
        wday      = _weekday_jp(target)
        title_map = {1: "今日", 2: "明日", 3: "明後日"}
        title     = title_map.get(page_no) or f"{page_no}日後"

        # 1. 構造化データ（10_report_candidates から）
        candidates = self._get_candidates(target)

        # 2. ベクトル検索（補足情報：持ち物・注意事項・課題説明など）
        vector_hits = self._vector_search(target, top_k=12)

        # 3. AI合成（Gemini Flash-lite）
        ai_page = self._synthesize_day(target, candidates, vector_hits)

        return {
            "page_no": page_no,
            "date":    target.isoformat(),
            "weekday": wday,
            "title":   title,
            "display": f"{target.month}/{target.day}({wday})",
            **ai_page,
        }

    # ─────────────────────────────────────────────────────────
    # 10_report_candidates 検索
    # ─────────────────────────────────────────────────────────

    def _get_candidates(self, target: date) -> list[dict]:
        """
        10_report_candidates から当日に関係するレコードを取得する。
        date_primary = target OR (date_start が当日) OR (due_date = target)
        """
        target_str = target.isoformat()
        cols = (
            "id,record_type,subtype,title,summary,person,source,category,"
            "source_priority,date_primary,date_start,date_end,due_date,"
            "is_actionable,is_completed,report_priority,details_json"
        )

        # ① date_primary = target
        r1 = (
            self.db.client.table(TABLE)
            .select(cols)
            .eq("date_primary", target_str)
            .eq("is_report_worthy", True)
            .order("report_priority")
            .execute()
        )

        # ② due_date = target（date_primary != target のもの）
        r2 = (
            self.db.client.table(TABLE)
            .select(cols)
            .eq("due_date", target_str)
            .neq("date_primary", target_str)
            .eq("is_report_worthy", True)
            .order("report_priority")
            .execute()
        )

        # 重複除去
        seen, rows = set(), []
        for row in (r1.data or []) + (r2.data or []):
            if row["id"] not in seen:
                seen.add(row["id"])
                rows.append(row)

        # report_priority 昇順にソート
        rows.sort(key=lambda x: (x.get("report_priority") or 5, x.get("source_priority") or 5))
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
            embedding = self.llm.generate_embedding(
                query,
                log_context={'app': 'daily-report', 'stage': 'vector-search-embedding'}
            )
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

    def _synthesize_day(
        self,
        target: date,
        candidates: list,
        vector_hits: list,
    ) -> dict:
        """Gemini Flash-lite で1日分ページを合成"""

        def _compact_candidate(c: dict) -> dict:
            details = c.get("details_json") or {}
            if isinstance(details, str):
                try:
                    details = json.loads(details)
                except Exception:
                    details = {}
            return {
                "type":        c.get("record_type"),
                "title":       c.get("title"),
                "person":      c.get("person"),
                "source":      c.get("source"),
                "start":       (str(c.get("date_start") or ""))[:16],
                "end":         (str(c.get("date_end") or ""))[:16],
                "due":         str(c.get("due_date") or ""),
                "actionable":  c.get("is_actionable"),
                "description": details.get("description"),
                "location":    details.get("location"),
            }

        def _compact_hit(h: dict) -> dict:
            return {
                "title":  h.get("title"),
                "source": h.get("source"),
                "chunk":  (h.get("best_chunk_text") or "")[:300],
                "score":  round(h.get("combined_score") or 0, 3),
            }

        candidates_json = json.dumps(
            [_compact_candidate(c) for c in candidates], ensure_ascii=False
        )
        hits = [h for h in vector_hits if (h.get("combined_score") or 0) >= 0.3]
        hits_json = json.dumps(
            [_compact_hit(h) for h in hits[:8]], ensure_ascii=False
        )

        date_str = f"{target.year}年{target.month}月{target.day}日({_weekday_jp(target)})"
        prompt = f"""あなたは家族のスケジュール管理アシスタントです。
以下のデータから {date_str} の1ページレポートを日本語で作成してください。

## 候補レコード（カレンダー・課題・注意事項等）
{candidates_json}

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
- type=event → schedule に、type=task/homework_item → homework に、type=notice → notices に分類
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
            import vertexai
from vertexai.generative_models import GenerativeModel, Part, GenerationConfig
            from shared.common.config.settings import settings
            vertexai.init(location="asia-northeast1")
            model = GenerativeModel(GEMINI_MODEL)
            resp  = model.generate_content(
                prompt,
                generation_config=GenerationConfig(temperature=0.1),
            )
            text = resp.text.strip()
            # マークダウンコードブロックを除去
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.strip()
            return json.loads(text)
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
        cols = (
            "id,record_type,title,person,source,due_date,"
            "is_completed,is_actionable,report_priority,details_json"
        )

        rows = (
            self.db.client.table(TABLE)
            .select(cols)
            .gte("due_date", today_str)
            .lte("due_date", horizon_str)
            .eq("is_completed", False)
            .eq("is_actionable", True)
            .eq("is_report_worthy", True)
            .order("due_date", desc=False)
            .order("report_priority", desc=False)
            .limit(80)
            .execute()
        ).data or []

        tasks = []
        for row in rows:
            details = row.get("details_json") or {}
            if isinstance(details, str):
                try:
                    details = json.loads(details)
                except Exception:
                    details = {}
            tasks.append({
                "person":      row.get("person"),
                "source":      row.get("source"),
                "doc_title":   details.get("doc_title") or row.get("title"),
                "task":        row.get("title"),
                "description": details.get("description"),
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
