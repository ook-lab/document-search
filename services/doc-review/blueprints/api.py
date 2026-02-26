"""
API Blueprint
REST APIエンドポイント

認可ルール:
- /api/health: 認証不要
- /api/workspaces, /api/stats: 認証必須
- documents / emails 系: すべて認証必須
"""
import os
import re
import json
import tempfile
from pathlib import Path

from flask import Blueprint, jsonify, request, session, Response, abort
from flask_wtf.csrf import generate_csrf
from loguru import logger

from services.auth_service import (
    auth_service,
    login_required,
    get_current_user_email,
    get_db_client_or_abort
)
from services.document_service import (
    get_documents_with_review_status,
    get_emails_with_review_status,
    derive_review_status
)

api_bp = Blueprint('api', __name__)


# =============================================================================
# 認証API
# =============================================================================

@api_bp.route('/auth/login', methods=['POST'])
def login():
    """ログイン"""
    data = request.get_json()
    if not data:
        return jsonify({
            'error_code': 'BAD_REQUEST',
            'message': 'Request body required',
            'details': {}
        }), 400

    email = data.get('email')
    password = data.get('password')

    if not email or not password:
        return jsonify({
            'error_code': 'BAD_REQUEST',
            'message': 'Email and password required',
            'details': {}
        }), 400

    success, error_msg = auth_service.login(email, password)

    if success:
        return jsonify({
            'success': True,
            'user_email': session.get('user_email'),
            'csrf_token': generate_csrf()
        })
    else:
        return jsonify({
            'error_code': 'AUTH_FAILED',
            'message': error_msg or 'Login failed',
            'details': {}
        }), 401


@api_bp.route('/auth/logout', methods=['POST'])
def logout():
    """ログアウト"""
    auth_service.logout()
    return jsonify({'success': True})


@api_bp.route('/auth/session', methods=['GET'])
def get_session():
    """
    セッション情報取得

    CSRFトークンもここで配布
    """
    session_info = auth_service.get_session_info()
    session_info['csrf_token'] = generate_csrf()
    return jsonify(session_info)


# =============================================================================
# ユーティリティAPI（認証不要）
# =============================================================================

@api_bp.route('/health', methods=['GET'])
def health():
    """ヘルスチェック（認証不要）"""
    return jsonify({
        'status': 'healthy',
        'service': 'doc-review',
        'version': '1.0.0'
    })


# =============================================================================
# ユーティリティAPI（認証必須）
# =============================================================================

@api_bp.route('/workspaces', methods=['GET'])
@login_required
def get_workspaces():
    """利用可能なワークスペース一覧（レビューUI専用）

    【設計方針】
    - このエンドポイントは doc-review UI 専用
    - doc-search, doc-processor にも同名の /api/workspaces が存在するが、
      それぞれ別サービス・別ホストで動作するため衝突しない
    - 将来の統合は行わない（各サービスの独立性を維持）
    """
    try:
        db_client = get_db_client_or_abort()
        workspaces = db_client.get_available_workspaces()
        return jsonify({
            'workspaces': workspaces
        })
    except Exception as e:
        logger.error(f"Failed to get workspaces: {e}")
        return jsonify({
            'error_code': 'INTERNAL_ERROR',
            'message': str(e),
            'details': {}
        }), 500


@api_bp.route('/stats', methods=['GET'])
@login_required
def get_stats():
    """レビュー進捗統計"""
    try:
        db_client = get_db_client_or_abort()
        progress = db_client.get_review_progress()
        return jsonify(progress)
    except Exception as e:
        logger.error(f"Failed to get stats: {e}")
        return jsonify({
            'error_code': 'INTERNAL_ERROR',
            'message': str(e),
            'details': {}
        }), 500


# =============================================================================
# Documents API (Read)
# =============================================================================

