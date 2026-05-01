"""
G18: Report Candidate Extractor

09_unified_documents の1行を受け取り、
10_report_candidates に日報検索用の最小単位レコードを書き込む。

1ドキュメント → 複数 candidate レコード
  - timeline アイテム → record_type=event
  - actions アイテム  → record_type=task
  - notices アイテム  → record_type=notice
  - g21_articles      → record_type=article_context
  - g11_structured_tables（時間割/課題表）→ record_type=timetable_slot / homework_item
  - カレンダーイベント（start_at あり）→ record_type=event（base）
  - 締切あり文書（due_date あり）→ record_type=task（base）
"""
import json
import re
from datetime import date, datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
from loguru import logger

# HH:MM または HH:MM:SS のみ受け付ける（"20:00～" 等は除外）
_TIME_RE = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$")

JST = timezone(timedelta(hours=9))

# ソーステーブル定数（g31 と同じ）
RAW_GCAL      = "02_gcal_01_raw"
RAW_GMAIL     = "01_gmail_01_raw"
RAW_EMA_CLASS = "03_ema_classroom_01_raw"
RAW_IKU_CLASS = "04_ikuya_classroom_01_raw"
RAW_IKU_WASE  = "05_ikuya_waseaca_01_raw"
CLASSROOM_TABLES = {RAW_EMA_CLASS, RAW_IKU_CLASS, RAW_IKU_WASE}

# source_priority: raw_table → 優先度
SOURCE_PRIORITY: Dict[str, int] = {
    RAW_GCAL:      1,
    RAW_EMA_CLASS: 2,
    RAW_IKU_CLASS: 2,
    RAW_IKU_WASE:  2,
    RAW_GMAIL:     5,
    "08_file_only_01_raw": 3,
}

# 時間割/課題表として扱うテーブルセマンティクスキーワード
TIMETABLE_SEMANTICS = {"timetable", "schedule", "時間割", "週間予定"}
HOMEWORK_SEMANTICS  = {"homework", "課題", "提出物", "assignment"}


def _parse_ui(ui_raw) -> dict:
    if isinstance(ui_raw, str):
        try:
            return json.loads(ui_raw)
        except Exception:
            return {}
    return ui_raw or {}


def _parse_date(val) -> Optional[date]:
    """文字列 / date / datetime → date。失敗時 None。"""
    if val is None:
        return None
    if isinstance(val, date):
        return val
    if isinstance(val, datetime):
        return val.date()
    try:
        return date.fromisoformat(str(val)[:10])
    except Exception:
        return None


def _parse_dt(val) -> Optional[str]:
    """値をそのまま文字列化（None は None）。"""
    return str(val) if val is not None else None


def _safe_timestamptz(val) -> Optional[str]:
    """
    TIMESTAMPTZ カラムに挿入する文字列を安全化する。
    - None はそのまま None
    - ISO 8601 っぽい文字列（YYYY-MM-DD で始まる）のみ通す
    - それ以外は None に変換（"20:00～" 等の不正値を除外）
    """
    if val is None:
        return None
    s = str(val).strip()
    # 最低限 "YYYY-MM-DD" で始まっていることを確認
    if re.match(r"^\d{4}-\d{2}-\d{2}", s):
        return s
    return None


