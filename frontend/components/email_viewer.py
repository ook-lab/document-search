"""
Email Viewer Component

メール専用の表示コンポーネント
- メール一覧（表形式）
- メール詳細表示（メールらしい見た目）
"""
import streamlit as st
from datetime import datetime
from typing import Dict, List, Any, Optional
import json
import html
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
        metadata = email.get('metadata', {})

        # メールの基本情報を取得 - display_*フィールドを優先的に使用
        # display_*フィールドはGmail取り込み時に正規化されたもの
        sender = email.get('display_sender', metadata.get('from', '送信者不明'))
        sender_email = email.get('display_sender_email', '')
        subject = email.get('display_subject', metadata.get('subject', '(件名なし)'))
        date_str = email.get('display_sent_at', metadata.get('date', ''))

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
        email: メールドキュメント
    """
    metadata = email.get('metadata', {})

    # デバッグ: データソースを確認
    with st.expander("🔍 データソース確認", expanded=False):
        st.markdown("**documents.summary:**")
        doc_summary = email.get('summary', '')
        st.code(str(doc_summary) if doc_summary else "なし")
        st.markdown(f"長さ: {len(str(doc_summary)) if doc_summary else 0} 文字")

        st.markdown("**metadata.summary:**")
        meta_summary = metadata.get('summary', '')
        st.code(str(meta_summary) if meta_summary else "なし")
        st.markdown(f"長さ: {len(str(meta_summary)) if meta_summary else 0} 文字")

        st.markdown("**attachment_text:**")
        attachment_text = email.get('attachment_text', '')
        st.code(str(attachment_text) if attachment_text else "なし")
        st.markdown(f"長さ: {len(str(attachment_text)) if attachment_text else 0} 文字")

    # summaryフィールドからJSONデータを抽出
    # 優先順位: documents.summary > metadata.summary
    email_data = {}
    summary_raw = email.get('summary', metadata.get('summary', ''))

    # JSONパースを試みる
    parse_success = False
    if summary_raw and isinstance(summary_raw, str):
        # ```jsonマーカーを削除
        json_str = summary_raw
        if json_str.startswith('```json'):
            json_str = json_str.replace('```json', '').replace('```', '').strip()
        elif json_str.startswith('```'):
            json_str = json_str.replace('```', '').strip()

        # JSONとしてパース
        if json_str.startswith('{'):
            try:
                email_data = json.loads(json_str)
                parse_success = True
            except json.JSONDecodeError as e:
                # エスケープシーケンスエラーの場合、修正を試みる
                error_msg = str(e)
                if 'escape' in error_msg.lower():
                    try:
                        # 不正なエスケープシーケンスを修正
                        # raw_unicode_escapeでデコードしてから再エンコード
                        import re
                        # バックスラッシュを二重エスケープ
                        fixed_str = json_str.replace('\\', '\\\\')
                        # 正しいエスケープシーケンスを元に戻す
                        fixed_str = fixed_str.replace('\\\\n', '\\n')
                        fixed_str = fixed_str.replace('\\\\t', '\\t')
                        fixed_str = fixed_str.replace('\\\\r', '\\r')
                        fixed_str = fixed_str.replace('\\\\"', '\\"')
                        fixed_str = re.sub(r'\\\\u([0-9a-fA-F]{4})', r'\\u\1', fixed_str)
                        # \\\\ -> \\ (二重バックスラッシュを単一に)
                        fixed_str = fixed_str.replace('\\\\\\\\', '\\\\')

                        email_data = json.loads(fixed_str)
                        parse_success = True
                        st.success("✅ エスケープエラーを修正してデータを読み込みました")
                    except:
                        pass

                # それでも失敗した場合、正規表現で重要フィールドを抽出
                if not parse_success:
                    st.warning(f"⚠️ JSON解析に失敗しました。重要なフィールドのみ抽出します。")
                    import re

                    # デバッグ情報を表示
                    with st.expander("🔍 デバッグ: JSON内容を確認", expanded=False):
                        st.markdown("**元のJSON:**")
                        st.code(json_str)
                        st.markdown("**JSON文字列の長さ:**")
                        st.code(f"{len(json_str)} 文字")

                    # より柔軟な正規表現で抽出
                    # "summary": "..." を抽出（エスケープされた引用符も考慮）
                    summary_match = re.search(r'"summary"\s*:\s*"((?:[^"\\]|\\.)*)"', json_str, re.DOTALL)
                    if summary_match:
                        summary_value = summary_match.group(1)
                        # エスケープシーケンスを復元
                        summary_value = summary_value.replace('\\n', '\n').replace('\\t', '\t').replace('\\"', '"').replace('\\\\', '\\')
                        email_data['summary'] = summary_value
                        st.info(f"✓ 要約を抽出しました（{len(summary_value)}文字）")

                    # "extracted_text": "..." を抽出
                    extracted_match = re.search(r'"extracted_text"\s*:\s*"((?:[^"\\]|\\.)*)"', json_str, re.DOTALL)
                    if extracted_match:
                        extracted_value = extracted_match.group(1)
                        # エスケープシーケンスを復元（最初の3000文字まで）
                        extracted_value = extracted_value[:3000]
                        extracted_value = extracted_value.replace('\\n', '\n').replace('\\t', '\t').replace('\\"', '"').replace('\\\\', '\\')
                        email_data['extracted_text'] = extracted_value
                        st.info(f"✓ 本文を抽出しました（{len(extracted_value)}文字）")

                    # "key_information": [...] を抽出
                    key_info_match = re.search(r'"key_information"\s*:\s*\[(.*?)\]', json_str, re.DOTALL)
                    if key_info_match:
                        try:
                            key_info_str = '[' + key_info_match.group(1) + ']'
                            email_data['key_information'] = json.loads(key_info_str)
                            st.info(f"✓ 重要情報を抽出しました（{len(email_data['key_information'])}件）")
                        except:
                            pass

                    # 抽出できたフィールドを表示
                    with st.expander("📊 抽出できたフィールド", expanded=False):
                        st.json({
                            "summary": bool(email_data.get('summary')),
                            "extracted_text": bool(email_data.get('extracted_text')),
                            "key_information": bool(email_data.get('key_information')),
                            "summary_length": len(email_data.get('summary', '')),
                            "extracted_text_length": len(email_data.get('extracted_text', ''))
                        })

                    if email_data:
                        parse_success = True

    # パースに失敗した場合はmetadataを使用
    if not parse_success or not email_data:
        email_data = metadata.copy() if metadata else {}

        # metadataに直接extracted_textやsummaryがある場合は使用
        # ただし、JSON文字列の場合は除外
        if 'summary' in metadata:
            meta_summary = metadata.get('summary', '')
            if meta_summary and not (isinstance(meta_summary, str) and (meta_summary.startswith('{') or meta_summary.startswith('```'))):
                email_data['summary'] = meta_summary

        # extracted_textがmetadataに直接ある場合
        if 'extracted_text' not in email_data or not email_data.get('extracted_text'):
            # attachment_textをextracted_textとして使用（構造化されていない場合のみ）
            attachment_text = email.get('attachment_text', '')
            if attachment_text and '要約:' not in attachment_text:
                email_data['extracted_text'] = attachment_text

    st.markdown("### ✏️ メール情報")

    # タブで情報を整理（要約を最初に）
    tab1, tab2, tab3, tab4 = st.tabs(["📊 要約", "📄 本文", "🔍 重要情報", "⚙️ メタデータ"])

    with tab1:
        st.markdown("#### メール要約")

        # 送信元 - display_*フィールドを優先的に使用
        st.markdown("**📤 送信元**")
        sender = email.get('display_sender', metadata.get('from', '不明'))
        sender_email = email.get('display_sender_email', '')
        # 送信者名とメールアドレスを整形
        if sender_email and sender:
            sender_display = f"{sender} ({sender_email})"
        elif sender_email:
            sender_display = sender_email
        elif sender and '<' in sender and '>' in sender:
            # metadata.fromの後方互換性
            sender_display = sender.split('<')[0].strip().strip('"')
            sender_email = sender.split('<')[1].split('>')[0]
            sender_display = f"{sender_display} ({sender_email})"
        else:
            sender_display = sender
        st.info(sender_display)

        # 宛先
        st.markdown("**📥 宛先**")
        recipient = metadata.get('to', '不明')
        st.info(recipient)

        # 送信日 - display_sent_atを優先的に使用
        st.markdown("**📅 送信日**")
        send_date = email.get('display_sent_at', metadata.get('date', '不明'))
        st.info(send_date)

        # 受信日（created_atを使用）
        st.markdown("**📩 受信日**")
        received_date = email.get('created_at', '不明')
        # ISO形式の日時を読みやすく整形
        if received_date and received_date != '不明':
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(received_date.replace('Z', '+00:00'))
                received_date = dt.strftime('%Y-%m-%d %H:%M:%S')
            except:
                pass
        st.info(received_date)

        # 本文要約
        st.markdown("**📝 本文要約**")
        # パース済みのemail_dataから要約を取得
        summary_text = email_data.get('summary', '')

        # summary_textがJSON文字列の場合は使用しない
        if summary_text and not (summary_text.startswith('{') or summary_text.startswith('```')):
            st.info(summary_text)
        else:
            # 要約が見つからない場合は、extracted_textの全文を表示
            extracted = email_data.get('extracted_text', '')
            if extracted:
                # From:, To:などのメタデータ行を除外
                lines = extracted.split('\n')
                clean_lines = [line for line in lines if not (line.startswith('From:') or line.startswith('To:') or line.startswith('Date:'))]
                summary_preview = '\n'.join(clean_lines).strip()
                st.info(summary_preview)
            else:
                st.info("要約がありません")

        # 画像の説明がある場合
        image_descriptions = email_data.get('image_descriptions', [])
        if image_descriptions:
            st.markdown("**📷 画像の説明**")
            for desc in image_descriptions:
                st.info(f"• {desc}")

    with tab2:
        st.markdown("#### メール本文（全文）")

        # extracted_textを取得
        extracted_text = email_data.get('extracted_text', '')

        # extracted_textがない場合は、metadataから取得
        if not extracted_text:
            extracted_text = metadata.get('extracted_text', '')

        # attachment_textは最後の手段（構造化されたテキストが含まれている可能性がある）
        if not extracted_text:
            attachment_text = email.get('attachment_text', '')
            # attachment_textに「要約:」などの構造が含まれている場合は除外
            if attachment_text and '要約:' not in attachment_text:
                extracted_text = attachment_text

        if extracted_text:
            # From, To, Date行と画像表示についての注意書きを除外
            lines = extracted_text.split('\n')
            body_lines = []
            skip_next = False

            for line in lines:
                # メタデータ行をスキップ
                if line.startswith('From:') or line.startswith('To:') or line.startswith('Date:'):
                    continue
                if '!画像表示について:' in line:
                    skip_next = True
                    continue
                if skip_next and ('End' in line or 'すべての画像を表示' in line):
                    skip_next = False
                    continue
                if not skip_next:
                    body_lines.append(line)

            body_text = '\n'.join(body_lines).strip()

            # テキストエリアで表示（スクロール可能、コピペ可能）
            st.text_area("", body_text, height=500, label_visibility="collapsed", key="email_body_text")
        else:
            # デバッグ情報を表示
            st.warning("本文が見つかりません")
            with st.expander("🔍 デバッグ情報", expanded=False):
                st.markdown("**email_dataのキー:**")
                st.code(str(list(email_data.keys())))
                st.markdown("**emailのキー:**")
                st.code(str(list(email.keys())))
                st.markdown("**metadataのキー:**")
                st.code(str(list(metadata.keys())))
                if summary:
                    st.markdown("**summary:**")
                    st.code(summary)

    with tab3:
        st.markdown("#### 重要な情報")

        # key_informationを表示
        key_info = email_data.get('key_information', [])

        if key_info and isinstance(key_info, list) and len(key_info) > 0:
            for i, info in enumerate(key_info, 1):
                st.markdown(f"{i}. {info}")
        else:
            st.info("重要な情報が抽出されていません")

        # リンクがある場合
        links = email_data.get('links', metadata.get('links', []))
        if links and len(links) > 0:
            st.markdown("---")
            st.markdown("#### 🔗 リンク")

            # リンクが多い場合は折りたたみ可能にする
            if len(links) > 5:
                with st.expander(f"リンク一覧 ({len(links)}件)", expanded=False):
                    for i, link in enumerate(links, 1):
                        # リンク形式を判定
                        if link.startswith('http'):
                            st.markdown(f"{i}. [{link}]({link})")
                        else:
                            st.markdown(f"{i}. {link}")
            else:
                for i, link in enumerate(links, 1):
                    if link.startswith('http'):
                        st.markdown(f"{i}. [{link}]({link})")
                    else:
                        st.markdown(f"{i}. {link}")

        # 画像がある場合
        has_images = email_data.get('has_images', False)
        if has_images:
            st.info("📷 このメールには画像が含まれています（HTMLプレビューで確認できます）")

    with tab4:
        st.markdown("#### メタデータ")

        # 主要なメタデータを読みやすく表示
        col1, col2 = st.columns(2)

        with col1:
            st.markdown("**送信者**")
            st.code(metadata.get('from', '不明'), language=None)

            st.markdown("**宛先**")
            st.code(metadata.get('to', '不明'), language=None)

        with col2:
            st.markdown("**件名**")
            st.code(metadata.get('subject', '(件名なし)'), language=None)

            st.markdown("**送信日時**")
            st.code(metadata.get('date', '不明'), language=None)

        # Workspace情報
        st.markdown("**Workspace**")
        st.code(email.get('workspace', 'unknown'), language=None)

        # Gmail Label
        gmail_label = metadata.get('gmail_label') or email.get('gmail_label')
        if gmail_label:
            st.markdown("**Gmail Label**")
            st.code(gmail_label, language=None)

        # 完全なメタデータJSONは折りたたみで表示
        with st.expander("🔍 完全なメタデータ（JSON）", expanded=False):
            st.json(metadata)

    # Google Drive HTMLファイルへのリンク
    st.divider()
    drive_file_id = email.get('drive_file_id') or email.get('source_id')
    if drive_file_id:
        col1, col2 = st.columns(2)
        with col1:
            st.link_button(
                "📥 元のHTMLをダウンロード",
                f"https://drive.google.com/uc?export=download&id={drive_file_id}",
                use_container_width=True
            )
        with col2:
            st.link_button(
                "👁️ Google Driveで表示",
                f"https://drive.google.com/file/d/{drive_file_id}/view",
                use_container_width=True
            )


def render_email_html_preview(email: Dict[str, Any], drive_connector=None):
    """
    メールのHTMLプレビューを表示

    Args:
        email: メールドキュメント
        drive_connector: GoogleDriveConnector インスタンス（オプション）
    """
    st.markdown("### 📧 メールプレビュー")

    # メールドキュメントの検証
    if not email:
        st.warning("メールデータが見つかりません")
        return

    drive_file_id = email.get('drive_file_id') or email.get('source_id')

    if not drive_file_id:
        st.info("プレビュー可能なHTMLファイルがありません")
        # デバッグ情報
        with st.expander("🔍 デバッグ情報"):
            st.json({
                "email_keys": list(email.keys()),
                "drive_file_id": drive_file_id,
                "source_id": email.get('source_id')
            })
        return

    # Google DriveからHTMLをダウンロードして表示
    try:
        if drive_connector is None:
            from shared.common.connectors.google_drive import GoogleDriveConnector
            drive_connector = GoogleDriveConnector()

        import tempfile
        temp_dir = tempfile.gettempdir()

        # より安全なファイル名の取得
        email_id = email.get('id', 'unknown')
        file_name = email.get('file_name', f"email_{email_id}.html")

        with st.spinner("メールHTMLを読み込み中..."):
            file_path = drive_connector.download_file(drive_file_id, file_name, temp_dir)

            if file_path:
                # HTMLファイルを読み込み
                with open(file_path, 'r', encoding='utf-8') as f:
                    html_content = f.read()

                # iframeでHTMLを表示（セキュリティを考慮してサンドボックス化）
                st.components.v1.html(
                    html_content,
                    height=700,
                    scrolling=True
                )
            else:
                st.warning("HTMLファイルのダウンロードに失敗しました")

    except Exception as e:
        error_str = str(e)

        # 404エラーの場合は特別なメッセージを表示
        if "File not found" in error_str or "404" in error_str:
            st.warning("⚠️ HTMLファイルがGoogle Driveで見つかりませんでした")
            st.info("""
            考えられる原因：
            - ファイルが削除されている
            - サービスアカウントにアクセス権限がない
            - ファイルIDが正しくない
            """)
        else:
            st.error(f"HTMLプレビューの表示中にエラーが発生しました")

        # デバッグ情報を表示
        with st.expander("🔍 エラー詳細"):
            st.text(f"エラー: {error_str}")
            import traceback
            st.code(traceback.format_exc())
            st.json({
                "email_data": {
                    "id": email.get('id'),
                    "drive_file_id": drive_file_id,
                    "file_name": email.get('file_name'),
                    "available_keys": list(email.keys())
                }
            })

        # フォールバック：リンクボタンを表示
        if drive_file_id:
            st.markdown("---")
            st.caption("Google Driveで直接確認してください：")
            col1, col2 = st.columns(2)
            with col1:
                st.link_button(
                    "📥 元のHTMLをダウンロード",
                    f"https://drive.google.com/uc?export=download&id={drive_file_id}",
                    use_container_width=True
                )
            with col2:
                st.link_button(
                    "👁️ Google Driveで表示",
                    f"https://drive.google.com/file/d/{drive_file_id}/view",
                    use_container_width=True
                )


def render_email_filters() -> Dict[str, Any]:
    """
    メールフィルター（workspace, 期間など）

    Returns:
        フィルター条件の辞書
    """
    st.sidebar.markdown("### 🔍 メールフィルター")

    filters = {}

    # workspace フィルター
    workspace_options = [
        "すべて",
        "DM_MAIL",
        "WORK_MAIL",
        "IKUYA_MAIL",
        "EMA_MAIL",
        "MONEY_MAIL",
        "JOB_MAIL",
    ]
    selected_workspace = st.sidebar.selectbox(
        "Workspace",
        workspace_options
    )
    if selected_workspace != "すべて":
        filters['workspace'] = selected_workspace

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
