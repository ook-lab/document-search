"""
rag-prepare: 既存本文から 10_ix_search_index のみ更新し、
09_unified_documents_meta.ix_vectorized_at（ステータスのみ）を記録する。

pipeline_meta は読まない・更新しない。
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from standalone.db import RagServiceDB
from standalone.embeddings import EmbeddingGen
from standalone.date_signals import build_date_signals, build_ix_search_date_list
from standalone.scope import RAG_PREPARE_VECTORIZE_RAW_TABLES
from standalone.ud_meta import UD_META_TABLE

logger = logging.getLogger(__name__)

DRIVE_URL_RE = re.compile(r"/d/([a-zA-Z0-9_-]+)")

_UD_SELECT = (
    "id, raw_id, raw_table, person, classification1, classification2, classification3, title, file_url, "
    "post_at, start_at, end_at, due_date, snippet, from_name, from_email, location, post_type, ui_data, meta, "
    "body, ix_date_signals, ix_search_dates"
)


class RagPrepareSearchIndexer:

    # アノテーション型 → 行頭プレフィックス（section_break は別処理）
    _LINE_PREFIX: Dict[str, str] = {
        "heading_1": "# ",
        "heading_2": "## ",
        "bullet_item": "- ",
        "blockquote": "> ",
    }

    # 内部分割マーカー
    _SPLIT_MARKER = "\x00SPLIT\x00"

    def __init__(self) -> None:
        self.db = RagServiceDB()
        self.embedder: Optional[EmbeddingGen] = None

    def process_document(
        self,
        unified_doc_id: Optional[str] = None,
        *,
        raw_table: Optional[str] = None,
        raw_id: Optional[str] = None,
    ) -> tuple[bool, Optional[str]]:
        """OCR をスキップし、既存テキストから 10_ix_search_index のみ更新する。"""
        try:
            ud = self._resolve_or_create_unified_document(
                unified_doc_id=unified_doc_id, raw_table=raw_table, raw_id=raw_id
            )
            if not ud:
                return False, "09_unified_documents が見つからず、raw からも作成できません"
            rt = ud.get("raw_table") or ""
            if rt not in RAG_PREPARE_VECTORIZE_RAW_TABLES:
                logger.error("raw_table out of rag-prepare vectorize scope: %s", rt)
                return False, "この raw_table は rag-prepare の検索インデックス登録の対象外です"

            ud_raw = ud.get("raw_id")
            if ud_raw is None or (isinstance(ud_raw, str) and not ud_raw.strip()):
                logger.error("09.raw_id がありません: %s", unified_doc_id)
                return False, "raw_id がありません"
            ud_raw_id = str(ud_raw).strip()

            meta_res = (
                self.db.client.table(UD_META_TABLE)
                .select("ix_skip_pdf")
                .eq("raw_table", rt)
                .eq("raw_id", ud_raw_id)
                .maybe_single()
                .execute()
            )
            ix_skip_pdf = bool((meta_res.data or {}).get("ix_skip_pdf"))

            ctx = {
                "raw_id": ud_raw_id,
                "raw_table": rt,
                "file_url": ud.get("file_url"),
                "skip_pdf": ix_skip_pdf,
            }
            full_markdown, md_err = self._resolve_markdown(ctx)
            if not full_markdown:
                logger.error("インデックス用の本文がありません: %s", unified_doc_id)
                return False, md_err or "インデックス用の本文がありません（raw / pdf_md_content）"

            unified_doc_id = str(ud["id"])
            person = ud.get("person")
            c1 = ud.get("classification1")
            c2 = ud.get("classification2")
            c3 = ud.get("classification3") or rt

            raw_row = self._load_raw_row(rt, ud_raw_id)
            sync_updates = self._sync_09_from_raw_row(ud, raw_row, full_markdown)
            self.db.client.table("09_unified_documents").update(sync_updates).eq("id", unified_doc_id).select("id").execute()

            ud_fresh = (
                self.db.client.table("09_unified_documents")
                .select(_UD_SELECT)
                .eq("id", unified_doc_id)
                .single()
                .execute()
                .data
            )
            if not ud_fresh:
                return False, "09 を再読込できません"

            date_signals = build_date_signals(ud_fresh, merge_meta_date_signals=False, extra_text="")
            ix_dates = build_ix_search_date_list(ud_fresh, date_signals)
            self.db.client.table("09_unified_documents").update(
                {"ix_date_signals": date_signals, "ix_search_dates": ix_dates}
            ).eq("id", unified_doc_id).select("id").execute()

            person = ud_fresh.get("person") or person
            c1 = ud_fresh.get("classification1") or c1
            c2 = ud_fresh.get("classification2")
            c3 = ud_fresh.get("classification3") or c3

            chunk_items = self._md_chunks_with_meta(full_markdown)
            gate = (
                self.db.client.table("09_unified_documents")
                .select("id")
                .eq("id", unified_doc_id)
                .limit(1)
                .execute()
            )
            if not gate.data:
                return False, "09_unified_documents に行が無い doc_id では 10_ix_search_index を更新できません"

            self.db.client.table("10_ix_search_index").delete().eq("doc_id", unified_doc_id).execute()
            if self.embedder is None:
                self.embedder = EmbeddingGen()

            for i, (chunk_text, chunk_type, chunk_weight) in enumerate(chunk_items):
                chunk_text = (chunk_text or "").replace("\u0000", "").strip()
                if not chunk_text:
                    continue
                vector = self.embedder.generate_embedding(chunk_text)
                self.db.client.table("10_ix_search_index").insert(
                    {
                        "doc_id": unified_doc_id,
                        "person": person,
                        "classification1": c1,
                        "classification2": c2,
                        "classification3": c3,
                        "chunk_index": i,
                        "chunk_text": chunk_text,
                        "chunk_type": chunk_type,
                        "chunk_weight": chunk_weight,
                        "embedding": vector,
                    }
                ).execute()

            now_iso = datetime.now(timezone.utc).isoformat()
            self._write_meta_vectorized(
                raw_table=rt,
                raw_id=ud_raw_id,
                doc_id=unified_doc_id,
                now_iso=now_iso,
            )

            logger.info("Successfully updated search index unified_doc_id=%s", unified_doc_id)
            return True, None

        except Exception as e:
            logger.error("Search index update failed: %s", e, exc_info=True)
            return False, str(e)

    def process_date_signals_for_document(
        self,
        unified_doc_id: Optional[str] = None,
        *,
        raw_table: Optional[str] = None,
        raw_id: Optional[str] = None,
    ) -> tuple[bool, Optional[str]]:
        """09 に raw 由来を同期したうえで ix_date_signals のみ更新（ベクトルは触らない）。"""
        try:
            ud = self._resolve_or_create_unified_document(
                unified_doc_id=unified_doc_id, raw_table=raw_table, raw_id=raw_id
            )
            if not ud:
                return False, "09_unified_documents が見つかりません"
            rt = ud.get("raw_table") or ""
            rid = ud.get("raw_id")
            if not rt or rid is None or not str(rid).strip():
                return False, "raw_table / raw_id がありません"
            rid_s = str(rid).strip()
            ctx = {"raw_id": rid_s, "raw_table": rt, "file_url": ud.get("file_url")}
            md, md_err = self._resolve_markdown(ctx)
            raw_row = self._load_raw_row(rt, rid_s)
            body_existing = str(ud.get("body") or "").strip()
            full_md = (md or "").strip() or body_existing
            if not full_md:
                return False, md_err or "本文がありません（raw / 09.body）"

            sync_updates = self._sync_09_from_raw_row(ud, raw_row, full_md)
            self.db.client.table("09_unified_documents").update(sync_updates).eq("id", ud["id"]).select("id").execute()

            ud_fresh = (
                self.db.client.table("09_unified_documents")
                .select(_UD_SELECT)
                .eq("id", ud["id"])
                .single()
                .execute()
                .data
            )
            if not ud_fresh:
                return False, "09 を再読込できません"
            ds = build_date_signals(ud_fresh, merge_meta_date_signals=False, extra_text="")
            ix_dates = build_ix_search_date_list(ud_fresh, ds)
            self.db.client.table("09_unified_documents").update(
                {"ix_date_signals": ds, "ix_search_dates": ix_dates}
            ).eq("id", ud["id"]).select("id").execute()
            return True, None
        except Exception as e:
            logger.error("date_signals update failed: %s", e, exc_info=True)
            return False, str(e)

    def skip_document(self, *, raw_table: str, raw_id: str) -> tuple[bool, Optional[str]]:
        """ix_skip_pdf=True をセットするだけ。PDF コンテンツを text_only 扱いにする。"""
        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            self.db.client.table(UD_META_TABLE).update(
                {"ix_skip_pdf": True, "updated_at": now_iso}
            ).eq("raw_table", raw_table).eq("raw_id", raw_id).select("raw_id").execute()
            logger.info("skip_document: ix_skip_pdf set for %s/%s", raw_table, raw_id)
            return True, None
        except Exception as e:
            logger.error("skip_document failed: %s", e, exc_info=True)
            return False, str(e)

    def reset_all_ix_vectorized_at(self, raw_tables: List[str]) -> Dict[str, Any]:
        """ix_vectorized_at を NULL に戻し、全行を未処理状態に戻す。10_ix_search_index は触らない（再登録時に上書きされる）。"""
        if not raw_tables:
            return {"success": False, "error": "raw_tables が空です"}
        try:
            res = (
                self.db.client.table(UD_META_TABLE)
                .update({"ix_vectorized_at": None})
                .in_("raw_table", raw_tables)
                .select("raw_id")
                .execute()
            )
            count = len(res.data or [])
            return {"success": True, "reset_count": count}
        except Exception as e:
            logger.error("reset_all_ix_vectorized_at failed: %s", e, exc_info=True)
            return {"success": False, "error": str(e)}

    def backfill_date_signals(
        self,
        *,
        limit: int = 200,
        person: Optional[str] = None,
        source: Optional[str] = None,
        force: bool = False,
    ) -> Dict[str, Any]:
        """
        既存 09 行に date_signals を付与（インデックス更新はしない）。
        """
        updated = 0
        skipped = 0
        errors: List[str] = []
        offset = 0
        page = min(max(limit, 1), 1000)
        while updated + skipped < limit:
            q = (
                self.db.client.table("09_unified_documents")
                .select(_UD_SELECT)
                .range(offset, offset + page - 1)
            )
            if person:
                q = q.eq("person", person)
            if source:
                q = q.eq("source", source)
            try:
                res = q.execute()
            except Exception as e:
                errors.append(f"fetch failed: {e}")
                break
            rows = res.data or []
            if not rows:
                break
            for row in rows:
                if updated + skipped >= limit:
                    break
                try:
                    ix_d = row.get("ix_search_dates") or []
                    if not force and isinstance(ix_d, list) and len(ix_d) > 0:
                        skipped += 1
                        continue
                    rt = row.get("raw_table") or ""
                    rid = row.get("raw_id")
                    extra = ""
                    if rt and rid is not None and str(rid).strip():
                        ctx = {
                            "raw_id": str(rid).strip(),
                            "raw_table": rt,
                            "file_url": row.get("file_url"),
                        }
                        md, _ = self._resolve_markdown(ctx)
                        extra = (md or "").strip()
                    body = str(row.get("body") or "").strip()
                    text_for_dates = body or extra
                    if not text_for_dates:
                        errors.append(f"id={row.get('id')}: 本文なし")
                        continue
                    row_for = dict(row)
                    row_for["body"] = text_for_dates
                    ds = build_date_signals(row_for, merge_meta_date_signals=False, extra_text="")
                    ix_dates = build_ix_search_date_list(row_for, ds)
                    self.db.client.table("09_unified_documents").update(
                        {"ix_date_signals": ds, "ix_search_dates": ix_dates}
                    ).eq("id", row["id"]).select("id").execute()
                    updated += 1
                except Exception as e:
                    errors.append(f"id={row.get('id')}: {e}")
            offset += page
            if len(rows) < page:
                break
        return {"success": len(errors) == 0, "updated": updated, "skipped": skipped, "errors": errors}

    @staticmethod
    def _sync_09_from_raw_row(ud: Dict[str, Any], raw_row: Dict[str, Any], full_markdown: str) -> Dict[str, Any]:
        """raw 行の分かる範囲で 09 の列を上書きし、統合 MD を body に載せる（meta は触らない）。"""
        updates: Dict[str, Any] = {"body": full_markdown}
        if not raw_row:
            return updates

        def _set_str(key: str, value: Any) -> None:
            if value is None:
                return
            s = str(value).strip()
            if s:
                updates[key] = value if not isinstance(value, str) else s

        for key in (
            "person",
            "file_url",
            "snippet",
            "from_name",
            "from_email",
            "location",
            "post_type",
        ):
            _set_str(key, raw_row.get(key))

        rt = ud.get("raw_table") or ""
        _set_str("classification1", raw_row.get("source"))
        if rt in (
            "03_ema_classroom_01_raw",
            "04_ikuya_classroom_01_raw",
            "05_ikuya_waseaca_01_raw",
        ):
            _set_str("classification2", raw_row.get("course_name"))
            _set_str("classification3", raw_row.get("category"))
        else:
            _set_str("classification3", raw_row.get("category"))

        tit = raw_row.get("title") or raw_row.get("file_name")
        _set_str("title", tit)

        if raw_row.get("created_at") is not None:
            updates["post_at"] = raw_row["created_at"]
        for key in ("due_date", "start_at", "end_at"):
            if raw_row.get(key) is not None:
                updates[key] = raw_row[key]

        return updates

    def _write_meta_vectorized(
        self, *, raw_table: str, raw_id: str, doc_id: str, now_iso: str
    ) -> None:
        """09_unified_documents_meta を raw 主キーで更新。upsert の列落ちを避け update→insert にする。"""
        upd_cols = {
            "doc_id": doc_id,
            "ix_vectorized_at": now_iso,
            "updated_at": now_iso,
        }
        upd = (
            self.db.client.table(UD_META_TABLE)
            .update(upd_cols)
            .eq("raw_table", raw_table)
            .eq("raw_id", raw_id)
            .select("raw_id")
            .execute()
        )
        if upd.data:
            return
        self.db.client.table(UD_META_TABLE).insert(
            {
                "raw_table": raw_table,
                "raw_id": raw_id,
                "doc_id": doc_id,
                "ix_vectorized_at": now_iso,
                "updated_at": now_iso,
            }
        ).select("raw_id").execute()

    def _resolve_or_create_unified_document(
        self,
        *,
        unified_doc_id: Optional[str],
        raw_table: Optional[str],
        raw_id: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        if unified_doc_id:
            res = (
                self.db.client.table("09_unified_documents")
                .select(_UD_SELECT)
                .eq("id", unified_doc_id)
                .single()
                .execute()
            )
            if res.data:
                return res.data
        if raw_table and raw_id:
            res2 = (
                self.db.client.table("09_unified_documents")
                .select(_UD_SELECT)
                .eq("raw_table", raw_table)
                .eq("raw_id", raw_id)
                .limit(1)
                .execute()
            )
            if res2.data:
                return res2.data[0]
            return self._create_unified_from_raw(raw_table, raw_id)
        return None

    def _create_unified_from_raw(self, raw_table: str, raw_id: str) -> Optional[Dict[str, Any]]:
        if raw_table not in RAG_PREPARE_VECTORIZE_RAW_TABLES:
            return None
        raw_row = self._load_raw_row(raw_table, raw_id)
        if not raw_row:
            return None
        doc = {
            "id": str(raw_id),
            "raw_id": str(raw_id),
            "raw_table": raw_table,
            "person": raw_row.get("person"),
            "classification1": raw_row.get("source"),
            "classification2": (
                raw_row.get("course_name")
                if raw_table
                in (
                    "03_ema_classroom_01_raw",
                    "04_ikuya_classroom_01_raw",
                    "05_ikuya_waseaca_01_raw",
                )
                else None
            ),
            "classification3": raw_row.get("category"),
            "title": raw_row.get("title") or raw_row.get("file_name"),
            "file_url": raw_row.get("file_url"),
            "post_at": raw_row.get("created_at"),
            "due_date": raw_row.get("due_date"),
            "post_type": raw_row.get("post_type"),
            "ui_data": {},
            "meta": {"created_by": "rag_prepare_search_index"},
        }
        ins = self.db.client.table("09_unified_documents").insert(doc).execute()
        rows = ins.data or []
        return rows[0] if rows else None

    def _load_raw_row(self, raw_table: str, raw_id: Any) -> Dict[str, Any]:
        try:
            return (
                self.db.client.table(raw_table)
                .select("*")
                .eq("id", raw_id)
                .single()
                .execute()
                .data
                or {}
            )
        except Exception as e:
            logger.warning("raw row load failed: table=%s id=%s error=%s", raw_table, raw_id, e)
            return {}

    def _drive_id_from_ctx(self, ctx: Dict[str, Any]) -> Optional[str]:
        fu = ctx.get("file_url")
        if fu:
            m = DRIVE_URL_RE.search(str(fu))
            if m:
                return m.group(1)
        raw_table = ctx.get("raw_table")
        raw_id = ctx.get("raw_id")
        if not raw_table or not raw_id:
            return None
        try:
            raw = (
                self.db.client.table(raw_table)
                .select("file_url")
                .eq("id", raw_id)
                .single()
                .execute()
                .data
            )
        except Exception:
            return None
        if not raw:
            return None
        fu2 = raw.get("file_url")
        if not fu2:
            return None
        m = DRIVE_URL_RE.search(str(fu2))
        return m.group(1) if m else None

    def _resolve_markdown(self, ctx: Dict[str, Any]) -> tuple[str, Optional[str]]:
        raw_table = ctx.get("raw_table")
        raw_id = ctx.get("raw_id")
        if raw_table and raw_id:
            raw_row = self._load_raw_row(str(raw_table), raw_id)
            sections: List[str] = []

            # Stage F ではファイル外テキストを本文に混ぜない。検索データ準備で raw メタと PDF MD を統合する。
            external = self._raw_external_markdown(raw_row)
            if external:
                sections.append("# ファイル外テキスト\n\n" + external)

            pdf_md = (raw_row.get("pdf_md_content") or "").strip()
            if pdf_md and not ctx.get("skip_pdf"):
                sections.append("# PDF抽出Markdown\n\n" + pdf_md)

            if sections:
                return "\n\n".join(sections).strip(), None

        drive_id = self._drive_id_from_ctx(ctx)
        if drive_id:
            msg = (
                "PDF（Drive）由来の本文はこのサービスでは未対応です。"
                " raw.pdf_md_content を設定するか、別経路でテキスト化してください。"
            )
            logger.error("%s drive_id=%s", msg, drive_id)
            return "", msg
        return "", None

    @staticmethod
    def _raw_external_markdown(raw: Dict[str, Any]) -> str:
        fields = [
            ("person", "対象者"),
            ("source", "ソース"),
            ("category", "カテゴリ"),
            ("course_name", "コース名"),
            ("topic_name", "トピック"),
            ("title", "タイトル"),
            ("description", "説明"),
            ("post_type", "投稿種別"),
            ("due_date", "期限日"),
            ("due_time", "期限時刻"),
            ("creator_name", "作成者"),
            ("source_url", "投稿URL"),
            ("file_name", "ファイル名"),
            ("file_url", "ファイルURL"),
            ("original_path", "元パス"),
            ("mime_type", "MIMEタイプ"),
        ]
        lines = []
        for key, label in fields:
            value = raw.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                lines.append(f"- {label}: {text}")
        return "\n".join(lines)

    @staticmethod
    def _get_ai_annotations(text: str) -> Dict[str, Any]:
        """
        Gemini にテキスト構造のアノテーション指示を JSON で返させる。
        AI はテキストを生成・変更しない。行番号とスパン文字列のみ返す。

        戻り値: {"annotation_types": {型名: 説明, ...}, "annotations": [...]}
        annotation_types は AI がこの文書に合わせて 5〜8 種類自由に定義する。
        """
        if not text.strip():
            return {"annotation_types": {}, "annotations": []}
        try:
            import json as _json
            import os
            import google.generativeai as genai
            api_key = os.environ.get("GOOGLE_AI_API_KEY", "")
            if not api_key:
                return {"annotation_types": {}, "annotations": []}

            lines = text.split("\n")
            numbered = "\n".join(f"{i}: {line}" for i, line in enumerate(lines))

            prompt = (
                "次のテキストを読んで、読者にとって重要な情報の種類を自分で決め、"
                "アノテーション指示を JSON のみで返してください。\n"
                "【絶対ルール】JSON 以外は一切出力しない。テキストを生成・変更しない。\n\n"
                "annotation_types: このドキュメントに合ったスパン型を 5〜8 個定義する。\n"
                "  型名は英小文字スネークケース（date, place, item, person, deadline 等）\n"
                "  説明は日本語（例: place: 集合場所・解散場所・目的地の固有名詞）\n"
                "  【禁止①】文体・修辞・文学的技法を説明する型は定義しない"
                "（literary_device, metaphor, rhetorical_question 等）。\n"
                "  【禁止②】語自体がすでに自分のカテゴリを説明している抽象名詞をタグ対象にしない。"
                "「気持ち」に[EMOTION]、「歌い方」に[METHOD]を付けるのは翻訳であり意味がない。\n"
                "  タグは読者が『スキャンして探したい』具体的な固有情報"
                "（日時・場所・人名・物品名・金額・締切など）にのみ付ける。\n\n"
                "行レベル（line キー）の固定型: "
                "heading_1（最重要見出し）, heading_2（セクション見出し）, "
                "section_break（話題の切れ目）, bullet_item（箇条書き項目）, blockquote（注記・引用）\n"
                "スパンレベル（span キー）: annotation_types で定義した型のみ使用\n\n"
                "section_break の制約: 文書全体で最大 5 箇所まで。"
                "段落ごとに区切らず、読み手が「ここから別の話題だ」と感じる大きな転換点のみに付ける。\n\n"
                "出力形式（例）:\n"
                '{"annotation_types": {"date": "日付・時刻・期限", "place": "集合・解散場所"}, '
                '"annotations": [{"line": 0, "type": "heading_1"}, '
                '{"span": "来週金曜日", "type": "date"}, '
                '{"span": "こどもの国", "type": "place"}]}\n\n'
                "span の値は下記テキストに存在する文字列のみ。存在しない文字列は絶対に使わない。\n\n"
                f"テキスト:\n{numbered}"
            )

            genai.configure(api_key=api_key)
            model = genai.GenerativeModel("gemini-2.5-flash-lite")
            resp = model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.0,
                    response_mime_type="application/json",
                ),
            )
            data = _json.loads(resp.text.strip())
            return {
                "annotation_types": data.get("annotation_types") or {},
                "annotations": data.get("annotations") or [],
            }
        except Exception as e:
            logger.warning("[RAG] AI アノテーション取得失敗: %s", e)
            return {"annotation_types": {}, "annotations": []}

    @staticmethod
    def _apply_annotations(md: str, annotations: List[Dict[str, Any]]) -> str:
        """
        AI アノテーション指示を md テキストに機械的に適用する。
        元テキストの文字は変えない。行頭プレフィックスとスパンタグのみ挿入。
        スパン型は [TYPE_NAME]...[/TYPE_NAME] に統一（型名は大文字化）。
        section_break は _SPLIT_MARKER を挿入（呼び出し元が --- に変換）。
        """
        if not annotations:
            return md

        lines = md.split("\n")
        marker = RagPrepareSearchIndexer._SPLIT_MARKER

        for ann in annotations:
            if "line" in ann:
                idx = ann.get("line")
                ann_type = ann.get("type", "")
                if not isinstance(idx, int) or idx < 0 or idx >= len(lines):
                    continue
                if ann_type == "section_break":
                    if not lines[idx].startswith(marker) and \
                       not lines[idx].startswith("# ") and \
                       not lines[idx].startswith("## "):
                        lines[idx] = marker + lines[idx]
                    continue
                prefix = RagPrepareSearchIndexer._LINE_PREFIX.get(ann_type)
                if prefix is None:
                    continue
                line = lines[idx]
                if line.startswith("# ") and ann_type in ("heading_1", "heading_2"):
                    continue
                if line.startswith("## ") and ann_type == "heading_2":
                    continue
                if line.startswith(prefix):
                    continue
                lines[idx] = prefix + line

        result = "\n".join(lines)

        for ann in annotations:
            if "span" in ann:
                span_text = ann.get("span", "")
                ann_type = ann.get("type", "").upper()
                if not ann_type or not span_text or span_text not in result:
                    continue
                open_tag = f"[{ann_type}]"
                close_tag = f"[/{ann_type}]"
                if f"{open_tag}{span_text}{close_tag}" in result:
                    continue
                result = result.replace(span_text, f"{open_tag}{span_text}{close_tag}", 1)

        return result

    @staticmethod
    def _plain_chunks(text: str, chunk_size: int = 1200) -> List[str]:
        t = text.strip()
        if not t:
            return []
        return [t[i : i + chunk_size] for i in range(0, len(t), chunk_size)]

    @staticmethod
    def _structured_md_chunks(md_text: str, prose_chunk_size: int = 800) -> List[Dict[str, Any]]:
        """
        構造化MD（## 非表（F 地の文）/ ## 表（埋め込み）形式）をチャンク化する。

        - 地の文: 段落単位でマージし prose_chunk_size 以内に収める
        - 表: YAML ブロック内の各テーブルエントリを1チャンク（絶対に分割しない）
        - ## 表（ui_data.tables）MD表 と HTML: スキップ（YAML と重複）

        Returns: list of {"text", "chunk_type", "chunk_weight"}
        """
        results: List[Dict[str, Any]] = []

        # 地の文: 旧フォーマット（## 非表）または新フォーマット（## 表（埋め込み）より前のテキスト）
        prose_text = ""
        prose_m = re.search(
            r'^## 非表（F 地の文）\s*\n(.*?)(?=^## |\Z)',
            md_text, re.MULTILINE | re.DOTALL,
        )
        if prose_m:
            prose_text = prose_m.group(1).strip()
        else:
            embed_idx = md_text.find('\n## 表（埋め込み）')
            if embed_idx >= 0:
                candidate = md_text[:embed_idx]
                # ::title:: / ::summary:: / ## heading を除去してプレーンテキスト化
                candidate = re.sub(r'^::title::.*$', '', candidate, flags=re.MULTILINE)
                candidate = re.sub(r'^::summary::.*$', '', candidate, flags=re.MULTILINE)
                candidate = re.sub(r'^## .+$', '', candidate, flags=re.MULTILINE)
                prose_text = re.sub(r'\n{3,}', '\n\n', candidate).strip()

        # 表タイトル・サマリーを抽出（チャンクのプレフィックスに付加）
        title_m = re.search(r'^## (.+?)$', md_text, re.MULTILINE)
        table_title = title_m.group(1).strip() if title_m else ''
        summary_m = re.search(r'^::summary::\s*(.+)$', md_text, re.MULTILINE)
        table_summary = summary_m.group(1).strip() if summary_m else ''

        # 地の文チャンク化（見出し検出でトピック単位に分割）
        if prose_text:
            paragraphs = [p.strip() for p in re.split(r'\n{2,}', prose_text) if p.strip()]
            # --- で明示的に分割（パイプライン側で埋め込み済み）
            if '---' in prose_text:
                raw_blocks = [b.strip() for b in prose_text.split('\n---\n') if b.strip()]
            else:
                raw_blocks = [prose_text]
            sections: List[List[str]] = []
            for block in raw_blocks:
                block_paras = [p.strip() for p in re.split(r'\n{2,}', block) if p.strip()]
                if block_paras:
                    sections.append(block_paras)
            # セクションごとにチャンク化（セクションタイトルを後続チャンクにもプレフィックスとして付加）
            for sec_paras in sections:
                sec_title = (
                    sec_paras[0]
                    if sec_paras and len(sec_paras[0]) <= 50 and '\n' not in sec_paras[0]
                    else ''
                )
                current: List[str] = []
                current_len = 0
                for para in sec_paras:
                    if current_len + len(para) > prose_chunk_size and current:
                        text = "\n\n".join(current)
                        if sec_title and not text.startswith(sec_title):
                            text = sec_title + "\n\n" + text
                        results.append({"text": text, "chunk_type": "prose", "chunk_weight": 1.0})
                        current = [para]
                        current_len = len(para)
                    else:
                        current.append(para)
                        current_len += len(para)
                if current:
                    text = "\n\n".join(current)
                    if sec_title and not text.startswith(sec_title):
                        text = sec_title + "\n\n" + text
                    results.append({"text": text, "chunk_type": "prose", "chunk_weight": 1.0})

        # YAML テーブル（## 表（埋め込み）内の ```yaml ブロック）
        yaml_m = re.search(r'```yaml\s*\n(.*?)```', md_text, re.DOTALL)
        if yaml_m:
            yaml_text = yaml_m.group(1).strip()
            # タイトル・サマリーをコンテキストプレフィックスとして構成
            ctx_parts = [p for p in [table_title, table_summary] if p]
            if not ctx_parts and prose_text:
                ctx_parts = [prose_text[:300].strip()]
            context_prefix = '\n'.join(ctx_parts)
            for block in re.split(r'(?=^- table_id:)', yaml_text, flags=re.MULTILINE):
                block = block.strip()
                if not block or not block.startswith('- table_id:'):
                    continue
                desc_m = re.search(r"^\s*description:\s*'(.*?)'", block, re.MULTILINE)
                desc = (desc_m.group(1).strip() if desc_m else '')
                prefix = context_prefix or desc or prose_text[:300].strip()
                text = f"{prefix}\n\n{block}" if prefix else block
                results.append({"text": text, "chunk_type": "table_yaml", "chunk_weight": 2.0})

        return results

    @staticmethod
    def _md_chunks_with_meta(
        full_markdown: str,
        plain_chunk_size: int = 1200,
        prose_chunk_size: int = 800,
    ) -> List[tuple[str, str, float]]:
        """
        full_markdown をチャンク化し (text, chunk_type, chunk_weight) のリストで返す。

        # PDF抽出Markdown セクションに構造化MD（## 非表 / ## 表（埋め込み））が含まれる場合は
        意味単位で分割。それ以外は固定サイズ分割。
        """
        results: List[tuple[str, str, float]] = []

        pdf_md_m = re.search(
            r'^# PDF抽出Markdown\s*\n(.*?)(?=^# |\Z)',
            full_markdown, re.MULTILINE | re.DOTALL,
        )
        if pdf_md_m:
            pdf_md = pdf_md_m.group(1)
            is_structured = bool(
                re.search(r'^## 非表（F 地の文）', pdf_md, re.MULTILINE)
                or re.search(r'^## 表（埋め込み）', pdf_md, re.MULTILINE)
            )
            if is_structured:
                for item in RagPrepareSearchIndexer._structured_md_chunks(pdf_md, prose_chunk_size):
                    results.append((item["text"], item["chunk_type"], item["chunk_weight"]))
            else:
                for c in RagPrepareSearchIndexer._plain_chunks(pdf_md, plain_chunk_size):
                    results.append((c, "rag_prepare_plain", 1.0))

            ext_m = re.search(
                r'^# ファイル外テキスト\s*\n(.*?)(?=^# |\Z)',
                full_markdown, re.MULTILINE | re.DOTALL,
            )
            if ext_m:
                ext_text = ext_m.group(1).strip()
                if ext_text:
                    annotations = RagPrepareSearchIndexer._get_ai_annotations(ext_text)
                    annotated = RagPrepareSearchIndexer._apply_annotations(ext_text, annotations)
                    # section_break マーカーを _structured_md_chunks が認識する --- に変換
                    annotated = annotated.replace(
                        RagPrepareSearchIndexer._SPLIT_MARKER, "\n---\n"
                    ).strip()
                    wrapped = "## 非表（F 地の文）\n\n" + annotated
                    for item in RagPrepareSearchIndexer._structured_md_chunks(wrapped, prose_chunk_size):
                        results.append((item["text"], item["chunk_type"], item["chunk_weight"]))
        else:
            for c in RagPrepareSearchIndexer._plain_chunks(full_markdown, plain_chunk_size):
                results.append((c, "rag_prepare_plain", 1.0))

        return results