@api_bp.route('/documents', methods=['GET'])
@login_required
def list_documents():
    """
    ドキュメント一覧取得

    Query params:
        workspace: ワークスペースフィルタ
        review_status: pending / reviewed / all（仮想フィールド、latest_correction_idから導出）
        search: 検索クエリ
        limit: 取得件数（デフォルト50）
    """
    try:
        db_client = get_db_client_or_abort()

        # クエリパラメータ取得
        workspace = request.args.get('workspace')
        review_status = request.args.get('review_status', 'pending')
        search_query = request.args.get('search')
        processing_status = request.args.get('processing_status')
        limit = int(request.args.get('limit', 50))

        # 空文字列をNoneに変換
        if workspace == '':
            workspace = None
        if search_query == '':
            search_query = None
        if processing_status == '':
            processing_status = None

        # サービス層を使用（review_statusはlatest_correction_idから導出）
        documents = get_documents_with_review_status(
            db_client=db_client,
            limit=limit,
            workspace=workspace,
            review_status=review_status,
            search_query=search_query,
            exclude_workspace='gmail',  # Gmailはメール側で処理
            processing_status=processing_status
        )

        return jsonify({
            'documents': documents,
            'count': len(documents)
        })

    except Exception as e:
        logger.error(f"Failed to list documents: {e}")
        return jsonify({
            'error_code': 'INTERNAL_ERROR',
            'message': str(e),
            'details': {}
        }), 500


@api_bp.route('/documents/<doc_id>', methods=['GET'])
@login_required
def get_document(doc_id: str):
    """ドキュメント詳細取得"""
    try:
        db_client = get_db_client_or_abort()
        document = db_client.get_document_by_id(doc_id)

        if not document:
            return jsonify({
                'error_code': 'NOT_FOUND',
                'message': 'Document not found',
                'details': {'doc_id': doc_id}
            }), 404

        # metadataをパース
        metadata = document.get('metadata') or {}
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except json.JSONDecodeError:
                metadata = {}
        document['metadata'] = metadata

        # stage_g_structured_data をパース（UI用構造化データ）
        stage_g_data = document.get('stage_g_structured_data') or {}
        if isinstance(stage_g_data, str):
            try:
                stage_g_data = json.loads(stage_g_data)
            except json.JSONDecodeError:
                stage_g_data = {}
        document['stage_g_structured_data'] = stage_g_data

        # ★G-11/G-14/G-17/G-21/G-22 の個別結果をパース
        for key in ['g11_structured_tables', 'g14_reconstructed_tables', 'g17_table_analyses', 'g21_articles', 'g22_ai_extracted']:
            value = document.get(key)
            if isinstance(value, str):
                try:
                    document[key] = json.loads(value)
                except json.JSONDecodeError:
                    document[key] = [] if key != 'g22_ai_extracted' else {}

        # ★UIが期待する形式で metadata に統合
        # UIは metadata.g11_output, metadata.g17_output, metadata.g21_output, metadata.g22_output を探す
        # metadata が辞書であることを保証
        if not isinstance(metadata, dict):
            metadata = {}

        if document.get('g11_structured_tables'):
            metadata['g11_output'] = document['g11_structured_tables']

        if document.get('g14_reconstructed_tables'):
            metadata['g14_output'] = document['g14_reconstructed_tables']

        # ★G-17: 古い形式（sections形式）を新しい形式（headers/rows形式）に変換
        if document.get('g17_table_analyses'):
            g17_data = document['g17_table_analyses']
            converted_tables = []
            for analysis in g17_data:
                # 古い形式（sections[0].data）の場合は変換
                if 'sections' in analysis and analysis['sections']:
                    section_data = analysis['sections'][0].get('data', [])
                    converted_table = {
                        'table_id': analysis.get('table_id', ''),
                        'table_type': analysis.get('table_type', 'structured'),
                        'description': analysis.get('description', ''),
                        'headers': [],  # UI側で自動生成
                        'rows': section_data,
                        'sections': analysis.get('sections', []),
                        'metadata': analysis.get('metadata', {})
                    }
                    converted_tables.append(converted_table)
                else:
                    # すでに新しい形式の場合はそのまま
                    converted_tables.append(analysis)
            metadata['g17_output'] = converted_tables
        if document.get('g21_articles'):
            metadata['g21_output'] = document['g21_articles']
        if document.get('g22_ai_extracted'):
            metadata['g22_output'] = document['g22_ai_extracted']

        # 統合後の metadata を document に反映
        document['metadata'] = metadata

        return jsonify(document)

    except Exception as e:
        logger.error(f"Failed to get document {doc_id}: {e}")
        return jsonify({
            'error_code': 'INTERNAL_ERROR',
            'message': str(e),
            'details': {}
        }), 500


