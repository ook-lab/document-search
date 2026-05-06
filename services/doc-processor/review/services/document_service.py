"""
Document Service
doc-review アプリ固有のドキュメント操作

設計方針:
- テーブル: pipeline_meta（レビュー管理・パイプライン状態）
- タイトル: 各 raw の title / file_name / subject 相当（09 は正本に使わない）
- 絞り込み軸: person, source, category
- review_status は仮想フィールドとして latest_correction_id から導出
  - reviewed ⇔ latest_correction_id IS NOT NULL
  - pending  ⇔ latest_correction_id IS NULL
"""
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional

from loguru import logger

_dp_root = Path(__file__).resolve().parents[2]
if str(_dp_root) not in sys.path:
    sys.path.insert(0, str(_dp_root))

from raw_title_search import raw_ids_for_title_keyword
from ui_data_markdown import CLASSROOM_RAW_MD_TABLES, ui_data_to_final_markdown


def derive_review_status(document: Dict[str, Any]) -> str:
    """
    ドキュメントの review_status を導出

    Args:
        document: pipeline_meta レコード

    Returns:
        'reviewed' or 'pending'
    """
    return 'reviewed' if document.get('latest_correction_id') else 'pending'


def _title_from_raw_row(raw_table: str, row: Dict[str, Any]) -> Optional[str]:
    if raw_table == "01_gmail_01_raw":
        return (row.get("header_subject") or "").strip() or None
    if raw_table == "02_gcal_01_raw":
        return (row.get("summary") or "").strip() or None
    fn = (row.get("file_name") or "").strip()
    tl = (row.get("title") or "").strip()
    return (fn or tl) or None


def _fetch_titles(db_client, docs: List[Dict[str, Any]]) -> None:
    """pipeline_meta 行に対応する raw から file_name（表示用）を付与する。"""
    if not docs:
        return
    by_table: Dict[str, List[str]] = defaultdict(list)
    for doc in docs:
        rid = doc.get("raw_id")
        rt = doc.get("raw_table")
        if rid and rt:
            by_table[str(rt)].append(str(rid))
    title_map: Dict[tuple, str] = {}
    for rt, ids in by_table.items():
        uniq = list(dict.fromkeys(ids))
        chunk_size = 80
        for i in range(0, len(uniq), chunk_size):
            chunk = uniq[i : i + chunk_size]
            try:
                if rt == "01_gmail_01_raw":
                    sel = "id, header_subject"
                elif rt == "02_gcal_01_raw":
                    sel = "id, summary"
                else:
                    sel = "id, title, file_name"
                response = db_client.client.table(rt).select(sel).in_("id", chunk).execute()
                for row in response.data or []:
                    rid = str(row.get("id"))
                    t = _title_from_raw_row(rt, row)
                    if t:
                        title_map[(rid, rt)] = t
            except Exception as e:
                logger.warning("raw タイトル取得失敗: %s %s", rt, e)
    for doc in docs:
        key = (str(doc.get("raw_id")), doc.get("raw_table"))
        doc["file_name"] = title_map.get(key)


