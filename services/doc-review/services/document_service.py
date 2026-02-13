"""
Document Service
doc-review アプリ固有のドキュメント操作

設計方針:
- shared/common/database/client.py は改変しない
- review_status は仮想フィールドとして latest_correction_id から導出
  - reviewed ⇔ latest_correction_id IS NOT NULL
  - pending ⇔ latest_correction_id IS NULL
"""
from typing import List, Dict, Any, Optional
from loguru import logger


def derive_review_status(document: Dict[str, Any]) -> str:
    """
    ドキュメントの review_status を導出

    Args:
        document: ドキュメントレコード

    Returns:
        'reviewed' or 'pending'
    """
    return 'reviewed' if document.get('latest_correction_id') else 'pending'


def get_documents_with_review_status(
    db_client,
    limit: int = 50,
    workspace: Optional[str] = None,
    file_type: Optional[str] = None,
    review_status: Optional[str] = None,
    search_query: Optional[str] = None,
    exclude_workspace: Optional[str] = None,
    doc_type: Optional[str] = None,
    processing_status: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    レビューステータス付きでドキュメント一覧を取得

    review_status フィルタは latest_correction_id で実現:
    - reviewed → latest_correction_id IS NOT NULL
    - pending → latest_correction_id IS NULL
    - all / None → フィルタなし

    Args:
        db_client: DatabaseClient インスタンス
        limit: 取得件数上限
        workspace: ワークスペースフィルタ
        file_type: ファイルタイプフィルタ
        review_status: 'reviewed', 'pending', 'all', または None
        search_query: 検索クエリ（ファイル名部分一致）
        exclude_workspace: 除外するワークスペース
        doc_type: ドキュメントタイプフィルタ
        processing_status: 処理ステータスフィルタ ('completed', 'pending', 等)

    Returns:
        review_status フィールドを付与したドキュメントリスト
    """
    try:
        # 直接 Supabase クライアントを使用（DatabaseClient改変を避ける）
        query = db_client.client.table('Rawdata_FILE_AND_MAIL').select('*')

        # 基本フィルタ
        if workspace:
            query = query.eq('workspace', workspace)

        if exclude_workspace:
            query = query.neq('workspace', exclude_workspace)

        if file_type:
            query = query.eq('file_type', file_type)

        if doc_type:
            query = query.eq('doc_type', doc_type)

        # processing_status フィルタ（completedのみ表示など）
        if processing_status:
            query = query.eq('processing_status', processing_status)

        # review_status フィルタ（latest_correction_id で判定）
        if review_status == 'reviewed':
            query = query.not_.is_('latest_correction_id', 'null')
        elif review_status == 'pending':
            query = query.is_('latest_correction_id', 'null')
        # 'all' または None の場合はフィルタなし

        # 検索クエリ
        if search_query:
            query = query.ilike('file_name', f'%{search_query}%')

        # ソートと件数制限
        query = query.order('updated_at', desc=True).limit(limit)

        response = query.execute()
        documents = response.data if response.data else []

        # review_status を導出して付与
        for doc in documents:
            doc['review_status'] = derive_review_status(doc)

        return documents

    except Exception as e:
        logger.error(f"Failed to get documents: {e}")
        return []


def get_emails_with_review_status(
    db_client,
    limit: int = 50,
    doc_type: Optional[str] = None,
    review_status: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    レビューステータス付きでメール一覧を取得

    Args:
        db_client: DatabaseClient インスタンス
        limit: 取得件数上限
        doc_type: ドキュメントタイプフィルタ (DM-mail, JOB-mail など)
        review_status: 'reviewed', 'pending', 'all', または None

    Returns:
        review_status フィールドを付与したメールリスト
    """
    try:
        query = db_client.client.table('Rawdata_FILE_AND_MAIL').select('*')

        # メールは workspace='gmail' で固定
        query = query.eq('workspace', 'gmail')

        if doc_type:
            query = query.eq('doc_type', doc_type)

        # review_status フィルタ
        if review_status == 'reviewed':
            query = query.not_.is_('latest_correction_id', 'null')
        elif review_status == 'pending':
            query = query.is_('latest_correction_id', 'null')

        query = query.order('updated_at', desc=True).limit(limit)

        response = query.execute()
        emails = response.data if response.data else []

        for email in emails:
            email['review_status'] = derive_review_status(email)

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
    Stage A の解析結果を DB に保存

    Args:
        db_client: DatabaseClient インスタンス
        document_id: ドキュメントID
        a_result: Stage A の結果（origin_app, layout_profile, pdf_creator, pdf_producer など）

    Returns:
        成功した場合 True、失敗した場合 False
    """
    try:
        logger.info(f"[DocumentService] Stage A 結果を保存: {document_id}")

        # PDF メタデータから Creator/Producer を取得
        raw_metadata = a_result.get('raw_metadata', {})
        pdf_creator = raw_metadata.get('Creator', '')
        pdf_producer = raw_metadata.get('Producer', '')

        # Supabase クライアントを直接使用
        response = db_client.client.table('Rawdata_FILE_AND_MAIL').update({
            'pdf_creator': pdf_creator,
            'pdf_producer': pdf_producer,
            'origin_app': a_result.get('origin_app'),
            'layout_profile': a_result.get('layout_profile'),
            'doc_type': a_result.get('document_type')  # 後方互換性
        }).eq('id', document_id).execute()

        if response.data:
            logger.info(f"[DocumentService] Stage A 結果保存成功: {document_id}")
            logger.info(f"  ├─ origin_app: {a_result.get('origin_app')}")
            logger.info(f"  ├─ layout_profile: {a_result.get('layout_profile')}")
            logger.info(f"  ├─ pdf_creator: {pdf_creator[:50]}..." if len(pdf_creator) > 50 else f"  ├─ pdf_creator: {pdf_creator}")
            logger.info(f"  └─ pdf_producer: {pdf_producer[:50]}..." if len(pdf_producer) > 50 else f"  └─ pdf_producer: {pdf_producer}")
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
    ui_data: Dict[str, Any]
) -> bool:
    """
    Stage G の解析結果を DB に保存

    Args:
        db_client: DatabaseClient インスタンス
        document_id: ドキュメントID
        ui_data: Stage G の ui_data（クリーンなUI用データ）

    Returns:
        成功した場合 True、失敗した場合 False
    """
    try:
        logger.info(f"[DocumentService] Stage G 結果を保存: {document_id}")

        # Supabase クライアントを直接使用
        response = db_client.client.table('Rawdata_FILE_AND_MAIL').update({
            'stage_g_structured_data': ui_data,
            'processing_status': 'completed'  # 構造化完了
        }).eq('id', document_id).execute()

        if response.data:
            logger.info(f"[DocumentService] Stage G 結果保存成功: {document_id}")
            return True
        else:
            logger.warning(f"[DocumentService] Stage G 結果保存失敗（レコードなし）: {document_id}")
            return False

    except Exception as e:
        logger.error(f"[DocumentService] Stage G 結果保存エラー: {e}", exc_info=True)
        return False
