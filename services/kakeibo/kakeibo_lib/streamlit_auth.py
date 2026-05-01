"""
Streamlit + Supabase auth for kakeibo review UI (no shared.common).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional, Tuple

from gotrue.errors import AuthApiError
from supabase import Client, create_client


def _url() -> str:
    u = os.environ.get("SUPABASE_URL", "").strip()
    if not u:
        raise ValueError("SUPABASE_URL is not set")
    return u


def _anon_key() -> str:
    k = os.environ.get("SUPABASE_KEY", "").strip()
    if not k:
        raise ValueError("SUPABASE_KEY is not set")
    return k


@dataclass
class AuthSession:
    access_token: str
    refresh_token: str
    user_id: str
    email: str
    expires_at: int


class AdminAuthManager:
    def __init__(self) -> None:
        self._client: Client = create_client(_url(), _anon_key())
        self._session: Optional[AuthSession] = None

    def sign_in_with_password(self, email: str, password: str) -> Tuple[bool, str]:
        try:
            response = self._client.auth.sign_in_with_password(
                {"email": email, "password": password}
            )
            if response.session:
                self._session = AuthSession(
                    access_token=response.session.access_token,
                    refresh_token=response.session.refresh_token,
                    user_id=response.user.id,
                    email=response.user.email or "",
                    expires_at=response.session.expires_at or 0,
                )
                return True, "OK: " + email
            return False, "Auth failed"
        except AuthApiError as e:
            error_msg = str(e)
            if "Invalid login credentials" in error_msg:
                return False, "Invalid email or password"
            if "Email not confirmed" in error_msg:
                return False, "Email not confirmed"
            return False, "Auth error: " + error_msg
        except Exception as e:
            return False, "Error: " + str(e)

    def sign_out(self) -> Tuple[bool, str]:
        try:
            self._client.auth.sign_out()
            self._session = None
            return True, "Signed out"
        except Exception as e:
            return False, "Sign out error: " + str(e)

    def refresh_session(self) -> bool:
        if not self._session:
            return False
        try:
            response = self._client.auth.refresh_session()
            if response.session:
                self._session = AuthSession(
                    access_token=response.session.access_token,
                    refresh_token=response.session.refresh_token,
                    user_id=response.user.id,
                    email=response.user.email or "",
                    expires_at=response.session.expires_at or 0,
                )
                return True
            return False
        except Exception:
            self._session = None
            return False

    @property
    def is_authenticated(self) -> bool:
        return self._session is not None

    @property
    def session(self) -> Optional[AuthSession]:
        return self._session

    @property
    def access_token(self) -> Optional[str]:
        return self._session.access_token if self._session else None

    @property
    def user_email(self) -> Optional[str]:
        return self._session.email if self._session else None

    def get_authenticated_client(self) -> Optional[Client]:
        if not self._session:
            return None
        return create_client(
            _url(),
            _anon_key(),
            options={"headers": {"Authorization": "Bearer " + self._session.access_token}},
        )


def create_streamlit_auth_ui():
    import streamlit as st

    if "auth_manager" not in st.session_state:
        st.session_state.auth_manager = AdminAuthManager()
    auth_manager = st.session_state.auth_manager
    if auth_manager.is_authenticated:
        return auth_manager, True

    st.sidebar.markdown("---")
    st.sidebar.subheader("Admin login")
    with st.sidebar.form("login_form"):
        email = st.text_input("Email", key="login_email")
        password = st.text_input("Password", type="password", key="login_password")
        submitted = st.form_submit_button("Login")
        if submitted:
            if email and password:
                success, message = auth_manager.sign_in_with_password(email, password)
                if success:
                    st.success(message)
                    st.rerun()
                else:
                    st.error(message)
            else:
                st.warning("Enter email and password")
    return auth_manager, False


def create_logout_button() -> None:
    import streamlit as st

    if "auth_manager" not in st.session_state:
        return
    auth_manager = st.session_state.auth_manager
    if auth_manager.is_authenticated:
        st.sidebar.markdown("---")
        st.sidebar.markdown("**" + (auth_manager.user_email or "") + "**")
        if st.sidebar.button("Logout"):
            success, message = auth_manager.sign_out()
            if success:
                st.success(message)
                st.rerun()
            else:
                st.error(message)