@api_bp.route('/documents/<doc_id>/preview', methods=['GET'])
@login_required
def preview_document(doc_id: str):
    """
    ドキュメントプレビュー（PDFストリーム）

    HTTP Range対応（206 Partial Content）
    """
    try:
        db_client = get_db_client_or_abort()
        document = db_client.get_document_by_id(doc_id)

        if not document:
            abort(404)

        # ファイルIDを file_url から取得
        _m = re.search(r'/d/([a-zA-Z0-9_-]+)', document.get('file_url') or '')
        file_id = _m.group(1) if _m else None
        file_name = document.get('file_name') or 'document'

        if not file_id:
            return jsonify({
                'error_code': 'NOT_FOUND',
                'message': 'File ID not found',
                'details': {}
            }), 404

        # Google Driveからダウンロード
        from shared.common.connectors.google_drive import GoogleDriveConnector
        drive_connector = GoogleDriveConnector()

        temp_dir = tempfile.gettempdir()
        file_path = drive_connector.download_file(file_id, file_name, temp_dir)

        if not file_path or not Path(file_path).exists():
            return jsonify({
                'error_code': 'DOWNLOAD_FAILED',
                'message': 'Failed to download file from Google Drive',
                'details': {}
            }), 500

        # ファイルサイズ取得
        file_size = os.path.getsize(file_path)

        # MIMEタイプ判定
        file_ext = Path(file_path).suffix.lower()
        mime_types = {
            '.pdf': 'application/pdf',
            '.txt': 'text/plain',
            '.md': 'text/markdown',
            '.json': 'application/json',
            '.csv': 'text/csv',
            '.html': 'text/html',
        }
        content_type = mime_types.get(file_ext, 'application/octet-stream')

        # Rangeヘッダ処理
        range_header = request.headers.get('Range')

        if range_header:
            # Range対応（206 Partial Content）
            byte_range = range_header.replace('bytes=', '').split('-')
            start = int(byte_range[0]) if byte_range[0] else 0
            end = int(byte_range[1]) if byte_range[1] else file_size - 1

            if start >= file_size:
                return Response(status=416)  # Range Not Satisfiable

            length = end - start + 1

            with open(file_path, 'rb') as f:
                f.seek(start)
                data = f.read(length)

            response = Response(
                data,
                status=206,
                mimetype=content_type,
                direct_passthrough=True
            )
            response.headers['Content-Range'] = f'bytes {start}-{end}/{file_size}'
            response.headers['Content-Length'] = length

        else:
            # 全データ返却（200 OK）
            with open(file_path, 'rb') as f:
                data = f.read()

            response = Response(
                data,
                status=200,
                mimetype=content_type,
                direct_passthrough=True
            )
            response.headers['Content-Length'] = file_size

        # 共通ヘッダ
        response.headers['Accept-Ranges'] = 'bytes'
        # ファイル名はRFC 5987形式でエンコード（日本語対応）
        from urllib.parse import quote
        encoded_filename = quote(file_name)
        response.headers['Content-Disposition'] = f"inline; filename*=UTF-8''{encoded_filename}"
        response.headers['Cache-Control'] = 'private, max-age=3600'

        return response

    except Exception as e:
        logger.error(f"Failed to preview document {doc_id}: {e}")
        return jsonify({
            'error_code': 'INTERNAL_ERROR',
            'message': str(e),
            'details': {}
        }), 500


