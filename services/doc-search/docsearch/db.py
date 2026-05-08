"""Supabase 検索専用クライアント（unified_search_v2 経路のみ）。"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta, date as date_type
from typing import Any, Dict, List, Optional

from loguru import logger
from supabase import Client, create_client

from docsearch.config import settings


class DocSearchDB:
    def __init__(self, *, use_service_role: bool = True):
        if not settings.SUPABASE_URL:
            raise ValueError("SUPABASE_URL が未設定です")
        if use_service_role:
            if not settings.SUPABASE_SERVICE_ROLE_KEY:
                raise ValueError("SUPABASE_SERVICE_ROLE_KEY が未設定です")
            self.client: Client = create_client(
                settings.SUPABASE_URL,
                settings.SUPABASE_SERVICE_ROLE_KEY,
            )
            self._is_service_role = True
        else:
            raise ValueError("doc-search は service_role 接続のみ想定です")

    def get_workspace_hierarchy(self) -> Dict[str, Dict[str, List[str]]]:
        """09 から person→source→category の階層を構築。PostgREST の 1000 行既定を超える場合はページング。"""
        try:
            hierarchy: Dict[str, Dict[str, set]] = {}
            offset = 0
            page_size = 1000
            while True:
                response = (
                    self.client.table("09_unified_documents")
                    .select("person, source, category")
                    .range(offset, offset + page_size - 1)
                    .execute()
                )
                batch = response.data or []
                for doc in batch:
                    person = (doc.get("person") or "").strip()
                    source = (doc.get("source") or "").strip()
                    cat = doc.get("category")
                    if not person or not source:
                        continue
                    hierarchy.setdefault(person, {}).setdefault(source, set())
                    if cat:
                        hierarchy[person][source].add(cat)
                if len(batch) < page_size:
                    break
                offset += page_size
            return {
                p: {s: sorted(list(cats)) for s, cats in sorted(srcs.items())}
                for p, srcs in sorted(hierarchy.items())
            }
        except Exception as e:
            logger.error("get_workspace_hierarchy: {}", e)
            return {}

    def _apply_date_filter(self, results: List[Dict[str, Any]], date_filter: str) -> List[Dict[str, Any]]:
        now = datetime.now()
        filtered_results: List[Dict[str, Any]] = []
        for result in results:
            document_date_str = result.get("document_date")
            if not document_date_str:
                if date_filter == "recent":
                    indexed_at_str = result.get("indexed_at")
                    if indexed_at_str:
                        try:
                            indexed_at = datetime.fromisoformat(indexed_at_str.replace("Z", "+00:00"))
                            if (now - indexed_at).days <= 30:
                                filtered_results.append(result)
                        except Exception:
                            pass
                continue
            try:
                document_date = datetime.strptime(document_date_str, "%Y-%m-%d")
            except Exception:
                continue
            if date_filter == "today":
                if document_date.date() == now.date():
                    filtered_results.append(result)
            elif date_filter == "this_week":
                week_start = now - timedelta(days=now.weekday())
                week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
                if document_date >= week_start:
                    filtered_results.append(result)
            elif date_filter == "this_month":
                if document_date.year == now.year and document_date.month == now.month:
                    filtered_results.append(result)
            elif date_filter == "recent":
                if (now - document_date).days <= 30:
                    filtered_results.append(result)
        return filtered_results

    def _filter_chunks_for_context(
        self,
        chunks: List[Dict[str, Any]],
        query: str,
        best_chunk_id: Optional[str],
        keywords: List[str],
        non_table_weight_threshold: float = 1.0,
        max_other_chunks: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        table_chunks = [c for c in chunks if (c.get("chunk_type") or "").startswith("table_")]
        other_chunks = [c for c in chunks if not (c.get("chunk_type") or "").startswith("table_")]
        relevant_tables: List[Dict[str, Any]] = []
        if keywords and table_chunks:
            for chunk in table_chunks:
                content = chunk.get("chunk_text") or ""
                if any(kw in content for kw in keywords):
                    relevant_tables.append(chunk)
            if not relevant_tables:
                best = [c for c in table_chunks if str(c.get("id")) == str(best_chunk_id)] if best_chunk_id else []
                relevant_tables = best if best else table_chunks[:1]
        else:
            relevant_tables = table_chunks[:1] if table_chunks else []
        filtered_other = [c for c in other_chunks if (c.get("chunk_weight") or 0) >= non_table_weight_threshold]
        # 週レンジなどで「複数日が別チャンクに分散」しているとき、絞り込みで取りこぼさないよう上限で制御する。
        filtered_other.sort(key=lambda x: (x.get("chunk_weight") or 0), reverse=True)
        if max_other_chunks is not None:
            filtered_other = filtered_other[:max_other_chunks]
        return relevant_tables + filtered_other

    def _parse_yyyy_mm_dd(self, s: Optional[Any]) -> Optional[str]:
        if s is None:
            return None
        if isinstance(s, datetime):
            return s.date().isoformat()
        if isinstance(s, date_type) and not isinstance(s, datetime):
            return s.isoformat()
        if not isinstance(s, str):
            s = str(s)
        if not s.strip():
            return None
        t = s.strip()
        if len(t) >= 10:
            t = t[:10]
        try:
            _ = date_type.fromisoformat(t)
            return t
        except Exception:
            return None

    def _coerce_meta_dict(self, meta: Any) -> Dict[str, Any]:
        if meta is None:
            return {}
        if isinstance(meta, dict):
            return meta
        if isinstance(meta, str) and meta.strip():
            try:
                o = json.loads(meta)
                return o if isinstance(o, dict) else {}
            except Exception:
                return {}
        return {}

    def _read_date_signals_from_ix(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """09.ix_date_signals のみ読む（検索側で日付を組み立てない）。"""
        raw = row.get("ix_date_signals")
        if isinstance(raw, str) and raw.strip():
            try:
                raw = json.loads(raw)
            except Exception:
                raw = None
        out: Dict[str, Any] = {
            "normalized_dates": [],
            "normalized_ranges": [],
            "partial_dates": [],
        }
        if not isinstance(raw, dict):
            return out
        nd = raw.get("normalized_dates")
        if isinstance(nd, list):
            seen: set[str] = set()
            for x in nd:
                iso = self._parse_yyyy_mm_dd(str(x))
                if iso:
                    seen.add(iso)
            out["normalized_dates"] = sorted(seen)
        nr = raw.get("normalized_ranges")
        if isinstance(nr, list):
            for r in nr:
                if not isinstance(r, dict):
                    continue
                s = self._parse_yyyy_mm_dd(str(r.get("start") or ""))
                e = self._parse_yyyy_mm_dd(str(r.get("end") or ""))
                if s and e:
                    out["normalized_ranges"].append(
                        {
                            "start": s,
                            "end": e,
                            "source_text": str(r.get("source_text") or ""),
                        }
                    )
        pd = raw.get("partial_dates")
        if isinstance(pd, list):
            for p in pd:
                if not isinstance(p, dict):
                    continue
                try:
                    out["partial_dates"].append(
                        {
                            "year": int(p.get("year")),
                            "month": int(p.get("month")),
                            "day": p.get("day"),
                            "text": str(p.get("text") or ""),
                            "granularity": str(p.get("granularity") or "month"),
                        }
                    )
                except Exception:
                    continue
        return out

    def _calc_time_score(self, doc: Dict[str, Any], date_range: Optional[str], query: Optional[str] = None) -> float:
        # 日付の当たり判定は ix_search_dates（検索用集約）のみ。document_date には寄せない。
        if not date_range or ".." not in date_range:
            return 0.0
        try:
            start_str, end_str = date_range.split("..", 1)
            start = date_type.fromisoformat(start_str.strip())
            end = date_type.fromisoformat(end_str.strip()) if end_str.strip() else date_type(9999, 12, 31)
            days = doc.get("ix_search_dates") or []
            if not isinstance(days, list) or not days:
                return 0.0
            month_m = re.search(r"(\d{1,2})月", query or "")
            specified_month = int(month_m.group(1)) if month_m else None
            best = 0.0
            for raw_d in days:
                try:
                    doc_dt = date_type.fromisoformat(str(raw_d)[:10])
                except Exception:
                    continue
                if start <= doc_dt <= end:
                    best = max(best, 1.0)
                else:
                    dist = min(abs((doc_dt - start).days), abs((doc_dt - end).days))
                    if dist <= 7:
                        best = max(best, 0.55)
                    elif dist <= 14:
                        best = max(best, 0.28)
                    elif specified_month and doc_dt.month == specified_month:
                        best = max(best, 0.22)
            return best
        except Exception:
            return 0.0

    def _calculate_keyword_match_score(self, title: str, keywords: List[str], query: str) -> float:
        normalized_query = query.replace("？", "").replace("?", "").replace("の内容は", "").replace("内容", "").strip()
        for kw in keywords:
            if "（" in kw or "(" in kw:
                if kw in title:
                    return 1.0
        matched_keywords = [kw for kw in keywords if kw in title]
        if not matched_keywords:
            return 0.0
        match_count = len(matched_keywords)
        total_keywords = len(keywords)
        if match_count == total_keywords:
            return 0.95
        if match_count >= 2:
            return 0.90
        return 0.85

    def _extract_date(self, query: str) -> Optional[str]:
        current_year = datetime.now().year
        match = re.search(r"(\d{4})-(\d{2})-(\d{2})", query)
        if match:
            try:
                date_obj = datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)))
                return date_obj.strftime("%Y-%m-%d")
            except ValueError:
                pass
        match = re.search(r"(\d{1,2})/(\d{1,2})", query)
        if match:
            month, day = int(match.group(1)), int(match.group(2))
            try:
                return datetime(current_year, month, day).strftime("%Y-%m-%d")
            except ValueError:
                pass
        match = re.search(r"(\d{1,2})月(\d{1,2})日", query)
        if match:
            month, day = int(match.group(1)), int(match.group(2))
            try:
                return datetime(current_year, month, day).strftime("%Y-%m-%d")
            except ValueError:
                pass
        return None

    def _extract_keywords(self, query: str) -> List[str]:
        keywords: List[str] = []
        keywords.extend(re.findall(r"[（(]([^）)]+)[）)]", query))
        keywords.extend(re.findall(r"[\w一-龠ぁ-んァ-ヶー]+[（(][^）)]+[）)]", query))
        cleaned_query = query
        for particle in ["の", "は", "を", "が", "に", "へ", "と", "から", "まで", "で", "？", "?"]:
            cleaned_query = cleaned_query.replace(particle, " ")
        words = re.findall(r"[一-龠ァ-ヶー]{2,}", cleaned_query)
        keywords.extend(words)
        keywords = [kw.strip() for kw in keywords if kw.strip()]
        return list(set(keywords))

    async def search_documents(
        self,
        query: str,
        embedding: List[float],
        limit: int = 50,
        sources: Optional[List[str]] = None,
        persons: Optional[List[str]] = None,
        category: Optional[List[str]] = None,
        date_filter: Optional[str] = None,
        threshold: float = 0.4,
        date_range: Optional[str] = None,
        filter_date_start: Optional[date_type] = None,
        filter_date_end: Optional[date_type] = None,
        calendar_filter_date_start: Optional[date_type] = None,
        calendar_filter_date_end: Optional[date_type] = None,
        rpc_match_count: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        mc = rpc_match_count if rpc_match_count is not None else settings.UNIFIED_SEARCH_RPC_MATCH_COUNT
        rpc_params = {
            "query_text": query,
            "query_embedding": embedding,
            "match_threshold": -1.0,
            "match_count": mc,
            "vector_weight": 0.7,
            "fulltext_weight": 0.3,
            "filter_sources": sources,
            "filter_chunk_types": None,
            "filter_persons": persons,
            "filter_category": category,
            "filter_date_start": filter_date_start.isoformat() if filter_date_start else None,
            "filter_date_end": filter_date_end.isoformat() if filter_date_end else None,
            "calendar_filter_date_start": calendar_filter_date_start.isoformat()
            if calendar_filter_date_start
            else None,
            "calendar_filter_date_end": calendar_filter_date_end.isoformat() if calendar_filter_date_end else None,
        }
        logger.debug("unified_search_v2: sources={} persons={}", sources, persons)
        response = self.client.rpc("unified_search_v2", rpc_params).execute()
        results = response.data if response.data else []
        if date_filter:
            results = self._apply_date_filter(results, date_filter)

        final_results: List[Dict[str, Any]] = []
        for result in results:
            raw_date = result.get("post_at") or result.get("start_at")
            document_date = raw_date[:10] if isinstance(raw_date, str) and len(raw_date) >= 10 else None
            date_signals = self._read_date_signals_from_ix(result)
            doc_id = result.get("doc_id")
            final_results.append(
                {
                    "id": doc_id,
                    "title": result.get("title"),
                    "source": result.get("source"),
                    "person": result.get("person"),
                    "category": result.get("category"),
                    "from_name": result.get("from_name"),
                    "from_email": result.get("from_email"),
                    "snippet": result.get("snippet"),
                    "post_at": result.get("post_at"),
                    "start_at": result.get("start_at"),
                    "end_at": result.get("end_at"),
                    "due_date": result.get("due_date"),
                    "location": result.get("location"),
                    "file_url": result.get("file_url"),
                    "ui_data": result.get("ui_data"),
                    "meta": result.get("meta"),
                    "date_signals": date_signals,
                    "ix_search_dates": result.get("ix_search_dates") or [],
                    "indexed_at": result.get("indexed_at"),
                    "document_date": document_date,
                    "document_body": "",
                    "chunk_content": result.get("best_chunk_text"),
                    "chunk_id": result.get("best_chunk_id"),
                    "chunk_index": result.get("best_chunk_index"),
                    "chunk_type": result.get("best_chunk_type"),
                    "similarity": result.get("combined_score", 0),
                    "raw_similarity": result.get("raw_similarity", 0),
                    "weighted_similarity": result.get("weighted_similarity", 0),
                    "fulltext_score": result.get("fulltext_score", 0),
                    "title_matched": result.get("title_matched", False),
                    "chunk_score": result.get("combined_score", 0),
                    "large_chunk_id": result.get("doc_id"),
                    "small_chunk_id": result.get("best_chunk_id"),
                }
            )

        keywords = self._extract_keywords(query)

        if self._is_service_role:
            week_mode = False
            if date_range and ".." in date_range:
                try:
                    a_s, b_s = date_range.split("..", 1)
                    a = date_type.fromisoformat(a_s.strip())
                    b = date_type.fromisoformat(b_s.strip())
                    if (b - a).days >= 6:
                        week_mode = True
                except Exception:
                    week_mode = False

            non_table_thr = 0.0 if week_mode else 1.0
            max_other = 40 if week_mode else None

            for doc_result in final_results:
                doc_id = doc_result.get("id")
                if not doc_id:
                    continue
                try:
                    body_response = (
                        self.client.table("09_unified_documents")
                        .select("body")
                        .eq("id", doc_id)
                        .limit(1)
                        .execute()
                    )
                    if body_response.data:
                        doc_result["document_body"] = (body_response.data[0].get("body") or "")
                    else:
                        doc_result["document_body"] = ""
                except Exception as e:
                    logger.warning("body fetch doc_id={}: {}", doc_id, e)
                    doc_result["document_body"] = ""

                try:
                    chunks_response = (
                        self.client.table("10_ix_search_index")
                        .select("id, chunk_index, chunk_text, chunk_type, chunk_weight")
                        .eq("doc_id", doc_id)
                        .order("chunk_weight", desc=True)
                        .execute()
                    )
                    if chunks_response.data:
                        raw_chunks = chunks_response.data
                        doc_result["all_chunks"] = self._filter_chunks_for_context(
                            raw_chunks,
                            query=query,
                            best_chunk_id=doc_result.get("chunk_id"),
                            keywords=keywords,
                            non_table_weight_threshold=non_table_thr,
                            max_other_chunks=max_other,
                        )
                    else:
                        doc_result["all_chunks"] = []
                except Exception as e:
                    logger.warning("chunk fetch doc_id={}: {}", doc_id, e)
                    doc_result["all_chunks"] = []
        else:
            for doc_result in final_results:
                doc_result["all_chunks"] = []

        for doc in final_results:
            if keywords:
                kw_boost = self._calculate_keyword_match_score(doc.get("title", ""), keywords, query)
                doc["similarity"] = doc.get("similarity", 0) + kw_boost * 0.2

        delta = settings.DATE_RANGE_THRESHOLD_DELTA
        effective_threshold = threshold - (float(delta) if date_range else 0.0)
        cutoff = [r for r in final_results if r.get("similarity", 0) >= effective_threshold]
        final_results = cutoff if cutoff else final_results[:3]
        final_results.sort(key=lambda x: x.get("similarity", 0), reverse=True)

        eps = settings.REL_SIM_MIX_EPS
        n = len(final_results)
        for rank, doc in enumerate(final_results):
            rel_rank = 1.0 - (rank / (n - 1)) if n > 1 else 1.0
            if eps > 0.0:
                sim_norm = max(0.0, min(1.0, doc.get("similarity", 0)))
                rel = (1.0 - eps) * rel_rank + eps * sim_norm
            else:
                rel = rel_rank
            rel = max(0.0, min(1.0, rel))
            ts = self._calc_time_score(doc, date_range, query=query)
            doc["time_score"] = ts
            doc["rel"] = rel
            doc["final_score"] = max(0.0, min(1.0, 0.75 * rel + 0.25 * ts))
            if ts > 0:
                doc["is_date_matched"] = True

        final_results.sort(key=lambda x: x.get("final_score", 0), reverse=True)
        return final_results

    def search_documents_sync(
        self,
        query: str,
        embedding: List[float],
        limit: int = 50,
        sources: Optional[List[str]] = None,
        persons: Optional[List[str]] = None,
        category: Optional[List[str]] = None,
        date_filter: Optional[str] = None,
        threshold: float = 0.4,
        date_range: Optional[str] = None,
        filter_date_start: Optional[date_type] = None,
        filter_date_end: Optional[date_type] = None,
        calendar_filter_date_start: Optional[date_type] = None,
        calendar_filter_date_end: Optional[date_type] = None,
        rpc_match_count: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        try:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    return loop.run_until_complete(
                        self.search_documents(
                            query,
                            embedding,
                            limit,
                            sources,
                            persons,
                            category,
                            date_filter,
                            threshold,
                            date_range,
                            filter_date_start,
                            filter_date_end,
                            calendar_filter_date_start,
                            calendar_filter_date_end,
                            rpc_match_count,
                        )
                    )
            except RuntimeError:
                pass
            return asyncio.run(
                self.search_documents(
                    query,
                    embedding,
                    limit,
                    sources,
                    persons,
                    category,
                    date_filter,
                    threshold,
                    date_range,
                    filter_date_start,
                    filter_date_end,
                    calendar_filter_date_start,
                    calendar_filter_date_end,
                    rpc_match_count,
                )
            )
        except Exception as e:
            logger.exception("search_documents_sync failed: {}", e)
            raise
