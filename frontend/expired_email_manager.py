#!/usr/bin/env python
"""
期限切れメール管理UI
ベクトル検索で期限切れメールを検出して、選択的に削除できるStreamlitアプリ
"""
import sys
import os
from pathlib import Path

# プロジェクトのルートディレクトリをPythonパスに追加
root_dir = Path(__file__).parent.parent
sys.path.insert(0, str(root_dir))

import streamlit as st
from datetime import datetime, timedelta
import re
from typing import List, Dict, Any
from dotenv import load_dotenv

from A_common.database.client import DatabaseClient
from A_common.connectors.google_drive import GoogleDriveConnector
from A_common.connectors.gmail_connector import GmailConnector
from C_ai_common.llm_client.llm_client import LLMClient

load_dotenv()

# ページ設定
st.set_page_config(
    page_title="期限切れメール管理",
    page_icon="📧",
    layout="wide"
)

# 期限関連のキーワード
EXPIRATION_KEYWORDS = [
    "セール 終了",
    "配送期限",
    "注文期限",
    "有効期限",
    "締切日",
    "まもなく終了",
    "本日最終日",
    "本日限定",
    "今日まで",
    "キャンペーン 終了"
]


@st.cache_data(ttl=300)  # 5分間キャッシュ
def extract_dates_from_text(text: str, title: str = "") -> List[datetime]:
    """テキストから日付を抽出"""
    dates = []
    now = datetime.now()
    combined_text = f"{title} {text}"

    # パターン1: YYYY年MM月DD日
    pattern1 = r'(\d{4})年(\d{1,2})月(\d{1,2})日'
    for match in re.finditer(pattern1, combined_text):
        try:
            year = int(match.group(1))
            month = int(match.group(2))
            day = int(match.group(3))
            date = datetime(year, month, day, 23, 59, 59)
            dates.append(date)
        except ValueError:
            continue

    # パターン2: MM月DD日（年なし）
    pattern2 = r'(\d{1,2})月(\d{1,2})日'
    for match in re.finditer(pattern2, combined_text):
        try:
            month = int(match.group(1))
            day = int(match.group(2))
            year = now.year
            date = datetime(year, month, day, 23, 59, 59)
            if date < now:
                date = datetime(year + 1, month, day, 23, 59, 59)
            dates.append(date)
        except ValueError:
            continue

    # パターン3: MM/DD
    pattern3 = r'(\d{1,2})/(\d{1,2})'
    for match in re.finditer(pattern3, combined_text):
        try:
            month = int(match.group(1))
            day = int(match.group(2))
            year = now.year
            date = datetime(year, month, day, 23, 59, 59)
            if date < now:
                date = datetime(year + 1, month, day, 23, 59, 59)
            dates.append(date)
        except ValueError:
            continue

    # パターン4: YYYY-MM-DD
    pattern4 = r'(\d{4})-(\d{1,2})-(\d{1,2})'
    for match in re.finditer(pattern4, combined_text):
        try:
            year = int(match.group(1))
            month = int(match.group(2))
            day = int(match.group(3))
            date = datetime(year, month, day, 23, 59, 59)
            dates.append(date)
        except ValueError:
            continue

    return dates


def find_expired_emails(grace_days: int = 0, progress_callback=None) -> List[Dict[str, Any]]:
    """ベクトル検索で期限切れメールを検出"""
    db_client = DatabaseClient()
    llm_client = LLMClient()

    expired_emails = []
    seen_ids = set()
    now = datetime.now()

    total_keywords = len(EXPIRATION_KEYWORDS)

    for i, keyword in enumerate(EXPIRATION_KEYWORDS):
        if progress_callback:
            progress_callback(i / total_keywords, f"検索中: {keyword}")

        try:
            embedding = llm_client.generate_embedding(keyword)
            results = db_client.search_documents_sync(
                keyword,
                embedding,
                limit=50,
                doc_types=['DM-mail']
            )

            for doc in results:
                doc_id = doc.get('id')
                if doc_id in seen_ids:
                    continue

                title = doc.get('file_name', '') or doc.get('title', '')
                content = ""
                all_chunks = doc.get('all_chunks', [])
                if all_chunks:
                    chunk_contents = [chunk.get('chunk_content', '') for chunk in all_chunks]
                    content = '\n'.join(chunk_contents)
                else:
                    content = doc.get('content', '') or doc.get('summary', '') or doc.get('attachment_text', '')

                dates = extract_dates_from_text(content, title)
                if not dates:
                    continue

                expiration_date = min(dates)
                grace = timedelta(days=grace_days)

                if (expiration_date + grace) < now:
                    doc['expiration_date'] = expiration_date
                    doc['search_keyword'] = keyword
                    doc['content_preview'] = content[:500]  # プレビュー用
                    expired_emails.append(doc)
                    seen_ids.add(doc_id)

        except Exception as e:
            st.error(f"検索エラー ({keyword}): {e}")
            continue

    if progress_callback:
        progress_callback(1.0, "検索完了")

    expired_emails.sort(key=lambda x: x.get('expiration_date', datetime.max))
    return expired_emails


def delete_email(email: Dict[str, Any]) -> bool:
    """メールを削除"""
    try:
        db_client = DatabaseClient()
        drive_connector = GoogleDriveConnector()
        user_email = os.getenv('GMAIL_USER_EMAIL', 'ookubo.y@workspace-o.com')
        gmail_connector = GmailConnector(user_email)

        email_id = email['id']
        source_id = email.get('source_id')
        metadata = email.get('metadata', {})

        if isinstance(metadata, str):
            import json
            try:
                metadata = json.loads(metadata)
            except:
                metadata = {}

        success = True

        # 1. Gmail
        message_id = metadata.get('message_id')
        if message_id:
            try:
                gmail_connector.trash_message(message_id)
            except Exception as e:
                st.warning(f"Gmail削除エラー: {e}")
                success = False

        # 2. Google Drive
        if source_id:
            try:
                drive_connector.trash_file(source_id)
            except Exception as e:
                st.warning(f"Drive削除エラー: {e}")
                success = False

        # 3. Database
        if not db_client.delete_document(email_id):
            st.error("データベース削除失敗")
            return False

        return success

    except Exception as e:
        st.error(f"削除エラー: {e}")
        return False


