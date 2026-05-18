"""
Admin UI 認証モジュール
Supabase Auth を使用した認証機能を提供

ローカル環境向けの暫定認証として、メール/パスワード認証を使用。
将来的には OAuth (Google) への移行も可能。
"""
import os
from typing import Optional, Tuple
from dataclasses import dataclass
from supabase import create_client, Client
from gotrue.errors import AuthApiError
from dms.common.config.settings import settings


@dataclass
class AuthSession:
    """認証セッション情報"""
    access_token: str
    refresh_token: str
    user_id: str
    email: str
    expires_at: int


class AdminAuthManager:
    """Admin UI 認証マネージャー"""

    def __init__(self):
        """初期化 - anon key で Supabase クライアントを作成"""
        if not settings.SUPABASE_URL:
            raise ValueError("SUPABASE_URL が設定されていません")
        if not settings.SUPABASE_KEY:
            raise ValueError("SUPABASE_KEY が設定されていません")

        self._client: Client = create_client(
            settings.SUPABASE_URL,
            settings.SUPABASE_KEY  # anon key を使用
        )
        self._session: Optional[AuthSession] = None

    def sign_in_with_password(self, email: str, password: str) -> Tuple[bool, str]:
        """
        メール/パスワードでサインイン

        Args:
            email: メールアドレス
            password: パスワード

        Returns:
            (成功フラグ, エラーメッセージ or 成功メッセージ)
        """
        try:
            response = self._client.auth.sign_in_with_password({
                "email": email,
                "password": password
            })

            if response.session:
                self._session = AuthSession(
                    access_token=response.session.access_token,
                    refresh_token=response.session.refresh_token,
                    user_id=response.user.id,
                    email=response.user.email,
                    expires_at=response.session.expires_at
                )
                return True, f"ログイン成功: {email}"
            else:
                return False, "認証に失敗しました"

        except AuthApiError as e:
            error_msg = str(e)
            if "Invalid login credentials" in error_msg:
                return False, "メールアドレスまたはパスワードが正しくありません"
            elif "Email not confirmed" in error_msg:
                return False, "メールアドレスが未確認です"
            else:
                return False, f"認証エラー: {error_msg}"
        except Exception as e:
            return False, f"予期しないエラー: {str(e)}"

    def sign_out(self) -> Tuple[bool, str]:
        """
        サインアウト

        Returns:
            (成功フラグ, メッセージ)
        """
        try:
            self._client.auth.sign_out()
            self._session = None
            return True, "ログアウトしました"
        except Exception as e:
            return False, f"ログアウトエラー: {str(e)}"

    def refresh_session(self) -> bool:
        """
        セッションをリフレッシュ

        Returns:
            成功フラグ
        """
        if not self._session:
            return False

        try:
            response = self._client.auth.refresh_session()
            if response.session:
                self._session = AuthSession(
                    access_token=response.session.access_token,
                    refresh_token=response.session.refresh_token,
                    user_id=response.user.id,
                    email=response.user.email,
                    expires_at=response.session.expires_at
                )
                return True
            return False
        except Exception:
            self._session = None
            return False

    @property
    def is_authenticated(self) -> bool:
        """認証済みかどうか"""
        return self._session is not None

    @property
    def session(self) -> Optional[AuthSession]:
        """現在のセッション"""
        return self._session

    @property
    def access_token(self) -> Optional[str]:
        """アクセストークン"""
        return self._session.access_token if self._session else None

    @property
    def user_email(self) -> Optional[str]:
        """ユーザーのメールアドレス"""
        return self._session.email if self._session else None

    def get_authenticated_client(self) -> Optional[Client]:
        """
        認証済みの Supabase クライアントを取得

        認証されていない場合は None を返す

        Returns:
            認証済み Supabase クライアント or None
        """
        if not self._session:
            return None

        # 認証済みセッションでクライアントを作成
        client = create_client(
            settings.SUPABASE_URL,
            settings.SUPABASE_KEY,
            options={
                "headers": {
                    "Authorization": f"Bearer {self._session.access_token}"
                }
            }
        )
        return client


def create_streamlit_auth_ui():
    """
    Streamlit 用の認証 UI を生成

    Returns:
        (AdminAuthManager, bool) - (認証マネージャー, 認証済みフラグ)
    """
    import streamlit as st

    # セッション状態で認証マネージャーを管理
    if 'auth_manager' not in st.session_state:
        st.session_state.auth_manager = AdminAuthManager()

    auth_manager = st.session_state.auth_manager

    # 既に認証済みの場合
    if auth_manager.is_authenticated:
        return auth_manager, True

    # ログインフォーム
    st.sidebar.markdown("---")
    st.sidebar.subheader("🔐 管理者ログイン")

    with st.sidebar.form("login_form"):
        email = st.text_input("メールアドレス", key="login_email")
        password = st.text_input("パスワード", type="password", key="login_password")
        submitted = st.form_submit_button("ログイン")

        if submitted:
            if email and password:
                success, message = auth_manager.sign_in_with_password(email, password)
                if success:
                    st.success(message)
                    st.rerun()
                else:
                    st.error(message)
            else:
                st.warning("メールアドレスとパスワードを入力してください")

    return auth_manager, False


def create_logout_button():
    """
    ログアウトボタンを表示
    """
    import streamlit as st

    if 'auth_manager' not in st.session_state:
        return

    auth_manager = st.session_state.auth_manager

    if auth_manager.is_authenticated:
        st.sidebar.markdown("---")
        st.sidebar.markdown(f"👤 **{auth_manager.user_email}**")
        if st.sidebar.button("🚪 ログアウト"):
            success, message = auth_manager.sign_out()
            if success:
                st.success(message)
                st.rerun()
            else:
                st.error(message)