@api_bp.route('/documents/<doc_id>/history', methods=['GET'])
@login_required
def get_document_history(doc_id: str):
    """修正履歴取得"""
    try:
        db_client = get_db_client_or_abort()
        limit = int(request.args.get('limit', 10))
        history = db_client.get_correction_history(doc_id, limit=limit)
        return jsonify({
            'history': history,
            'count': len(history)
        })
    except Exception as e:
        logger.error(f"Failed to get history for {doc_id}: {e}")
        return jsonify({
            'error_code': 'INTERNAL_ERROR',
            'message': str(e),
            'details': {}
        }), 500


# =============================================================================
# Documents API (Write)
# =============================================================================

@api_bp.route('/documents/<doc_id>', methods=['PUT'])
@login_required
def update_document(doc_id: str):
    """
    ドキュメントメタデータ更新

    監査ログ: corrector_emailは必ずsessionのuser_emailを使用
    """
    try:
        db_client = get_db_client_or_abort()
        user_email = get_current_user_email()

        if not user_email:
            return jsonify({
                'error_code': 'UNAUTHORIZED',
                'message': 'User email not found in session',
                'details': {}
            }), 401

        data = request.get_json()
        if not data:
            return jsonify({
                'error_code': 'BAD_REQUEST',
                'message': 'Request body required',
                'details': {}
            }), 400

        metadata = data.get('metadata')
        doc_type = data.get('doc_type')
        notes = data.get('notes', 'Flask UIからの手動修正')

        if metadata is None:
            return jsonify({
                'error_code': 'BAD_REQUEST',
                'message': 'metadata field required',
                'details': {}
            }), 400

        # 修正履歴を記録して保存
        success = db_client.record_correction(
            doc_id=doc_id,
            new_metadata=metadata,
            new_doc_type=doc_type,
            corrector_email=user_email,  # 必ずセッションから取得
            notes=notes
        )

        if success:
            return jsonify({'success': True})
        else:
            return jsonify({
                'error_code': 'UPDATE_FAILED',
                'message': 'Failed to update document',
                'details': {}
            }), 500

    except Exception as e:
        logger.error(f"Failed to update document {doc_id}: {e}")
        return jsonify({
            'error_code': 'INTERNAL_ERROR',
            'message': str(e),
            'details': {}
        }), 500


@api_bp.route('/documents/<doc_id>', methods=['DELETE'])
@login_required
def delete_document(doc_id: str):
    """ドキュメント削除"""
    try:
        db_client = get_db_client_or_abort()
        document = db_client.get_document_by_id(doc_id)

        if not document:
            return jsonify({
                'error_code': 'NOT_FOUND',
                'message': 'Document not found',
                'details': {}
            }), 404

        # Google Driveからも削除
        _m = re.search(r'/d/([a-zA-Z0-9_-]+)', document.get('file_url') or '')
        file_id = _m.group(1) if _m else None
        if file_id:
            try:
                from shared.common.connectors.google_drive import GoogleDriveConnector
                drive_connector = GoogleDriveConnector()
                drive_connector.trash_file(file_id)
            except Exception as e:
                logger.warning(f"Failed to trash file in Drive: {e}")

        # DBから削除
        success = db_client.delete_document(doc_id)

        if success:
            return jsonify({'success': True})
        else:
            return jsonify({
                'error_code': 'DELETE_FAILED',
                'message': 'Failed to delete document',
                'details': {}
            }), 500

    except Exception as e:
        logger.error(f"Failed to delete document {doc_id}: {e}")
        return jsonify({
            'error_code': 'INTERNAL_ERROR',
            'message': str(e),
            'details': {}
        }), 500


