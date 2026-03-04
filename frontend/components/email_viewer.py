"""
Email Viewer Component

メール専用の表示コンポーネント
- メール一覧（表形式）
- メール詳細表示（メールらしい見た目）
"""
import streamlit as st
from datetime import datetime
from typing import Dict, List, Any, Optional
import pandas as pd


def render_email_list(emails: List[Dict[str, Any]]) -> tuple[Optional[int], pd.DataFrame]:
    """
    メール一覧を表形式で表示（チェックボックス付き）

    Args:
        emails: メールドキュメントのリスト

    Returns:
        選択されたメールのインデックス（None の場合は未選択）と編集されたDataFrame
    """
    st.subheader("📬 受信メール一覧")

    if not emails:
        st.info("メールがありません")
        return None, None

    # メールのDataFrameを作成（チェックボックス付き）
    df_data = []
    for email in emails:
        meta = email.get('meta', {})

        sender       = email.get('from_name',  meta.get('from', '送信者不明'))
        sender_email = email.get('from_email', '')
        subject      = email.get('title',      meta.get('subject', '(件名なし)'))
        date_str     = email.get('post_at',    meta.get('date', ''))

        # 送信者名とメールアドレスを表示用に整形
        if sender_email and sender:
            sender_display = f"{sender} ({sender_email})"
        elif sender_email:
            sender_display = sender_email
        elif sender and '<' in sender and '>' in sender:
            # metadata.fromの場合の後方互換性: "名前 <email>" の形式から名前だけを取得
            sender_display = sender.split('<')[0].strip().strip('"')
        else:
            sender_display = sender

        # 日付をフォーマット
        try:
            display_date = date_str[:10] if date_str else ""
        except:
            display_date = date_str

        df_data.append({
            '選択': False,  # チェックボックス用
            '件名': subject,
            '送信者': sender_display,
            '送信日時': display_date,
            '送信者メール': sender_email  # CSVエクスポート用に追加
        })

    df = pd.DataFrame(df_data)

    # データエディタでチェックボックス付きの表を表示
    edited_df = st.data_editor(
        df,
        use_container_width=True,
        height=200,
        hide_index=True,
        column_config={
            "選択": st.column_config.CheckboxColumn(
                "選択",
                help="削除するメールを選択",
                default=False,
            ),
            "送信者メール": st.column_config.TextColumn(
                "送信者メールアドレス",
                help="送信者のメールアドレス",
            )
        },
        disabled=["件名", "送信者", "送信日時", "送信者メール"],
        key="email_list_editor"
    )

    # セレクトボックスでメールを選択
    selected_index = st.selectbox(
        "表示するメールを選択",
        range(len(emails)),
        format_func=lambda i: f"{df_data[i]['件名']} ({df_data[i]['送信者']})",
        key="email_selector"
    )

    return selected_index, edited_df


