"""
Authentication Service
Flask session + Supabase Auth による認証管理

設計方針:
- Flask sessionにaccess_tokenとuser_emailを保持
- DatabaseClientはリクエスト毎に生成
- service_roleは絶対に使用しない
- session消失時は再ログインでOK（refresh_token管理は不要）
"""
import os
from functools import wraps
from typing import Optional, Tuple

from flask import session, g, jsonify, request
from loguru import logger
from supabase import create_client, Client


class AuthService:
    """認証サービス"""

    def __init__(self):
        """Supabaseクライアント初期化（anon keyで）"""
        self.supabase_url = os.environ.get('SUPABASE_URL')
        self.supabase_key = os.environ.get('SUPABASE_KEY')  # anon key

        if not self.supabase_url or not self.supabase_key:
            logger.warning("Supabase credentials not configured")

    def _get_anon_client(self) -> Optional[Client]:
        """匿名クライアント取得（認証処理用）"""
        if not self.supabase_url or not self.supabase_key:
            return None
        return create_client(self.supabase_url, self.supabase_key)

    def login(self, email: str, password: str) -> Tuple[bool, Optional[str]]:
        """
        ログイン処理

        Args:
            email: メールアドレス
            password: パスワード

        Returns:
            (成功フラグ, エラーメッセージ)
        """
        try:
            client = self._get_anon_client()
            if not client:
                return False, "Supabase not configured"

            # Supabase Authでログイン
            response = client.auth.sign_in_with_password({
                "email": email,
                "password": password
            })

            if response.user and response.session:
                # sessionに保存
                session['access_token'] = response.session.access_token
                session['user_email'] = response.user.email
                session['user_id'] = response.user.id

                logger.info(f"Login successful: {email}")
                return True, None
            else:
                return False, "Invalid credentials"

        except Exception as e:
            logger.error(f"Login error: {e}")
            error_msg = str(e)
            if "Invalid login credentials" in error_msg:
                return False, "メールアドレスまたはパスワードが間違っています"
            return False, f"ログインエラー: {error_msg}"

    def logout(self) -> bool:
        """
        ログアウト処理

        Returns:
            成功フラグ
        """
        try:
            # sessionをクリア
            session.pop('access_token', None)
            session.pop('user_email', None)
            session.pop('user_id', None)

            logger.info("Logout successful")
            return True

        except Exception as e:
            logger.error(f"Logout error: {e}")
            return False

    def get_session_info(self) -> dict:
        """
        現在のセッション情報を取得

        Returns:
            セッション情報辞書
        """
        # 開発時：service_role keyがあれば認証済み
        if os.environ.get('SUPABASE_SERVICE_ROLE_KEY'):
            return {
                'is_authenticated': True,
                'user_email': 'dev@local',
                'user_id': None
            }

        return {
            'is_authenticated': session.get('access_token') is not None,
            'user_email': session.get('user_email'),
            'user_id': session.get('user_id')
        }

    def get_db_client(self):
        """
        DatabaseClientを取得（開発時はservice_role使用）

        Returns:
            DatabaseClient or None

        注意:
            - リクエスト毎に新規生成
            - 開発時はservice_roleを使用（RLSバイパス）
        """
        try:
            from shared.common.database.client import DatabaseClient
            # 開発用：service_roleで接続
            return DatabaseClient(use_service_role=True)

        except Exception as e:
            logger.error(f"Failed to create DatabaseClient: {e}")
            return None


def auto_login_from_env() -> bool:
    """
    環境変数からSUPABASE_KEYを取得してセッションに設定（開発用）

    環境変数:
        SUPABASE_KEY: Supabaseのキー（既存の設定を使用）

    Returns:
        成功フラグ
    """
    if session.get('access_token'):
        return True  # 既にログイン済み

    supabase_key = os.environ.get('SUPABASE_KEY')

    if not supabase_key:
        logger.warning("SUPABASE_KEY not set in .env")
        return False

    # セッションにキーを設定
    session['access_token'] = supabase_key
    session['user_email'] = 'dev@local'
    logger.info("Auto login with SUPABASE_KEY")
    return True


def login_required(f):
    """
    認証必須デコレータ

    開発時: SUPABASE_SERVICE_ROLE_KEYがあれば認証スキップ
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # 開発用：service_role keyがあれば認証OK
        if os.environ.get('SUPABASE_SERVICE_ROLE_KEY'):
            return f(*args, **kwargs)

        if not session.get('access_token'):
            return jsonify({
                'error_code': 'UNAUTHORIZED',
                'message': 'Authentication required',
                'details': {}
            }), 401
        return f(*args, **kwargs)
    return decorated_function


def get_current_user_email() -> Optional[str]:
    """
    現在のユーザーのメールアドレスを取得

    監査ログ用に使用（corrector_emailにNoneを許可しない）

    Returns:
        user_email or None
    """
    return session.get('user_email')


def get_db_client_or_abort():
    """
    DatabaseClientを取得、失敗時は401エラー

    Returns:
        DatabaseClient

    Raises:
        401 Unauthorized
    """
    from flask import abort

    auth_service = AuthService()
    db_client = auth_service.get_db_client()

    if not db_client:
        abort(401, description="Authentication required")

    return db_client


# シングルトンインスタンス
auth_service = AuthService()