@api_bp.route('/documents/<doc_id>/review', methods=['POST'])
@login_required
def mark_document_reviewed(doc_id: str):
    """レビュー済みにマーク"""
    try:
        db_client = get_db_client_or_abort()
        user_email = get_current_user_email()
        success = db_client.mark_document_reviewed(doc_id, reviewed_by=user_email)

        if success:
            return jsonify({'success': True})
        else:
            return jsonify({
                'error_code': 'UPDATE_FAILED',
                'message': 'Failed to mark as reviewed',
                'details': {}
            }), 500

    except Exception as e:
        logger.error(f"Failed to mark document {doc_id} as reviewed: {e}")
        return jsonify({
            'error_code': 'INTERNAL_ERROR',
            'message': str(e),
            'details': {}
        }), 500


@api_bp.route('/documents/<doc_id>/unreview', methods=['POST'])
@login_required
def mark_document_unreviewed(doc_id: str):
    """未レビューに戻す"""
    try:
        db_client = get_db_client_or_abort()
        success = db_client.mark_document_unreviewed(doc_id)

        if success:
            return jsonify({'success': True})
        else:
            return jsonify({
                'error_code': 'UPDATE_FAILED',
                'message': 'Failed to mark as unreviewed',
                'details': {}
            }), 500

    except Exception as e:
        logger.error(f"Failed to mark document {doc_id} as unreviewed: {e}")
        return jsonify({
            'error_code': 'INTERNAL_ERROR',
            'message': str(e),
            'details': {}
        }), 500


@api_bp.route('/documents/<doc_id>/rollback', methods=['POST'])
@login_required
def rollback_document(doc_id: str):
    """ロールバック"""
    try:
        db_client = get_db_client_or_abort()
        success = db_client.rollback_document(doc_id)

        if success:
            return jsonify({'success': True})
        else:
            return jsonify({
                'error_code': 'ROLLBACK_FAILED',
                'message': 'Failed to rollback document',
                'details': {}
            }), 500

    except Exception as e:
        logger.error(f"Failed to rollback document {doc_id}: {e}")
        return jsonify({
            'error_code': 'INTERNAL_ERROR',
            'message': str(e),
            'details': {}
        }), 500


@api_bp.route('/documents/bulk-approve', methods=['POST'])
@login_required
def bulk_approve_documents():
    """一括承認"""
    try:
        db_client = get_db_client_or_abort()
        user_email = get_current_user_email()

        data = request.get_json()
        doc_ids = data.get('doc_ids', [])

        if not doc_ids:
            return jsonify({
                'error_code': 'BAD_REQUEST',
                'message': 'doc_ids required',
                'details': {}
            }), 400

        success_count = 0
        fail_count = 0

        for doc_id in doc_ids:
            if db_client.mark_document_reviewed(doc_id, reviewed_by=user_email):
                success_count += 1
            else:
                fail_count += 1

        return jsonify({
            'success': True,
            'success_count': success_count,
            'fail_count': fail_count
        })

    except Exception as e:
        logger.error(f"Failed to bulk approve: {e}")
        return jsonify({
            'error_code': 'INTERNAL_ERROR',
            'message': str(e),
            'details': {}
        }), 500


@api_bp.route('/documents/bulk-delete', methods=['POST'])
@login_required
def bulk_delete_documents():
    """一括削除"""
    try:
        db_client = get_db_client_or_abort()

        data = request.get_json()
        doc_ids = data.get('doc_ids', [])

        if not doc_ids:
            return jsonify({
                'error_code': 'BAD_REQUEST',
                'message': 'doc_ids required',
                'details': {}
            }), 400

        from shared.common.connectors.google_drive import GoogleDriveConnector
        drive_connector = GoogleDriveConnector()

        success_count = 0
        fail_count = 0

        for doc_id in doc_ids:
            try:
                document = db_client.get_document_by_id(doc_id)
                if document:
                    # Driveから削除
                    _m = re.search(r'/d/([a-zA-Z0-9_-]+)', document.get('file_url') or '')
                    file_id = _m.group(1) if _m else None
                    if file_id:
                        try:
                            drive_connector.trash_file(file_id)
                        except Exception:
                            pass

                    # DBから削除
                    if db_client.delete_document(doc_id):
                        success_count += 1
                    else:
                        fail_count += 1
                else:
                    fail_count += 1
            except Exception:
                fail_count += 1

        return jsonify({
            'success': True,
            'success_count': success_count,
            'fail_count': fail_count
        })

    except Exception as e:
        logger.error(f"Failed to bulk delete: {e}")
        return jsonify({
            'error_code': 'INTERNAL_ERROR',
            'message': str(e),
            'details': {}
        }), 500


