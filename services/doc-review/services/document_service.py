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