class G18CandidateExtractor:
    """09_unified_documents → 10_report_candidates 変換器"""

    def __init__(self, db_client):
        self.db = db_client

    def process(
        self,
        doc_id: str,
        doc: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        1ドキュメントから候補レコードを生成して挿入する。

        Args:
            doc_id: 09_unified_documents.id
            doc:    09_unified_documents の1行データ（辞書）

        Returns:
            {'success': bool, 'inserted': int, 'doc_id': str}
        """
        logger.info(f"[G18] 開始: doc_id={doc_id}")
        try:
            candidates = self._extract(doc_id, doc)
            if not candidates:
                logger.info(f"[G18] 候補なし: doc_id={doc_id}")
                return {"success": True, "inserted": 0, "doc_id": doc_id}

            # 既存の候補を削除してから再挿入（冪等性）
            self.db.client.table("10_report_candidates").delete().eq("doc_id", doc_id).execute()

            # バッチ挿入（100件ずつ）
            total = 0
            for i in range(0, len(candidates), 100):
                chunk = candidates[i : i + 100]
                self.db.client.table("10_report_candidates").insert(chunk).execute()
                total += len(chunk)

            logger.info(f"[G18] 完了: doc_id={doc_id}, inserted={total}")
            return {"success": True, "inserted": total, "doc_id": doc_id}

        except Exception as e:
            logger.error("[G18] エラー: doc_id={}, error={}", doc_id, repr(e))
            return {"success": False, "error": str(e), "doc_id": doc_id}

    # ------------------------------------------------------------------
    # 抽出ロジック
    # ------------------------------------------------------------------

    def _extract(self, doc_id: str, doc: Dict[str, Any]) -> List[Dict[str, Any]]:
        ui      = _parse_ui(doc.get("ui_data"))
        raw_tbl = doc.get("raw_table") or ""
        prio    = SOURCE_PRIORITY.get(raw_tbl, 9)

        base_ctx = {
            "doc_id":          doc_id,
            "raw_id":          doc.get("raw_id"),
            "raw_table":       raw_tbl,
            "person":          doc.get("person"),
            "source":          doc.get("source"),
            "category":        doc.get("category"),
            "source_priority": prio,
            "origin_stage":    "G18",
        }

        candidates: List[Dict[str, Any]] = []

        # ① カレンダーイベント（start_at あり）
        if doc.get("start_at"):
            candidates += self._from_calendar(doc, base_ctx, ui)

        # ② ui_data.timeline
        for item in ui.get("timeline", []):
            c = self._from_timeline_item(item, doc, base_ctx)
            if c:
                candidates.append(c)

        # ③ ui_data.actions（タスク）
        for item in ui.get("actions", []):
            c = self._from_action_item(item, doc, base_ctx)
            if c:
                candidates.append(c)

        # ④ ui_data.notices
        for item in ui.get("notices", []):
            c = self._from_notice_item(item, doc, base_ctx)
            if c:
                candidates.append(c)

        # ⑤ g21_articles
        for item in ui.get("g21_articles", []):
            c = self._from_article(item, doc, base_ctx)
            if c:
                candidates.append(c)

        # ⑥ g11_structured_tables → 時間割 / 課題表
        for tbl in ui.get("g11_structured_tables", []):
            candidates += self._from_structured_table(tbl, doc, base_ctx)

        # ⑦ due_date のみある文書（②〜⑥ で拾えなかった場合のフォールバック）
        if doc.get("due_date") and not any(
            c.get("record_type") == "task" for c in candidates
        ):
            c = self._from_due_date_doc(doc, base_ctx)
            if c:
                candidates.append(c)

        return candidates

    # ------------------------------------------------------------------
    # ① カレンダーイベント
    # ------------------------------------------------------------------
    def _from_calendar(
        self,
        doc: Dict,
        ctx: Dict,
        ui: Dict,
    ) -> List[Dict]:
        """start_at を持つカレンダーイベントを event レコードに変換"""
        start_dt = doc.get("start_at")
        d = _parse_date(start_dt)

        # timeline が既にあれば、base イベントは重複するので timeline 優先
        if ui.get("timeline"):
            return []

        return [{
            **ctx,
            "record_type":   "event",
            "title":         doc.get("title"),
            "summary":       doc.get("title"),
            "date_primary":  d.isoformat() if d else None,
            "date_start":    _safe_timestamptz(start_dt),
            "date_end":      _safe_timestamptz(doc.get("end_at")),
            "due_date":      None,
            "is_actionable": False,
            "report_priority": ctx["source_priority"],
            "origin_path":   "base_event",
            "details_json":  {
                "location": doc.get("location"),
                "body":     doc.get("body"),
            },
        }]

    # ------------------------------------------------------------------
    # ② timeline アイテム
    # ------------------------------------------------------------------
    def _from_timeline_item(self, item: Dict, doc: Dict, ctx: Dict) -> Optional[Dict]:
        """ui_data.timeline の1アイテム → event レコード"""
        raw_date = item.get("date") or item.get("normalized_date")
        raw_time = item.get("time") or item.get("normalized_time")
        d = _parse_date(raw_date)

        # date が取れない場合は doc の start_at から推測
        if d is None:
            d = _parse_date(doc.get("start_at"))

        title = item.get("event") or item.get("title") or doc.get("title") or ""
        if not title:
            return None

        # date_start を組み立て（HH:MM 形式の時刻のみ使用、"20:00～" 等は除外）
        date_start = None
        if d and raw_time and _TIME_RE.match(str(raw_time).strip()):
            date_start = f"{d.isoformat()}T{raw_time}:00+09:00"
        elif doc.get("start_at"):
            date_start = _safe_timestamptz(doc.get("start_at"))

        return {
            **ctx,
            "record_type":   "event",
            "title":         title,
            "summary":       title,
            "date_primary":  d.isoformat() if d else None,
            "date_start":    date_start,
            "date_end":      _safe_timestamptz(doc.get("end_at")),
            "due_date":      None,
            "is_actionable": False,
            "report_priority": ctx["source_priority"],
            "origin_path":   "timeline",
            "details_json":  {
                "location":    item.get("location"),
                "description": item.get("description"),
            },
        }

    # ------------------------------------------------------------------
    # ③ actions アイテム
    # ------------------------------------------------------------------
    def _from_action_item(self, item: Dict, doc: Dict, ctx: Dict) -> Optional[Dict]:
        """ui_data.actions の1アイテム → task レコード"""
        title = (
            item.get("item")
            or item.get("text")
            or item.get("title")
            or item.get("task")
            or ""
        )
        if not title:
            return None

        deadline_raw = item.get("deadline") or item.get("due_date")
        due = _parse_date(deadline_raw) or _parse_date(doc.get("due_date"))

        return {
            **ctx,
            "record_type":   "task",
            "title":         title,
            "summary":       title,
            "date_primary":  due.isoformat() if due else None,
            "date_start":    None,
            "date_end":      None,
            "due_date":      due.isoformat() if due else None,
            "is_actionable": True,
            "report_priority": max(1, ctx["source_priority"] - 1),  # タスクは1段階高優先
            "origin_path":   "actions",
            "details_json":  {
                "description": item.get("description"),
                "priority":    item.get("priority"),
                "doc_title":   doc.get("title"),
            },
        }

    # ------------------------------------------------------------------
    # ④ notices アイテム
    # ------------------------------------------------------------------
    def _from_notice_item(self, item: Dict, doc: Dict, ctx: Dict) -> Optional[Dict]:
        """ui_data.notices の1アイテム → notice レコード"""
        if isinstance(item, str):
            content = item
        else:
            content = item.get("content") or item.get("text") or item.get("item") or ""
        if not content:
            return None

        # notice の日付は文書の due_date か start_at を使う
        d = _parse_date(doc.get("due_date")) or _parse_date(doc.get("start_at"))

        return {
            **ctx,
            "record_type":   "notice",
            "title":         content[:200],
            "summary":       content[:200],
            "date_primary":  d.isoformat() if d else None,
            "date_start":    None,
            "date_end":      None,
            "due_date":      _parse_date(doc.get("due_date")).isoformat()
                             if _parse_date(doc.get("due_date")) else None,
            "is_actionable": False,
            "report_priority": ctx["source_priority"],
            "origin_path":   "notices",
            "details_json":  {
                "category":    item.get("category") if isinstance(item, dict) else None,
                "importance":  item.get("importance") if isinstance(item, dict) else None,
                "doc_title":   doc.get("title"),
            },
        }

    # ------------------------------------------------------------------
    # ⑤ g21_articles
    # ------------------------------------------------------------------
    def _from_article(self, item: Dict, doc: Dict, ctx: Dict) -> Optional[Dict]:
        """g21_articles の1アイテム → article_context レコード"""
        body = item.get("body") or item.get("content") or ""
        title = item.get("title") or doc.get("title") or ""
        if not title and not body:
            return None

        d = _parse_date(doc.get("due_date")) or _parse_date(doc.get("start_at"))

        return {
            **ctx,
            "record_type":   "article_context",
            "title":         title,
            "summary":       (body[:300] if body else title),
            "date_primary":  d.isoformat() if d else None,
            "date_start":    None,
            "date_end":      None,
            "due_date":      _parse_date(doc.get("due_date")).isoformat()
                             if _parse_date(doc.get("due_date")) else None,
            "is_actionable": False,
            "report_priority": ctx["source_priority"] + 2,  # 記事は低め
            "is_report_worthy": False,  # 補足情報なので通常非表示
            "origin_path":   "g21_articles",
            "details_json":  {"body": body[:1000]},
        }

    # ------------------------------------------------------------------
    # ⑥ g11_structured_tables
    # ------------------------------------------------------------------
    def _from_structured_table(
        self,
        tbl: Dict,
        doc: Dict,
        ctx: Dict,
    ) -> List[Dict]:
        """g11_structured_tables の1テーブル → 複数レコード"""
        semantics = (tbl.get("table_semantics") or "").lower()
        records   = tbl.get("records") or tbl.get("rows") or []
        result    = []

        for rec in records:
            if not isinstance(rec, dict):
                continue

            # 日付候補フィールドを探す
            d = None
            for key in ("date", "日付", "date_primary", "deadline", "期限", "due_date"):
                if key in rec:
                    d = _parse_date(rec[key])
                    if d:
                        break

            # タイトル候補フィールド
            title = ""
            for key in ("title", "subject", "科目", "内容", "item", "task", "授業", "行事"):
                if key in rec:
                    title = str(rec[key])
                    break
            if not title:
                title = str(list(rec.values())[0]) if rec else ""

            if any(kw in semantics for kw in TIMETABLE_SEMANTICS):
                rec_type = "timetable_slot"
                prio_adj = 0
            elif any(kw in semantics for kw in HOMEWORK_SEMANTICS):
                rec_type = "homework_item"
                prio_adj = -1
            else:
                rec_type = "schedule_item"
                prio_adj = 0

            result.append({
                **ctx,
                "record_type":   rec_type,
                "title":         title[:200],
                "summary":       title[:200],
                "date_primary":  d.isoformat() if d else None,
                "date_start":    None,
                "date_end":      None,
                "due_date":      d.isoformat() if d and rec_type == "homework_item" else None,
                "is_actionable": rec_type == "homework_item",
                "report_priority": max(1, ctx["source_priority"] + prio_adj),
                "origin_path":   f"g11_structured_tables/{semantics}",
                "details_json":  {"row": rec, "table_semantics": semantics},
            })

        return result

    # ------------------------------------------------------------------
    # ⑦ due_date フォールバック
    # ------------------------------------------------------------------
    def _from_due_date_doc(self, doc: Dict, ctx: Dict) -> Optional[Dict]:
        """actions が空で due_date だけある文書 → task レコード"""
        due = _parse_date(doc.get("due_date"))
        if not due:
            return None
        title = doc.get("title") or ""
        return {
            **ctx,
            "record_type":   "task",
            "title":         title,
            "summary":       title,
            "date_primary":  due.isoformat(),
            "date_start":    None,
            "date_end":      None,
            "due_date":      due.isoformat(),
            "is_actionable": True,
            "report_priority": max(1, ctx["source_priority"] - 1),
            "origin_path":   "due_date_fallback",
            "details_json":  {
                "post_type":  doc.get("post_type"),
                "doc_title":  title,
            },
        }

    # ------------------------------------------------------------------
    # バッチ処理用ヘルパー
    # ------------------------------------------------------------------

    def process_batch(
        self,
        docs: List[Dict[str, Any]],
        id_field: str = "id",
    ) -> Dict[str, Any]:
        """
        複数ドキュメントをまとめて処理。

        Args:
            docs:     09_unified_documents 行のリスト（'id' フィールド必須）
            id_field: ID フィールド名（デフォルト 'id'）

        Returns:
            {'success': bool, 'processed': int, 'total_inserted': int, 'errors': list}
        """
        processed = 0
        total_ins  = 0
        errors     = []

        for doc in docs:
            doc_id = str(doc.get(id_field) or "")
            if not doc_id:
                errors.append({"doc": doc, "error": "id なし"})
                continue
            res = self.process(doc_id, doc)
            if res.get("success"):
                processed  += 1
                total_ins  += res.get("inserted", 0)
            else:
                errors.append({"doc_id": doc_id, "error": res.get("error")})

        return {
            "success":        len(errors) == 0,
            "processed":      processed,
            "total_inserted": total_ins,
            "errors":         errors,
        }