# =============================================================================
# Emails API
# =============================================================================

@api_bp.route('/emails', methods=['GET'])
@login_required
def list_emails():
    """
    メール一覧取得

    Query params:
        doc_type: DM-mail / JOB-mail
        review_status: pending / reviewed / all（仮想フィールド）
        limit: 取得件数
    """
    try:
        db_client = get_db_client_or_abort()

        doc_type = request.args.get('doc_type')
        review_status = request.args.get('review_status', 'all')
        limit = int(request.args.get('limit', 50))

        if doc_type == '':
            doc_type = None

        # サービス層を使用（review_statusはlatest_correction_idから導出）
        emails = get_emails_with_review_status(
            db_client=db_client,
            limit=limit,
            doc_type=doc_type,
            review_status=review_status
        )

        return jsonify({
            'emails': emails,
            'count': len(emails)
        })

    except Exception as e:
        logger.error(f"Failed to list emails: {e}")
        return jsonify({
            'error_code': 'INTERNAL_ERROR',
            'message': str(e),
            'details': {}
        }), 500


@api_bp.route('/emails/<email_id>', methods=['GET'])
@login_required
def get_email(email_id: str):
    """メール詳細取得"""
    try:
        db_client = get_db_client_or_abort()
        email = db_client.get_document_by_id(email_id)

        if not email:
            return jsonify({
                'error_code': 'NOT_FOUND',
                'message': 'Email not found',
                'details': {'email_id': email_id}
            }), 404

        # metadataをパース
        metadata = email.get('metadata') or {}
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except json.JSONDecodeError:
                metadata = {}
        email['metadata'] = metadata

        return jsonify(email)

    except Exception as e:
        logger.error(f"Failed to get email {email_id}: {e}")
        return jsonify({
            'error_code': 'INTERNAL_ERROR',
            'message': str(e),
            'details': {}
        }), 500


@api_bp.route('/emails/<email_id>/html', methods=['GET'])
@login_required
def get_email_html(email_id: str):
    """
    メールHTMLプレビュー

    セキュリティ:
    - baseタグ削除
    - 厳格なCSPヘッダー
    - sandbox iframeで表示することを前提
    """
    try:
        db_client = get_db_client_or_abort()
        email = db_client.get_document_by_id(email_id)

        if not email:
            abort(404)

        file_id = email.get('source_id')
        if not file_id:
            return Response(
                '<html><body><p>HTML not available</p></body></html>',
                mimetype='text/html'
            )

        # Google DriveからHTMLをダウンロード
        from shared.common.connectors.google_drive import GoogleDriveConnector
        drive_connector = GoogleDriveConnector()

        temp_dir = tempfile.gettempdir()
        file_name = email.get('file_name') or 'email.html'
        file_path = drive_connector.download_file(file_id, file_name, temp_dir)

        if not file_path or not Path(file_path).exists():
            return Response(
                '<html><body><p>Failed to load HTML</p></body></html>',
                mimetype='text/html'
            )

        with open(file_path, 'r', encoding='utf-8') as f:
            html_content = f.read()

        # baseタグを削除
        import re
        html_content = re.sub(r'<base[^>]*>', '', html_content, flags=re.IGNORECASE)

        response = Response(html_content, mimetype='text/html; charset=utf-8')

        # セキュリティヘッダー
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Cache-Control'] = 'private, no-store'

        # CSP（表示品質を保ちつつスクリプト禁止）
        csp = "; ".join([
            "default-src 'none'",
            "img-src data: https:",
            "style-src 'unsafe-inline' https:",
            "font-src data: https:",
            "script-src 'none'",
            "base-uri 'none'",
            "form-action 'none'"
        ])
        response.headers['Content-Security-Policy'] = csp

        return response

    except Exception as e:
        logger.error(f"Failed to get email HTML {email_id}: {e}")
        return Response(
            f'<html><body><p>Error: {str(e)}</p></body></html>',
            mimetype='text/html'
        )