def get_documents_with_review_status(
    db_client,
    limit: int = 50,
    person: Optional[str] = None,
    source: Optional[str] = None,
    category: Optional[str] = None,
    review_status: Optional[str] = None,
    search_query: Optional[str] = None,
    processing_status: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    レビューステータス付きでドキュメント一覧を取得（pipeline_meta ベース）

    Args:
        db_client: DatabaseClient インスタンス
        limit: 取得件数上限
        person: person フィルタ
        source: source フィルタ
        category: category フィルタ
        review_status: 'reviewed', 'pending', 'all', または None
        search_query: 検索クエリ（各 raw の title / file_name / subject で部分一致）
        processing_status: 処理ステータスフィルタ

    Returns:
        review_status フィールドを付与したドキュメントリスト
    """
    try:
        raw_id_filter = None
        if search_query and str(search_query).strip():
            raw_id_filter = raw_ids_for_title_keyword(db_client, search_query)
            if raw_id_filter is not None and len(raw_id_filter) == 0:
                return []

        query = db_client.client.table('pipeline_meta').select('*')

        # Gmail は除外（emails 側で処理）
        query = query.neq('raw_table', '01_gmail_01_raw')

        if person:
            query = query.eq('person', person)
        if source:
            query = query.eq('source', source)
        if category:
            query = query.eq('category', category)
        if processing_status:
            query = query.eq('processing_status', processing_status)

        if review_status == 'reviewed':
            query = query.not_.is_('latest_correction_id', 'null')
        elif review_status == 'pending':
            query = query.is_('latest_correction_id', 'null')
        # 'all' または None の場合はフィルタなし

        if raw_id_filter is not None:
            query = query.in_('raw_id', raw_id_filter)

        query = query.order('updated_at', desc=True).limit(limit)

        response = query.execute()
        documents = response.data if response.data else []

        _fetch_titles(db_client, documents)
        for doc in documents:
            doc['review_status'] = derive_review_status(doc)

        return documents

    except Exception as e:
        logger.error(f"Failed to get documents: {e}")
        return []


def get_emails_with_review_status(
    db_client,
    limit: int = 50,
    category: Optional[str] = None,
    review_status: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    レビューステータス付きでメール一覧を取得（pipeline_meta ベース）

    pipeline_meta から取得後、01_gmail_01_raw の display_* フィールドを結合する。

    Args:
        db_client: DatabaseClient インスタンス
        limit: 取得件数上限
        category: category フィルタ
        review_status: 'reviewed', 'pending', 'all', または None

    Returns:
        display_* / is_reviewed フィールドを付与したメールリスト
    """
    try:
        query = db_client.client.table('pipeline_meta').select('*')

        query = query.eq('raw_table', '01_gmail_01_raw')
        query = query.eq('processing_status', 'completed')

        if category:
            query = query.eq('category', category)

        if review_status == 'reviewed':
            query = query.not_.is_('latest_correction_id', 'null')
        elif review_status == 'pending':
            query = query.is_('latest_correction_id', 'null')

        query = query.order('updated_at', desc=True).limit(limit)

        response = query.execute()
        emails = response.data if response.data else []

        if not emails:
            return []

        raw_ids = [str(e['raw_id']) for e in emails if e.get('raw_id')]
        raw_map: Dict[str, Dict] = {}
        if raw_ids:
            try:
                chunk_size = 80
                for i in range(0, len(raw_ids), chunk_size):
                    chunk = raw_ids[i : i + chunk_size]
                    rw = (
                        db_client.client
                        .table("01_gmail_01_raw")
                        .select(
                            "id, header_subject, from_name, from_email, snippet, body_plain, sent_at"
                        )
                        .in_("id", chunk)
                        .execute()
                    )
                    for r in rw.data or []:
                        raw_map[str(r["id"])] = r
            except Exception as e:
                logger.warning(f"01_gmail_01_raw 結合失敗（継続）: {e}")

        for email in emails:
            rw = raw_map.get(str(email.get('raw_id')), {})
            email['display_subject'] = (rw.get('header_subject') or '').strip()
            email['display_sender'] = (rw.get('from_name') or '').strip()
            email['display_sender_email'] = (rw.get('from_email') or '').strip()
            email['display_sent_at'] = rw.get('sent_at') or email.get('created_at', '')
            email['display_snippet'] = (rw.get('body_plain') or rw.get('snippet') or '').strip()
            email['doc_type']             = email.get('category', '')
            email['review_status']        = derive_review_status(email)
            email['is_reviewed']          = email['review_status'] == 'reviewed'

        return emails

    except Exception as e:
        logger.error(f"Failed to get emails: {e}")
        return []


def update_stage_a_result(
    db_client,
    document_id: str,
    a_result: Dict[str, Any]
) -> bool:
    """
    Stage A の解析結果を pipeline_meta に保存

    Args:
        db_client: DatabaseClient インスタンス
        document_id: pipeline_meta.id
        a_result: Stage A の結果

    Returns:
        成功した場合 True
    """
    try:
        logger.info(f"[DocumentService] Stage A 結果を保存: {document_id}")

        raw_metadata = a_result.get('raw_metadata', {})
        pdf_creator = raw_metadata.get('Creator', '')
        pdf_producer = raw_metadata.get('Producer', '')

        gatekeeper_result = a_result.get('a5_gatekeeper') or a_result.get('gatekeeper') or {}

        update_data = {
            'pdf_creator':          pdf_creator,
            'pdf_producer':         pdf_producer,
            'origin_app':           a_result.get('origin_app'),
            'layout_profile':       a_result.get('layout_profile'),
            'origin_confidence':    a_result.get('confidence'),
            'gate_decision':        gatekeeper_result.get('decision'),
            'gate_block_code':      gatekeeper_result.get('block_code'),
            'gate_block_reason':    gatekeeper_result.get('block_reason'),
            'gate_policy_version':  gatekeeper_result.get('policy_version'),
        }

        response = (
            db_client.client
            .table('pipeline_meta')
            .update(update_data)
            .eq('id', document_id)
            .execute()
        )

        if response.data:
            logger.info(f"[DocumentService] Stage A 結果保存成功: {document_id}")
            return True
        else:
            logger.warning(f"[DocumentService] Stage A 結果保存失敗（レコードなし）: {document_id}")
            return False

    except Exception as e:
        logger.error(f"[DocumentService] Stage A 結果保存エラー: {e}", exc_info=True)
        return False


def update_stage_g_result(
    db_client,
    document_id: str,
    ui_data: Dict[str, Any],
    final_metadata: Optional[Dict[str, Any]] = None
) -> bool:
    """
    Stage G の解析結果を保存

    - pipeline_meta.processing_status を 'completed' に更新
    - 03–05 classroom raw の pdf_md_content に最終 MD（ui_data から生成）を保存
    - 09 には書かない（中間 JSON は pipeline_meta 側の責務）
    """
    try:
        logger.info(f"[DocumentService] Stage G 結果を保存: {document_id}")

        pm_sel = (
            db_client.client
            .table("pipeline_meta")
            .select("id, raw_id, raw_table, processing_status")
            .eq("id", document_id)
            .limit(1)
            .execute()
        )
        if not pm_sel.data:
            logger.warning(f"[DocumentService] pipeline_meta 不在: {document_id}")
            return False
        pm0 = pm_sel.data[0]

        pm_response = (
            db_client.client
            .table('pipeline_meta')
            .update({'processing_status': 'completed'})
            .eq('id', document_id)
            .execute()
        )

        if not pm_response.data:
            logger.warning(f"[DocumentService] pipeline_meta 更新失敗: {document_id}")
            return False

        pm = pm_response.data[0]
        raw_id = pm.get('raw_id') or pm0.get('raw_id')
        raw_table = pm.get('raw_table') or pm0.get('raw_table')

        if final_metadata:
            g11 = final_metadata.get('g11_output', [])
            g14 = final_metadata.get('g14_output', [])
            g17 = final_metadata.get('g17_output', [])
            g21 = final_metadata.get('g21_output', [])
            g22 = final_metadata.get('g22_output', {})
            logger.info(f"[DocumentService] G-11: {len(g11)}表")
            logger.info(f"[DocumentService] G-14: {len(g14)}表")
            logger.info(f"[DocumentService] G-17: {len(g17)}表")
            logger.info(f"[DocumentService] G-21: {len(g21)}記事")
            logger.info(f"[DocumentService] G-22: イベント{len(g22.get('calendar_events', []))}件")

        if raw_table in CLASSROOM_RAW_MD_TABLES and raw_id:
            md = ui_data_to_final_markdown(ui_data)
            now_iso = datetime.now(timezone.utc).isoformat()
            raw_res = (
                db_client.client
                .table(raw_table)
                .update({"pdf_md_content": md, "pdf_md_updated_at": now_iso})
                .eq("id", raw_id)
                .execute()
            )
            if not raw_res.data:
                logger.warning(
                    "[DocumentService] raw pdf_md 更新失敗: %s %s", raw_table, raw_id
                )
                return False
        else:
            logger.info(
                "[DocumentService] classroom 03–05 以外のため raw MD スキップ: raw_table=%s",
                raw_table,
            )

        logger.info(f"[DocumentService] Stage G 結果保存成功: {document_id}")
        return True

    except Exception as e:
        logger.error(f"[DocumentService] Stage G 結果保存エラー: {e}", exc_info=True)
        return False
