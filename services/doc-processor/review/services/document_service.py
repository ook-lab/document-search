"""
Document Service
doc-review アプリ固有のドキュメント操作

設計方針:
- テーブル: pipeline_meta（レビュー管理・パイプライン状態）
- タイトル: 09_unified_documents.title（file_name 相当）
- 絞り込み軸: person, source, category
- review_status は仮想フィールドとして latest_correction_id から導出
  - reviewed ⇔ latest_correction_id IS NOT NULL
  - pending  ⇔ latest_correction_id IS NULL
"""
from typing import List, Dict, Any, Optional
from loguru import logger


def derive_review_status(document: Dict[str, Any]) -> str:
    """
    ドキュメントの review_status を導出

    Args:
        document: pipeline_meta レコード

    Returns:
        'reviewed' or 'pending'
    """
    return 'reviewed' if document.get('latest_correction_id') else 'pending'


def _fetch_titles(db_client, docs: List[Dict[str, Any]]) -> None:
    """
    pipeline_meta のリストに 09_unified_documents.title を file_name として付与

    Args:
        db_client: DatabaseClient インスタンス
        docs: pipeline_meta レコードのリスト（in-place 更新）
    """
    if not docs:
        return
    raw_ids = list({doc['raw_id'] for doc in docs if doc.get('raw_id')})
    if not raw_ids:
        return
    try:
        response = (
            db_client.client
            .table('09_unified_documents')
            .select('raw_id, raw_table, title')
            .in_('raw_id', raw_ids)
            .execute()
        )
        title_map = {
            (r['raw_id'], r['raw_table']): r['title']
            for r in (response.data or [])
        }
        for doc in docs:
            doc['file_name'] = title_map.get((doc.get('raw_id'), doc.get('raw_table')))
    except Exception as e:
        logger.warning(f"Failed to fetch titles from 09_unified_documents: {e}")


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
        search_query: 検索クエリ（09_unified_documents.title で部分一致）
        processing_status: 処理ステータスフィルタ

    Returns:
        review_status フィールドを付与したドキュメントリスト
    """
    try:
        # search_query がある場合は 09_unified_documents.title で raw_id を絞り込む
        raw_id_filter = None
        if search_query:
            try:
                sr = (
                    db_client.client
                    .table('09_unified_documents')
                    .select('raw_id')
                    .ilike('title', f'%{search_query}%')
                    .execute()
                )
                if not sr.data:
                    return []
                raw_id_filter = [r['raw_id'] for r in sr.data]
            except Exception as e:
                logger.warning(f"Failed to search 09_unified_documents.title: {e}")

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

        # 09_unified_documents から display_* フィールドを結合
        raw_ids = [str(e['raw_id']) for e in emails if e.get('raw_id')]
        ud_map: Dict[str, Dict] = {}
        if raw_ids:
            try:
                ud_res = (
                    db_client.client
                    .table('09_unified_documents')
                    .select('raw_id, title, from_name, from_email, snippet, body, post_at')
                    .eq('raw_table', '01_gmail_01_raw')
                    .in_('raw_id', raw_ids)
                    .execute()
                )
                ud_map = {str(r['raw_id']): r for r in (ud_res.data or [])}
            except Exception as e:
                logger.warning(f"09_unified_documents 結合失敗（継続）: {e}")

        for email in emails:
            ud = ud_map.get(str(email.get('raw_id')), {})
            email['display_subject']      = ud.get('title') or ''
            email['display_sender']       = ud.get('from_name') or ''
            email['display_sender_email'] = ud.get('from_email') or ''
            email['display_sent_at']      = ud.get('post_at') or email.get('created_at', '')
            email['display_snippet']      = ud.get('body') or ud.get('snippet') or ''
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
    - 09_unified_documents.ui_data / meta に構造化データを保存

    Args:
        db_client: DatabaseClient インスタンス
        document_id: pipeline_meta.id
        ui_data: Stage G の ui_data（UI用構造化データ）
        final_metadata: G11/G14/G17/G21/G22 の出力

    Returns:
        成功した場合 True
    """
    try:
        logger.info(f"[DocumentService] Stage G 結果を保存: {document_id}")

        # pipeline_meta を completed に更新
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

        # 09_unified_documents に ui_data と meta を保存
        meta = {}
        if final_metadata:
            g11 = final_metadata.get('g11_output', [])
            g14 = final_metadata.get('g14_output', [])
            g17 = final_metadata.get('g17_output', [])
            g21 = final_metadata.get('g21_output', [])
            g22 = final_metadata.get('g22_output', {})
            if g11: meta['g11_output'] = g11
            if g14: meta['g14_output'] = g14
            if g17: meta['g17_output'] = g17
            if g21: meta['g21_output'] = g21
            if g22: meta['g22_output'] = g22

            logger.info(f"[DocumentService] G-11: {len(g11)}表")
            logger.info(f"[DocumentService] G-14: {len(g14)}表")
            logger.info(f"[DocumentService] G-17: {len(g17)}表")
            logger.info(f"[DocumentService] G-21: {len(g21)}記事")
            logger.info(f"[DocumentService] G-22: イベント{len(g22.get('calendar_events', []))}件")

        ud_update = {'ui_data': ui_data}
        if meta:
            ud_update['meta'] = meta

        ud_response = (
            db_client.client
            .table('09_unified_documents')
            .update(ud_update)
            .eq('raw_id', pm['raw_id'])
            .eq('raw_table', pm['raw_table'])
            .execute()
        )

        if ud_response.data:
            logger.info(f"[DocumentService] Stage G 結果保存成功: {document_id}")
            return True
        else:
            logger.warning(f"[DocumentService] 09_unified_documents 更新失敗: {document_id}")
            return False

    except Exception as e:
        logger.error(f"[DocumentService] Stage G 結果保存エラー: {e}", exc_info=True)
        return False