@api_bp.route('/emails/<email_id>', methods=['PUT'])
@login_required
def update_email(email_id: str):
    """メールメタデータ更新"""
    try:
        db_client = get_db_client_or_abort()
        user_email = get_current_user_email()

        if not user_email:
            return jsonify({
                'error_code': 'UNAUTHORIZED',
                'message': 'User email not found in session',
                'details': {}
            }), 401

        data = request.get_json()
        metadata = data.get('metadata')
        doc_type = data.get('doc_type')
        notes = data.get('notes', 'Flask UIからの手動修正')

        success = db_client.record_correction(
            doc_id=email_id,
            new_metadata=metadata,
            new_doc_type=doc_type,
            corrector_email=user_email,
            notes=notes
        )

        if success:
            return jsonify({'success': True})
        else:
            return jsonify({
                'error_code': 'UPDATE_FAILED',
                'message': 'Failed to update email',
                'details': {}
            }), 500

    except Exception as e:
        logger.error(f"Failed to update email {email_id}: {e}")
        return jsonify({
            'error_code': 'INTERNAL_ERROR',
            'message': str(e),
            'details': {}
        }), 500


@api_bp.route('/emails/<email_id>/review', methods=['POST'])
@login_required
def mark_email_reviewed(email_id: str):
    """メールをレビュー済みにマーク"""
    try:
        db_client = get_db_client_or_abort()
        user_email = get_current_user_email()
        success = db_client.mark_document_reviewed(email_id, reviewed_by=user_email)

        if success:
            return jsonify({'success': True})
        else:
            return jsonify({
                'error_code': 'UPDATE_FAILED',
                'message': 'Failed to mark as reviewed',
                'details': {}
            }), 500

    except Exception as e:
        logger.error(f"Failed to mark email {email_id} as reviewed: {e}")
        return jsonify({
            'error_code': 'INTERNAL_ERROR',
            'message': str(e),
            'details': {}
        }), 500


@api_bp.route('/emails/<email_id>/unreview', methods=['POST'])
@login_required
def mark_email_unreviewed(email_id: str):
    """メールを未レビューに戻す"""
    try:
        db_client = get_db_client_or_abort()
        success = db_client.mark_document_unreviewed(email_id)

        if success:
            return jsonify({'success': True})
        else:
            return jsonify({
                'error_code': 'UPDATE_FAILED',
                'message': 'Failed to mark as unreviewed',
                'details': {}
            }), 500

    except Exception as e:
        logger.error(f"Failed to mark email {email_id} as unreviewed: {e}")
        return jsonify({
            'error_code': 'INTERNAL_ERROR',
            'message': str(e),
            'details': {}
        }), 500


