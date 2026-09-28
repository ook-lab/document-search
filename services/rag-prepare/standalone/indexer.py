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

from typing_extensions import TypedDict

import yaml

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


class _AiAnnotationItem(TypedDict):
    line: int
    type: str


class _AiAnnotationsResponse(TypedDict):
    annotations: List[_AiAnnotationItem]


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

            unified_doc_id = str(ud["id"])
            raw_row = self._load_raw_row(rt, ud_raw_id)
            ctx = {
                "raw_id": ud_raw_id,
                "raw_table": rt,
                "file_url": raw_row.get("file_url"),
                "skip_pdf": ix_skip_pdf,
            }
            full_markdown, md_err = self._resolve_markdown(ctx)
            if not full_markdown:
                logger.error("インデックス用の本文がありません: %s", unified_doc_id)
                return False, md_err or "インデックス用の本文がありません（raw / pdf_md_content）"

            person = ud.get("person")
            c1 = ud.get("classification1")
            c2 = ud.get("classification2")
            c3 = ud.get("classification3")

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

            date_signals = build_date_signals(ud_fresh, extra_text="")
            ix_dates = build_ix_search_date_list(ud_fresh, date_signals)
            self.db.client.table("09_unified_documents").update(
                {"ix_date_signals": date_signals, "ix_search_dates": ix_dates}
            ).eq("id", unified_doc_id).select("id").execute()

            person = ud_fresh.get("person")
            c1 = ud_fresh.get("classification1")
            c2 = ud_fresh.get("classification2")
            c3 = ud_fresh.get("classification3")

            raw_file_name = (raw_row.get("file_name") or "").strip() or None
            chunk_items = self._md_chunks_with_meta(full_markdown, file_name=raw_file_name)
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

            now_iso = datetime.now(timezone.utc).isoformat()
            inserted_count = 0
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
                        "embedding_v2": vector,
                        "embedding_v2_at": now_iso,
                    }
                ).execute()
                inserted_count += 1

            if inserted_count == 0:
                logger.error("chunk_count=0: unified_doc_id=%s", unified_doc_id)
                return False, "インデックスに登録できるテキストがありませんでした（チャンク0件）"

            now_iso = datetime.now(timezone.utc).isoformat()
            self._write_meta_vectorized(
                raw_table=rt,
                raw_id=ud_raw_id,
                doc_id=unified_doc_id,
                now_iso=now_iso,
            )

            logger.info("Successfully updated search index unified_doc_id=%s chunks=%d", unified_doc_id, inserted_count)
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
            raw_row = self._load_raw_row(rt, rid_s)
            ctx = {
                "raw_id": rid_s,
                "raw_table": rt,
                "file_url": raw_row.get("file_url"),
            }
            md, md_err = self._resolve_markdown(ctx)
            if not md or not md.strip():
                return False, md_err or "本文がありません（raw / pdf_md_content）"
            full_md = md.strip()

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
            ds = build_date_signals(ud_fresh, extra_text="")
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
                        raw_row = self._load_raw_row(rt, str(rid).strip())
                        ctx = {
                            "raw_id": str(rid).strip(),
                            "raw_table": rt,
                            "file_url": raw_row.get("file_url"),
                        }
                        md, _ = self._resolve_markdown(ctx)
                        extra = (md or "").strip()
                    body = str(row.get("body") or "").strip()
                    if not body:
                        errors.append(f"id={row.get('id')}: body欠損のため日付抽出をスキップしました")
                        continue
                    row_for = dict(row)
                    row_for["body"] = body
                    ds = build_date_signals(row_for, extra_text="")
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

        rt = (ud.get("raw_table") or "").strip()
        if not rt:
            raise ValueError(f"raw_table が未設定です (doc_id={ud.get('id')})")
        _set_str("classification1", raw_row.get("source"))
        # クラスルーム行(03/04): classification2=コース名, classification3=NULL（投稿種別は不要）
        # 05_ikuya_waseaca_01_raw: classification2=コース名, classification3=カテゴリ（従来どおり）
        # それ以外: classification2=NULL, classification3=カテゴリ
        _CLASSROOM_RAW_TABLES = (
            "03_ema_classroom_01_raw",
            "04_ikuya_classroom_01_raw",
        )
        if rt in _CLASSROOM_RAW_TABLES:
            _set_str("classification2", raw_row.get("course_name"))
            # classification3 は明示的に NULL にする（投稿種別を入れない）
            updates["classification3"] = None
        elif rt == "05_ikuya_waseaca_01_raw":
            _set_str("classification2", raw_row.get("course_name"))
            _set_str("classification3", raw_row.get("category"))
        else:
            _set_str("classification3", raw_row.get("category"))

        tit = raw_row.get("title")
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
            "ix_vectorize_error": None,
            "ix_vectorize_error_at": None,
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
                "ix_vectorize_error": None,
                "ix_vectorize_error_at": None,
            }
        ).select("raw_id").execute()

    def record_vectorize_error(
        self,
        *,
        raw_table: Optional[str],
        raw_id: Optional[str],
        error_message: str,
        doc_id: Optional[str] = None,
    ) -> tuple[bool, Optional[str]]:
        """09_unified_documents_meta にベクトル化失敗情報 (ix_vectorize_error, ix_vectorize_error_at) を記録する。"""
        if not raw_table or not raw_id:
            if doc_id:
                try:
                    ud_row = (
                        self.db.client.table("09_unified_documents")
                        .select("raw_table, raw_id")
                        .eq("id", str(doc_id))
                        .maybe_single()
                        .execute()
                    )
                    if ud_row.data:
                        raw_table = ud_row.data.get("raw_table")
                        raw_id = ud_row.data.get("raw_id")
                except Exception as e:
                    return False, f"09_unified_documents から raw_table/raw_id を解決できませんでした: {e}"
        if not raw_table or not raw_id:
            return False, "raw_table と raw_id が不明なためエラーを記録できません"

        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            upd_cols: Dict[str, Any] = {
                "ix_vectorize_error": str(error_message),
                "ix_vectorize_error_at": now_iso,
                "updated_at": now_iso,
            }
            upd = (
                self.db.client.table(UD_META_TABLE)
                .update(upd_cols)
                .eq("raw_table", str(raw_table))
                .eq("raw_id", str(raw_id))
                .select("raw_id")
                .execute()
            )
            if upd.data:
                return True, None

            ins_cols: Dict[str, Any] = {
                "raw_table": str(raw_table),
                "raw_id": str(raw_id),
                "ix_vectorize_error": str(error_message),
                "ix_vectorize_error_at": now_iso,
                "updated_at": now_iso,
            }
            self.db.client.table(UD_META_TABLE).insert(ins_cols).select("raw_id").execute()
            return True, None
        except Exception as e:
            logger.error("record_vectorize_error failed: %s", e, exc_info=True)
            return False, str(e)

    def clear_vectorize_error(
        self,
        *,
        raw_table: Optional[str],
        raw_id: Optional[str],
        doc_id: Optional[str] = None,
    ) -> tuple[bool, Optional[str]]:
        """09_unified_documents_meta の ix_vectorize_error, ix_vectorize_error_at をクリアする。"""
        if not raw_table or not raw_id:
            if doc_id:
                try:
                    ud_row = (
                        self.db.client.table("09_unified_documents")
                        .select("raw_table, raw_id")
                        .eq("id", str(doc_id))
                        .maybe_single()
                        .execute()
                    )
                    if ud_row.data:
                        raw_table = ud_row.data.get("raw_table")
                        raw_id = ud_row.data.get("raw_id")
                except Exception as e:
                    return False, f"09_unified_documents から raw_table/raw_id を解決できませんでした: {e}"
        if not raw_table or not raw_id:
            return False, "raw_table と raw_id が不明なためエラーをクリアできません"

        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            self.db.client.table(UD_META_TABLE).update({
                "ix_vectorize_error": None,
                "ix_vectorize_error_at": None,
                "updated_at": now_iso,
            }).eq("raw_table", str(raw_table)).eq("raw_id", str(raw_id)).execute()
            return True, None
        except Exception as e:
            logger.error("clear_vectorize_error failed: %s", e, exc_info=True)
            return False, str(e)

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
                .maybe_single()
                .execute()
            )
            if res.data:
                return res.data
            logger.error("指定された unified_doc_id (%s) が 09_unified_documents に存在しません", unified_doc_id)
            return None
        if raw_table and raw_id:
            res2 = (
                self.db.client.table("09_unified_documents")
                .select(_UD_SELECT)
                .eq("raw_table", raw_table)
                .eq("raw_id", raw_id)
                .limit(2)
                .execute()
            )
            rows = res2.data or []
            if len(rows) > 1:
                dup_ids = ", ".join(str(r.get("id")) for r in rows)
                raise RuntimeError(
                    f"09_unified_documents に同じ (raw_table, raw_id) のレコードが複数存在します（重複: {dup_ids}）"
                )
            if len(rows) == 1:
                return rows[0]
            return self._create_unified_from_raw(raw_table, raw_id)
        return None

    def _create_unified_from_raw(self, raw_table: str, raw_id: str) -> Optional[Dict[str, Any]]:
        if raw_table not in RAG_PREPARE_VECTORIZE_RAW_TABLES:
            return None
        raw_row = self._load_raw_row(raw_table, raw_id)
        if not raw_row:
            return None
        # クラスルーム(03/04): classification2=コース名, classification3=NULL（投稿種別は不要）
        # 05_ikuya_waseaca_01_raw: classification2=コース名, classification3=カテゴリ（従来どおり）
        # それ以外: classification2=NULL, classification3=カテゴリ
        _CLASSROOM_RAW_TABLES = (
            "03_ema_classroom_01_raw",
            "04_ikuya_classroom_01_raw",
        )
        _COURSE_RAW_TABLES = (
            "03_ema_classroom_01_raw",
            "04_ikuya_classroom_01_raw",
            "05_ikuya_waseaca_01_raw",
        )
        doc = {
            "id": str(raw_id),
            "raw_id": str(raw_id),
            "raw_table": raw_table,
            "person": raw_row.get("person"),
            "classification1": raw_row.get("source"),
            "classification2": (
                raw_row.get("course_name") if raw_table in _COURSE_RAW_TABLES else None
            ),
            # クラスルーム(03/04)は classification3=None（投稿種別を入れない）
            "classification3": (
                None if raw_table in _CLASSROOM_RAW_TABLES else raw_row.get("category")
            ),
            "title": raw_row.get("title"),
            "file_url": raw_row.get("file_url"),
            "post_at": (
                raw_row.get("created_at") if raw_table in _COURSE_RAW_TABLES else None
            ),
            "due_date": raw_row.get("due_date"),
            "post_type": raw_row.get("post_type"),
            "ui_data": {},
            "meta": {
                "created_by": "rag_prepare_search_index",
                "file_name": raw_row.get("file_name"),
            },
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
            logger.error("raw row load failed: table=%s id=%s error=%s", raw_table, raw_id, e, exc_info=True)
            raise

    def _drive_id_from_ctx(self, ctx: Dict[str, Any]) -> Optional[str]:
        fu = ctx.get("file_url")
        if not fu:
            logger.info(
                "ctx に file_url が無いため Drive ID 不明: raw_table=%s raw_id=%s",
                ctx.get("raw_table"),
                ctx.get("raw_id"),
            )
            return None
        m = DRIVE_URL_RE.search(str(fu))
        if m:
            return m.group(1)
        logger.info("file_url に Drive ID が含まれていません: %s", fu)
        return None

    def _resolve_markdown(self, ctx: Dict[str, Any]) -> tuple[str, Optional[str]]:
        raw_table = ctx.get("raw_table")
        raw_id = ctx.get("raw_id")
        if raw_table and raw_id:
            raw_row = self._load_raw_row(str(raw_table), raw_id)
            sections: List[str] = []

            # Stage F ではファイル外テキストを本文に混ぜない。検索データ準備で raw メタと PDF MD を統合する。
            external = self._raw_external_markdown(raw_row, raw_table=str(raw_table))
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
    def _raw_external_markdown(raw: Dict[str, Any], raw_table: str) -> str:
        if not raw_table or not isinstance(raw_table, str) or not raw_table.strip():
            raise ValueError(f"raw_table が未設定です: {raw_table!r}")
        rt = raw_table.strip()
        _CLASSROOM_RAW_TABLES = (
            "03_ema_classroom_01_raw",
            "04_ikuya_classroom_01_raw",
        )
        is_classroom = rt in _CLASSROOM_RAW_TABLES

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
            if is_classroom and key in ("category", "post_type"):
                continue
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
        AI はテキストを生成・変更しない。行番号のみ返す（行レベル型）。

        戻り値: {"annotations": [...]}
        """
        if not text.strip():
            return {"annotations": []}
        try:
            import json as _json
            import os
            import google.generativeai as genai
            api_key = os.environ.get("GOOGLE_AI_PAID_API_KEY")
            if not api_key:
                raise RuntimeError("GOOGLE_AI_PAID_API_KEY が未設定です")

            lines = text.split("\n")
            numbered = "\n".join(f"{i}: {line}" for i, line in enumerate(lines))

            line_types = (
                "paragraph_break（前行と結合しない・新しい項目・段落の開始行）, "
                "heading_1（文書タイトル等の最重要見出し）, "
                "heading_2（セクション見出し）, "
                "section_break（内容のテーマが切り替わる境界行）, "
                "bullet_item（並列する箇条書き項目）, "
                "blockquote（注記・引用・お知らせ補足）"
            )

            prompt = (
                "次のテキストの各行を読んで、行レベルの構造型を JSON のみで返してください。\n"
                "【絶対ルール】JSON 以外は一切出力しない。テキストを生成・変更しない。\n\n"
                f"行レベルの型（line キー）: {line_types}\n\n"
                "【bullet_item の使い方】\n"
                "  同じレベルで並ぶ項目リストの行には bullet_item を付ける。\n"
                "  例：「①〇〇」「②〇〇」、「・〇〇」、「項目名：値」が複数並ぶ行など。\n"
                "  散文の途中で改行されただけの継続行には付けない（それは paragraph_break）。\n\n"
                "【paragraph_break の使い方】\n"
                "  このテキストは PDF の物理的な折り返しを含む。\n"
                "  散文で前の行から文が継続する場合は付けない（折り返しは結合する）。\n"
                "  前の行と意味的に独立した新しい段落・見出し行には付ける。\n\n"
                "【blockquote の使い方】\n"
                "  本文と別扱いの注記・補足・条件付きお知らせには blockquote を付ける。\n"
                "  例：「※〜」「＊注意〜」「〔補足〕〜」「なお〜」「ただし〜」など、\n"
                "  条件・例外・注意書きとして添えられた行。\n\n"
                "【heading_1 / heading_2 の使い方】\n"
                "  短い見出し行（タイトル・セクション名）には heading_1 または heading_2 を付ける。\n"
                "  heading_1/2 が付いた行は自動的にセクション分割の境界になるので、\n"
                "  見出し行に section_break は不要（重複して付けない）。\n\n"
                "【section_break の使い方】\n"
                "  見出し行ではないが内容のテーマが切り替わる境界行に付ける。\n"
                "  見出し行には heading_1/2 を使い、section_break は使わない。\n\n"
                "出力形式（例）:\n"
                '{"annotations": [{"line": 0, "type": "heading_1"}, '
                '{"line": 3, "type": "bullet_item"}, '
                '{"line": 4, "type": "bullet_item"}, '
                '{"line": 9, "type": "heading_2"}, '
                '{"line": 14, "type": "paragraph_break"}]}\n\n'
                f"テキスト:\n{numbered}"
            )

            genai.configure(api_key=api_key)
            model = genai.GenerativeModel("gemini-3.5-flash-lite")
            resp = model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.0,
                    response_mime_type="application/json",
                    response_schema=_AiAnnotationsResponse,
                ),
                request_options={"timeout": 30},
            )
            data = _json.loads(resp.text.strip())
            if not isinstance(data, dict) or "annotations" not in data:
                keys = list(data.keys()) if isinstance(data, dict) else type(data).__name__
                raise KeyError(f"Gemini応答にannotationsキーが存在しません: {keys}")
            return {
                "annotations": data["annotations"],
            }
        except Exception as e:
            logger.error("[RAG] AI アノテーション取得失敗: %s", e)
            raise RuntimeError(f"AI アノテーション取得に失敗しました: {e}") from e

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
                if "type" not in ann:
                    logger.error("[RAG] アノテーション要素にtypeキーが存在しません (line=%s): %s", idx, ann)
                    continue
                ann_type = ann["type"]
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

        return "\n".join(lines)

    @staticmethod
    def _plain_chunks(text: str, chunk_size: int = 1200) -> List[str]:
        t = text.strip()
        if not t:
            return []
        return [t[i : i + chunk_size] for i in range(0, len(t), chunk_size)]

    @staticmethod
    def _chunk_single_page(
        page_md: str,
        prose_chunk_size: int = 800,
        page_header: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        単一ページ（または単一文書）の構造化MDから地の文チャンクと表チャンクを生成する。
        """
        results: List[Dict[str, Any]] = []

        # 地の文: 旧フォーマット（## 非表（F 地の文））または新フォーマット（## 表（埋め込み）より前のテキスト）
        prose_text = ""
        prose_matches = list(re.finditer(
            r'^## 非表（F 地の文）\s*\n(.*?)(?=^## (?:非表|表（埋め込み）)|\Z)',
            page_md, re.MULTILINE | re.DOTALL,
        ))
        if prose_matches:
            prose_parts = [m.group(1).strip() for m in prose_matches if m.group(1).strip()]
            prose_text = "\n\n".join(prose_parts).strip()
        else:
            embed_m = re.search(r'(?:^|\n)## 表（埋め込み）', page_md)
            if embed_m:
                candidate = page_md[:embed_m.start()]
            else:
                candidate = page_md
            # ::title:: / ::summary:: / ## heading を除去してプレーンテキスト化
            candidate = re.sub(r'^::title::.*$', '', candidate, flags=re.MULTILINE)
            candidate = re.sub(r'^::summary::.*$', '', candidate, flags=re.MULTILINE)
            candidate = re.sub(r'^## .+$', '', candidate, flags=re.MULTILINE)
            prose_text = re.sub(r'\n{3,}', '\n\n', candidate).strip()

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
                        if page_header and not text.startswith(page_header):
                            text = page_header + "\n\n" + text
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
                    if page_header and not text.startswith(page_header):
                        text = page_header + "\n\n" + text
                    results.append({"text": text, "chunk_type": "prose", "chunk_weight": 1.0})

        # YAML テーブル（## 表（埋め込み）内の ```yaml ブロック）
        yaml_matches = list(re.finditer(r'```yaml\s*\n(.*?)```', page_md, re.DOTALL))
        for ym in yaml_matches:
            yaml_text = ym.group(1).strip()
            for block in re.split(r'(?=^\s*- table_id:)', yaml_text, flags=re.MULTILINE):
                block = block.strip()
                if not block or not block.startswith('- table_id:'):
                    continue

                try:
                    parsed = yaml.safe_load(block)
                except Exception as e:
                    raise ValueError(f"表 YAML ブロックの解析に失敗しました: {e}\n{block}")

                if isinstance(parsed, list):
                    if len(parsed) != 1:
                        raise ValueError(f"表 YAML ブロックの要素数がちょうど1つではありません (要素数: {len(parsed)}): {block}")
                    if not isinstance(parsed[0], dict):
                        raise ValueError(f"表 YAML ブロックの要素が辞書形式ではありません: {type(parsed[0])}\n{block}")
                    entry = parsed[0]
                elif isinstance(parsed, dict):
                    entry = parsed
                else:
                    raise ValueError(f"表 YAML ブロックの解析結果が辞書形式ではありません: {type(parsed)}\n{block}")

                raw_table_id = entry.get('table_id')
                if not raw_table_id:
                    raise ValueError(f"表 YAML ブロックに table_id が存在しません: {block}")
                table_id = str(raw_table_id).strip()

                desc_val = entry.get('description')
                if desc_val is None:
                    raise ValueError(f"表 YAML ブロック (table_id='{table_id}') に description が存在しません: {block}")
                desc = str(desc_val).strip()

                # その表自身の caption と description の両方を YAML から取得
                caption_val = entry.get('caption')
                caption = str(caption_val).strip() if caption_val is not None else ""

                ctx_parts: List[str] = []
                if caption:
                    ctx_parts.append(caption)
                if desc:
                    ctx_parts.append(desc)

                prefix = '\n'.join(ctx_parts)
                text = f"{prefix}\n\n{block}" if prefix else block
                results.append({"text": text, "chunk_type": "table_yaml", "chunk_weight": 2.0})

        return results

    @staticmethod
    def _structured_md_chunks(
        md_text: str,
        prose_chunk_size: int = 800,
        file_name: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        構造化MD（## 非表（F 地の文）/ ## 表（埋め込み）形式、または ## Page {n} で複数ページが結合された形式）をチャンク化する。

        - 本文が『## Page {n}』見出しで区切られている場合は、ページごとに
          そのページの『## 表（埋め込み）』より前を本文チャンク、そのページの yaml の各 table_id を表チャンクとして全ページ分をページ順に返す。
        - 『## Page』見出しが無い文書は現行どおりの動作を変えない。

        Returns: list of {"text", "chunk_type", "chunk_weight"}
        """
        page_pattern = re.compile(r'^## Page (\d+)\b[^\n]*', re.MULTILINE)
        matches = list(page_pattern.finditer(md_text))

        default_header = f"[{file_name}]" if file_name else None

        if not matches:
            # ## Page 見出しが無い文書は現行どおりの動作
            return RagPrepareSearchIndexer._chunk_single_page(
                md_text, prose_chunk_size=prose_chunk_size, page_header=default_header
            )

        results: List[Dict[str, Any]] = []

        # 最初の ## Page 見出しより前にテキストがあれば先に処理（黙って捨てない）
        if matches[0].start() > 0:
            preamble = md_text[:matches[0].start()].strip()
            if preamble:
                results.extend(
                    RagPrepareSearchIndexer._chunk_single_page(
                        preamble,
                        prose_chunk_size=prose_chunk_size,
                        page_header=default_header,
                    )
                )

        # ページごとに順次チャンク化（全ページ分をページ順に返す）
        for i, m in enumerate(matches):
            start = m.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(md_text)
            page_content = md_text[start:end].strip()
            if not page_content:
                continue

            # 既存の表チャンクの書き方（ヘッダー行: "Page {n}"）に合わせたページ情報
            page_title = m.group(0).replace('##', '').strip()  # 例: "Page 1"
            header = f"[{file_name}] {page_title}" if file_name else page_title
            results.extend(
                RagPrepareSearchIndexer._chunk_single_page(
                    page_content,
                    prose_chunk_size=prose_chunk_size,
                    page_header=header,
                )
            )

        return results

    @staticmethod
    def _md_chunks_with_meta(
        full_markdown: str,
        plain_chunk_size: int = 1200,
        prose_chunk_size: int = 800,
        file_name: Optional[str] = None,
    ) -> List[tuple[str, str, float]]:
        """
        full_markdown をチャンク化し (text, chunk_type, chunk_weight) のリストで返す。

        - # ファイル外テキスト（投稿本文・メタ情報）は AI アノテーションにより構造解析し、chunk_type = 'post_body', chunk_weight = 1.0 の独立断片とする。
        - # PDF抽出Markdown（添付ファイル）は投稿本文を含めず、中身のみ＋ファイル名見出しで独立断片とする（prose / table_yaml）。
        """
        results: List[tuple[str, str, float]] = []

        # section stop: named headers only, not PDF content headings
        _SECTION_STOP = r'(?=^# (?:PDF抽出Markdown|ファイル外テキスト)|\Z)'

        # 1. 投稿本文（ファイル外テキスト）の独立断片化
        ext_m = re.search(
            r'^# ファイル外テキスト\s*\n(.*?)' + _SECTION_STOP,
            full_markdown, re.MULTILINE | re.DOTALL,
        )
        if ext_m:
            ext_text = ext_m.group(1).strip()
            if ext_text:
                ann_result = RagPrepareSearchIndexer._get_ai_annotations(ext_text)
                annotated = RagPrepareSearchIndexer._apply_annotations(
                    ext_text, ann_result.get("annotations") or []
                )
                # section_break マーカーを _structured_md_chunks が認識する --- に変換
                annotated = annotated.replace(
                    RagPrepareSearchIndexer._SPLIT_MARKER, "\n---\n"
                ).strip()
                wrapped = "## 非表（F 地の文）\n\n" + annotated
                for item in RagPrepareSearchIndexer._structured_md_chunks(
                    wrapped, prose_chunk_size=prose_chunk_size
                ):
                    results.append((item["text"], "post_body", 1.0))

        # 2. 添付ファイル（PDF抽出Markdown）の独立断片化
        pdf_md_m = re.search(
            r'^# PDF抽出Markdown\s*\n(.*?)' + _SECTION_STOP,
            full_markdown, re.MULTILINE | re.DOTALL,
        )
        if pdf_md_m:
            pdf_md = pdf_md_m.group(1).strip()
            if pdf_md:
                is_structured = bool(
                    re.search(r'^## 非表（F 地の文）', pdf_md, re.MULTILINE)
                    or re.search(r'^## 表（埋め込み）', pdf_md, re.MULTILINE)
                    or re.search(r'^## Page \d+', pdf_md, re.MULTILINE)
                )
                if is_structured:
                    for item in RagPrepareSearchIndexer._structured_md_chunks(
                        pdf_md, prose_chunk_size=prose_chunk_size, file_name=file_name
                    ):
                        results.append((item["text"], item["chunk_type"], item["chunk_weight"]))
                else:
                    for c in RagPrepareSearchIndexer._plain_chunks(pdf_md, plain_chunk_size):
                        chunk_text = f"[{file_name}]\n\n{c}" if file_name else c
                        results.append((chunk_text, "file_plain", 1.0))

        # 3. どちらのセクションも見つからなかった場合は契約違反として例外送出
        if not ext_m and not pdf_md_m:
            raise ValueError(
                "本文に '# ファイル外テキスト' または '# PDF抽出Markdown' が含まれていません（契約違反）"
            )

        return results