def render_email_detail(email: Dict[str, Any]):
    """
    メール詳細をタブ形式で表示（PDFレビューと同じスタイル）

    Args:
        email: メールドキュメント（09_unified_documents スキーマ）
    """
    meta     = email.get('meta', {})
    ui_data  = email.get('ui_data', {})
    sections = ui_data.get('sections', []) if isinstance(ui_data, dict) else []

    # 本文 = ui_data.sections[*].body を結合
    body_text = '\n\n'.join(
        s.get('body', '') for s in sections if s.get('body')
    ).strip()

    # デバッグ: データソースを確認
    with st.expander("🔍 データソース確認", expanded=False):
        st.markdown("**snippet:**")
        st.code(str(email.get('snippet', '')) or "なし")
        st.markdown("**ui_data.sections (先頭2件):**")
        st.json(sections[:2])
        st.markdown("**meta:**")
        st.json(meta)

    st.markdown("### ✏️ メール情報")

    tab1, tab2, tab3, tab4 = st.tabs(["📊 要約", "📄 本文", "🔍 重要情報", "⚙️ メタデータ"])

    with tab1:
        st.markdown("#### メール要約")

        # 送信元
        st.markdown("**📤 送信元**")
        from_name  = email.get('from_name',  meta.get('from', '不明'))
        from_email = email.get('from_email', '')
        if from_email and from_name:
            sender_display = f"{from_name} ({from_email})"
        elif from_email:
            sender_display = from_email
        else:
            sender_display = from_name
        st.info(sender_display)

        # 宛先
        st.markdown("**📥 宛先**")
        st.info(meta.get('to', '不明'))

        # 送信日
        st.markdown("**📅 送信日**")
        st.info(email.get('post_at', meta.get('date', '不明')))

        # インデックス日時
        st.markdown("**📩 インデックス日時**")
        indexed_at = email.get('indexed_at', '不明')
        if indexed_at and indexed_at != '不明':
            try:
                dt = datetime.fromisoformat(str(indexed_at).replace('Z', '+00:00'))
                indexed_at = dt.strftime('%Y-%m-%d %H:%M:%S')
            except Exception:
                pass
        st.info(indexed_at)

        # 本文要約
        st.markdown("**📝 本文要約**")
        snippet = email.get('snippet', '')
        if snippet:
            st.info(snippet)
        elif body_text:
            st.info(body_text[:500])
        else:
            st.info("要約がありません")

    with tab2:
        st.markdown("#### メール本文（全文）")

        if body_text:
            st.text_area("", body_text, height=500, label_visibility="collapsed", key="email_body_text")
        else:
            st.warning("本文が見つかりません")
            with st.expander("🔍 デバッグ情報", expanded=False):
                st.markdown("**emailのキー:**")
                st.code(str(list(email.keys())))
                st.markdown("**ui_data:**")
                st.json(ui_data)

    with tab3:
        st.markdown("#### 重要な情報")

        key_info = meta.get('key_information', [])
        if key_info and isinstance(key_info, list):
            for i, info in enumerate(key_info, 1):
                st.markdown(f"{i}. {info}")
        else:
            st.info("重要な情報が抽出されていません")

        links = meta.get('links', [])
        if links:
            st.markdown("---")
            st.markdown("#### 🔗 リンク")
            if len(links) > 5:
                with st.expander(f"リンク一覧 ({len(links)}件)", expanded=False):
                    for i, link in enumerate(links, 1):
                        if str(link).startswith('http'):
                            st.markdown(f"{i}. [{link}]({link})")
                        else:
                            st.markdown(f"{i}. {link}")
            else:
                for i, link in enumerate(links, 1):
                    if str(link).startswith('http'):
                        st.markdown(f"{i}. [{link}]({link})")
                    else:
                        st.markdown(f"{i}. {link}")

    with tab4:
        st.markdown("#### メタデータ")

        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**送信者**")
            st.code(meta.get('from', email.get('from_email', '不明')), language=None)
            st.markdown("**宛先**")
            st.code(meta.get('to', '不明'), language=None)
        with col2:
            st.markdown("**件名**")
            st.code(email.get('title', '(件名なし)'), language=None)
            st.markdown("**送信日時**")
            st.code(meta.get('date', email.get('post_at', '不明')), language=None)

        st.markdown("**Person**")
        st.code(email.get('person', 'unknown'), language=None)

        gmail_label = meta.get('gmail_label')
        if gmail_label:
            st.markdown("**Gmail Label**")
            st.code(gmail_label, language=None)

        with st.expander("🔍 完全なメタデータ（JSON）", expanded=False):
            st.json(meta)

    # file_url があればリンクボタンを表示
    st.divider()
    file_url = email.get('file_url')
    if file_url:
        st.link_button("👁️ ファイルを開く", file_url, use_container_width=True)


def render_email_html_preview(email: Dict[str, Any], drive_connector=None):
    """
    メールのHTMLプレビューを表示

    Args:
        email: メールドキュメント（09_unified_documents スキーマ）
        drive_connector: 未使用（後方互換のため残す）
    """
    st.markdown("### 📧 メールプレビュー")

    if not email:
        st.warning("メールデータが見つかりません")
        return

    file_url = email.get('file_url')
    if not file_url:
        st.info("プレビュー可能なファイルがありません")
        return

    import re
    # Google Drive URL から file_id を抽出（あれば）
    match = re.search(r'/d/([^/?#]+)', file_url)
    if match:
        drive_file_id = match.group(1)
        col1, col2 = st.columns(2)
        with col1:
            st.link_button(
                "📥 元のファイルをダウンロード",
                f"https://drive.google.com/uc?export=download&id={drive_file_id}",
                use_container_width=True
            )
        with col2:
            st.link_button(
                "👁️ Google Driveで表示",
                file_url,
                use_container_width=True
            )
    else:
        st.link_button("👁️ ファイルを開く", file_url, use_container_width=True)


def render_email_filters() -> Dict[str, Any]:
    """
    メールフィルター（person, 期間など）

    Returns:
        フィルター条件の辞書
    """
    st.sidebar.markdown("### 🔍 メールフィルター")

    filters = {}

    # person フィルター
    person_options = [
        "すべて",
        "DM_MAIL",
        "WORK_MAIL",
        "IKUYA_MAIL",
        "EMA_MAIL",
        "MONEY_MAIL",
        "JOB_MAIL",
    ]
    selected_person = st.sidebar.selectbox(
        "Person",
        person_options
    )
    if selected_person != "すべて":
        filters['person'] = selected_person

    # 期間フィルター
    date_range = st.sidebar.radio(
        "期間",
        ["すべて", "今日", "今週", "今月", "カスタム"]
    )

    if date_range == "カスタム":
        col1, col2 = st.sidebar.columns(2)
        with col1:
            start_date = st.date_input("開始日")
            filters['start_date'] = start_date
        with col2:
            end_date = st.date_input("終了日")
            filters['end_date'] = end_date

    # 検索キーワード
    keyword = st.sidebar.text_input("🔎 キーワード検索")
    if keyword:
        filters['keyword'] = keyword

    return filters