# メイン画面
st.title("📧 期限切れメール管理")
st.markdown("---")

# サイドバー設定
with st.sidebar:
    st.header("⚙️ 設定")
    grace_days = st.number_input(
        "猶予日数",
        min_value=0,
        max_value=30,
        value=0,
        help="期限から何日後まで削除対象外にするか"
    )

    st.markdown("---")
    st.markdown("### 検索キーワード")
    st.markdown("\n".join([f"- {kw}" for kw in EXPIRATION_KEYWORDS]))

# 検索ボタン
if st.button("🔍 期限切れメールを検索", type="primary", use_container_width=True):
    with st.spinner("検索中..."):
        progress_bar = st.progress(0)
        status_text = st.empty()

        def update_progress(value, text):
            progress_bar.progress(value)
            status_text.text(text)

        expired_emails = find_expired_emails(grace_days, update_progress)
        st.session_state['expired_emails'] = expired_emails

        progress_bar.empty()
        status_text.empty()

        if expired_emails:
            st.success(f"✅ {len(expired_emails)}件の期限切れメールを発見しました")
        else:
            st.info("期限切れメールはありません")

# 検索結果の表示
if 'expired_emails' in st.session_state:
    expired_emails = st.session_state['expired_emails']

    if expired_emails:
        st.markdown("---")
        st.subheader(f"📋 期限切れメール一覧 ({len(expired_emails)}件)")

        # 全選択/全解除
        col1, col2 = st.columns([1, 5])
        with col1:
            select_all = st.checkbox("全選択", value=False)

        # 選択されたメールID
        if 'selected_emails' not in st.session_state:
            st.session_state['selected_emails'] = set()

        if select_all:
            st.session_state['selected_emails'] = {email['id'] for email in expired_emails}
        elif not select_all and len(st.session_state['selected_emails']) == len(expired_emails):
            st.session_state['selected_emails'] = set()

        # メール一覧
        for i, email in enumerate(expired_emails):
            email_id = email['id']
            title = email.get('file_name', '') or email.get('title', '(タイトルなし)')
            expiration = email.get('expiration_date')
            keyword = email.get('search_keyword', '')
            content_preview = email.get('content_preview', '')

            exp_str = expiration.strftime('%Y年%m月%d日') if expiration else '不明'

            with st.expander(f"{'✅' if email_id in st.session_state['selected_emails'] else '⬜'} {title[:80]}", expanded=False):
                col1, col2 = st.columns([1, 5])

                with col1:
                    selected = st.checkbox(
                        "削除対象",
                        key=f"checkbox_{email_id}",
                        value=email_id in st.session_state['selected_emails']
                    )
                    if selected:
                        st.session_state['selected_emails'].add(email_id)
                    else:
                        st.session_state['selected_emails'].discard(email_id)

                with col2:
                    st.markdown(f"**期限日:** {exp_str}")
                    st.markdown(f"**検索キーワード:** {keyword}")

                    # 日付を含む行を抽出して表示
                    lines_with_dates = [
                        line for line in content_preview.split('\n')
                        if re.search(r'\d{4}年|\d{1,2}月\d{1,2}日|\d{1,2}/\d{1,2}', line)
                    ]
                    if lines_with_dates:
                        st.markdown("**本文抜粋:**")
                        for line in lines_with_dates[:3]:
                            st.text(line[:150])

        # 削除ボタン
        st.markdown("---")
        selected_count = len(st.session_state['selected_emails'])

        if selected_count > 0:
            col1, col2, col3 = st.columns([2, 2, 2])

            with col2:
                if st.button(
                    f"🗑️ 選択した {selected_count} 件を削除",
                    type="primary",
                    use_container_width=True
                ):
                    st.session_state['confirm_delete'] = True

            # 削除確認
            if st.session_state.get('confirm_delete', False):
                st.warning(f"⚠️ 本当に {selected_count} 件のメールを削除しますか？")
                col1, col2, col3 = st.columns([2, 2, 2])

                with col1:
                    if st.button("✅ はい、削除します", type="primary", use_container_width=True):
                        progress_bar = st.progress(0)
                        success_count = 0
                        fail_count = 0

                        emails_to_delete = [
                            email for email in expired_emails
                            if email['id'] in st.session_state['selected_emails']
                        ]

                        for i, email in enumerate(emails_to_delete):
                            progress_bar.progress((i + 1) / len(emails_to_delete))
                            if delete_email(email):
                                success_count += 1
                            else:
                                fail_count += 1

                        progress_bar.empty()

                        if fail_count == 0:
                            st.success(f"✅ {success_count}件のメールを削除しました")
                        else:
                            st.warning(f"⚠️ 完了: 成功={success_count}, 失敗={fail_count}")

                        # リセット
                        st.session_state['confirm_delete'] = False
                        st.session_state['selected_emails'] = set()
                        if st.button("🔄 再検索", use_container_width=True):
                            st.rerun()

                with col2:
                    if st.button("❌ キャンセル", use_container_width=True):
                        st.session_state['confirm_delete'] = False
                        st.rerun()
        else:
            st.info("削除するメールを選択してください")