@api_bp.route('/emails/<email_id>', methods=['DELETE'])
@login_required
def delete_email(email_id: str):
    """メール削除（Gmail + Drive + DB）"""
    try:
        db_client = get_db_client_or_abort()
        email = db_client.get_document_by_id(email_id)

        if not email:
            return jsonify({
                'error_code': 'NOT_FOUND',
                'message': 'Email not found',
                'details': {}
            }), 404

        metadata = email.get('metadata') or {}
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except:
                metadata = {}

        # 1. Gmailのメッセージをゴミ箱に移動
        message_id = metadata.get('message_id')
        if message_id:
            try:
                import os
                from shared.common.connectors.gmail_connector import GmailConnector
                user_email_addr = os.getenv('GMAIL_USER_EMAIL', '')
                if user_email_addr:
                    gmail_connector = GmailConnector(user_email_addr)
                    gmail_connector.trash_message(message_id)
            except Exception as e:
                logger.warning(f"Failed to trash Gmail message: {e}")

        # 2. Google DriveからHTMLファイルを削除
        file_id = email.get('source_id')
        if file_id:
            try:
                from shared.common.connectors.google_drive import GoogleDriveConnector
                drive_connector = GoogleDriveConnector()
                drive_connector.trash_file(file_id)
            except Exception as e:
                logger.warning(f"Failed to trash Drive file: {e}")

        # 3. DBから削除
        success = db_client.delete_document(email_id)

        if success:
            return jsonify({'success': True})
        else:
            return jsonify({
                'error_code': 'DELETE_FAILED',
                'message': 'Failed to delete email',
                'details': {}
            }), 500

    except Exception as e:
        logger.error(f"Failed to delete email {email_id}: {e}")
        return jsonify({
            'error_code': 'INTERNAL_ERROR',
            'message': str(e),
            'details': {}
        }), 500


@api_bp.route('/emails/bulk-approve', methods=['POST'])
@login_required
def bulk_approve_emails():
    """メール一括承認"""
    try:
        db_client = get_db_client_or_abort()
        user_email = get_current_user_email()

        data = request.get_json()
        email_ids = data.get('email_ids', [])

        if not email_ids:
            return jsonify({
                'error_code': 'BAD_REQUEST',
                'message': 'email_ids required',
                'details': {}
            }), 400

        success_count = 0
        fail_count = 0

        for email_id in email_ids:
            if db_client.mark_document_reviewed(email_id, reviewed_by=user_email):
                success_count += 1
            else:
                fail_count += 1

        return jsonify({
            'success': True,
            'success_count': success_count,
            'fail_count': fail_count
        })

    except Exception as e:
        logger.error(f"Failed to bulk approve emails: {e}")
        return jsonify({
            'error_code': 'INTERNAL_ERROR',
            'message': str(e),
            'details': {}
        }), 500


@api_bp.route('/emails/bulk-delete', methods=['POST'])
@login_required
def bulk_delete_emails():
    """メール一括削除"""
    try:
        db_client = get_db_client_or_abort()

        data = request.get_json()
        email_ids = data.get('email_ids', [])

        if not email_ids:
            return jsonify({
                'error_code': 'BAD_REQUEST',
                'message': 'email_ids required',
                'details': {}
            }), 400

        import os
        from shared.common.connectors.google_drive import GoogleDriveConnector
        from shared.common.connectors.gmail_connector import GmailConnector

        drive_connector = GoogleDriveConnector()
        user_email_addr = os.getenv('GMAIL_USER_EMAIL', '')
        gmail_connector = GmailConnector(user_email_addr) if user_email_addr else None

        success_count = 0
        fail_count = 0

        for email_id in email_ids:
            try:
                email = db_client.get_document_by_id(email_id)
                if email:
                    metadata = email.get('metadata') or {}
                    if isinstance(metadata, str):
                        try:
                            metadata = json.loads(metadata)
                        except:
                            metadata = {}

                    # Gmail削除
                    message_id = metadata.get('message_id')
                    if message_id and gmail_connector:
                        try:
                            gmail_connector.trash_message(message_id)
                        except:
                            pass

                    # Drive削除
                    file_id = email.get('source_id')
                    if file_id:
                        try:
                            drive_connector.trash_file(file_id)
                        except:
                            pass

                    # DB削除
                    if db_client.delete_document(email_id):
                        success_count += 1
                    else:
                        fail_count += 1
                else:
                    fail_count += 1
            except:
                fail_count += 1

        return jsonify({
            'success': True,
            'success_count': success_count,
            'fail_count': fail_count
        })

    except Exception as e:
        logger.error(f"Failed to bulk delete emails: {e}")
        return jsonify({
            'error_code': 'INTERNAL_ERROR',
            'message': str(e),
            'details': {}
        }), 500
