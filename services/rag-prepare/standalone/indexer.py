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
from standalone.scope import RAG_PREPARE_VECTORIZE_RAW_TABLES
from standalone.ud_meta import UD_META_TABLE

logger = logging.getLogger(__name__)

DRIVE_URL_RE = re.compile(r"/d/([a-zA-Z0-9_-]+)")

_UD_SELECT = "id, raw_id, raw_table, person, source, category, title, file_url"


class RagPrepareSearchIndexer:
    def __init__(self) -> None:
        self.db = RagServiceDB()
        self.embedder = EmbeddingGen()

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

            ctx = {
                "raw_id": ud_raw_id,
                "raw_table": rt,
                "file_url": ud.get("file_url"),
            }
            full_markdown, md_err = self._resolve_markdown(ctx)
            if not full_markdown:
                logger.error("インデックス用の本文がありません: %s", unified_doc_id)
                return False, md_err or "インデックス用の本文がありません（raw / pdf_md_content）"

            unified_doc_id = str(ud["id"])
            person = ud.get("person")
            source = ud.get("source")
            category = ud.get("category") or rt

            self.db.client.table("09_unified_documents").update({"body": full_markdown}).eq(
                "id", unified_doc_id
            ).execute()

            chunks = self._plain_chunks(full_markdown, chunk_size=1200)
            self.db.client.table("10_ix_search_index").delete().eq("doc_id", unified_doc_id).execute()

            for i, chunk_text in enumerate(chunks):
                chunk_text = (chunk_text or "").replace("\u0000", "").strip()
                if not chunk_text:
                    continue
                vector = self.embedder.generate_embedding(chunk_text)
                self.db.client.table("10_ix_search_index").insert(
                    {
                        "doc_id": unified_doc_id,
                        "person": person,
                        "source": source,
                        "category": category,
                        "chunk_index": i,
                        "chunk_text": chunk_text,
                        "chunk_type": "rag_prepare_plain",
                        "chunk_weight": 1.0,
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
        ).execute()

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
            "source": raw_row.get("source"),
            "category": raw_row.get("course_name") or raw_row.get("category"),
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
            if pdf_md:
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
    def _plain_chunks(text: str, chunk_size: int = 1200) -> List[str]:
        t = text.strip()
        if not t:
            return []
        return [t[i : i + chunk_size] for i in range(0, len(t), chunk_size)]
