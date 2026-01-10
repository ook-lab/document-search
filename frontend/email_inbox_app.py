"""
Email Inbox App (独立アプリ版)
Gmailから取り込んだメールの確認・管理

機能:
- メール一覧表示
- メールプレビュー（HTML）
- 構造化データ表示（テキストブロックなど）
- Doc typeフィルタ（DM-mail / JOB-mail）
- 削除機能
"""
import sys
from pathlib import Path

# プロジェクトのルートディレクトリをPythonパスに追加
root_dir = Path(__file__).parent.parent
sys.path.insert(0, str(root_dir))

import streamlit as st
import json
from typing import Dict, Any, List
import pandas as pd
from loguru import logger

from shared.common.database.client import DatabaseClient
from shared.common.connectors.google_drive import GoogleDriveConnector
from shared.common.connectors.gmail_connector import GmailConnector
import os

# コンポーネントをインポート
from ui.components.email_viewer import (
    render_email_list,
    render_email_detail,
    render_email_html_preview
)
from ui.components.table_editor import _format_field_name, _render_array_table
from ui.components.form_editor import render_form_editor
from ui.components.json_preview import render_json_preview, render_json_diff
from ui.components.table_creator import render_table_creator
from ui.utils.schema_detector import SchemaDetector


def detect_structured_fields(metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    メタデータから構造化データフィールドを自動検出

    Args:
        metadata: メタデータ辞書

    Returns:
        構造化フィールドのリスト [{"key": str, "label": str, "data": list}, ...]
    """
    structured_fields = []

    logger.info("=" * 60)
    logger.info("🔍 構造化フィールド検出を開始")
    logger.info(f"メタデータのキー数: {len(metadata)}")
    logger.info("=" * 60)

    for key, value in metadata.items():
        # デバッグログ: 全てのキーと値の型を出力
        logger.debug(f"Key: {key}, Type: {type(value)}, Value start: {str(value)[:50]}")

        # extracted_tablesの特別処理
        if key == "extracted_tables":
            logger.info(f"🎯 FOUND extracted_tables! Type: {type(value)}, Length: {len(value) if isinstance(value, list) else 'N/A'}")
            if isinstance(value, list):
                logger.info(f"  First element type: {type(value[0]) if len(value) > 0 else 'empty'}")

        # _list, _blocks, _matrix, _tables で終わるキー、または structured_tables, weekly_schedule, extracted_tables を構造化データとして認識
        # ただし text_blocks は除外（フォーム編集タブで編集可能にするため）
        if key == "text_blocks":
            logger.info(f"✓ '{key}' は text_blocks として検出されましたが、フォーム編集タブで表示するため除外します")
            continue

        if (key.endswith("_list") or key.endswith("_blocks") or
            key.endswith("_matrix") or key.endswith("_tables") or
            key == "structured_tables" or key == "weekly_schedule" or key == "extracted_tables"):
            logger.info(f"✓ '{key}' は構造化データフィールドとして検出")

            if not isinstance(value, list):
                logger.warning(f"  ⚠️ '{key}' はリストではありません。Type: {type(value)}")
                continue

            if len(value) == 0:
                logger.warning(f"  ⚠️ '{key}' は空のリストです")
                continue

            logger.info(f"  ✓ '{key}' はリストで、要素数: {len(value)}")

            # extracted_tablesは特別処理（文字列のリストをパースして構造化データに変換）
            if key == "extracted_tables":
                logger.info(f"  ✓ '{key}' は extracted_tables として検出 - パース処理を実行")
                from ui.utils.table_parser import parse_extracted_tables
                parsed_tables = parse_extracted_tables(value)
                if parsed_tables:
                    logger.info(f"  ✓ {len(parsed_tables)} 個の表をパースしました")
                    structured_fields.append({
                        "key": key,
                        "label": _format_field_name(key),
                        "data": parsed_tables
                    })
                else:
                    logger.warning(f"  ⚠️ '{key}' のパースに失敗しました")
                continue

            # 配列の最初の要素が辞書であることを確認（構造化データの証拠）
            if isinstance(value[0], dict):
                logger.info(f"  ✓ '{key}' の最初の要素は辞書です → 構造化フィールドとして検出!")
                structured_fields.append({
                    "key": key,
                    "label": _format_field_name(key),
                    "data": value
                })
            else:
                logger.warning(f"  ⚠️ '{key}' の最初の要素は辞書ではありません。Type: {type(value[0])}")

    logger.info("=" * 60)
    logger.info(f"🎯 検出された構造化フィールド数: {len(structured_fields)}")
    for field in structured_fields:
        logger.info(f"  - {field['key']} ({field['label']}) - {len(field['data'])} 件")
    logger.info("=" * 60)

    return structured_fields


def email_inbox_ui():
    """メール受信トレイUIロジック"""
    # データベースクライアントの初期化
    try:
        db_client = DatabaseClient()
        drive_connector = GoogleDriveConnector()
    except Exception as e:
        st.error(f"初期化エラー: {e}")
        st.stop()

    # サイドバー: フィルタ設定
    st.sidebar.header("🔍 フィルタ")

    # Doc typeフィルタ（メール種別）
    doc_type_options = ["全て", "DM-mail", "JOB-mail"]
    doc_type_filter = st.sidebar.selectbox(
        "メール種別",
        options=doc_type_options,
        index=0,
        help="メールの種類でフィルタリング"
    )

    # 取得件数
    limit = st.sidebar.number_input(
        "取得件数",
        min_value=10,
        max_value=500,
        value=50,
        step=10,
        help="表示するメールの最大件数"
    )

    # リスト更新ボタン
    st.sidebar.markdown("---")
    if st.sidebar.button("🔄 リストを更新", use_container_width=True, key="refresh_email_list"):
        st.rerun()

    # メールを取得（workspace='gmail', doc_typeでフィルタ）
    with st.spinner("メールを取得中..."):
        # Doc typeフィルタの値を変換（"全て"の場合はNone）
        doc_type_value = doc_type_filter if doc_type_filter != "全て" else None

        # get_documents_for_reviewを使用してメールを取得
        emails = db_client.get_documents_for_review(
            limit=limit,
            workspace="gmail",  # Gmailのみ
            doc_type=doc_type_value,  # Doc typeでフィルタ
            review_status="all"  # 全てのステータス
        )

    logger.info(f"DBから取得したメール数: {len(emails)}件")

    if not emails:
        st.info("メールがありません")
        return

    st.sidebar.success(f"✅ {len(emails)}件のメール")

    # メール一覧を表示
    selected_index, edited_df = render_email_list(emails)

    if selected_index is None:
        st.info("メールを選択してください")
        return

    # 選択されたメールを取得
    selected_indices = edited_df[edited_df['選択'] == True].index.tolist() if edited_df is not None else []
    selected_count = len(selected_indices)

    # まとめて操作ボタン（一覧の直下に常に表示）
    col_approve, col_delete, col_spacer = st.columns([1, 1, 2])

    with col_approve:
        if selected_count > 0:
            if st.button(f"✅ まとめて承認 ({selected_count}件)", use_container_width=True, type="primary"):
                with st.spinner(f"{selected_count}件のメールを承認中..."):
                    success_count = 0
                    fail_count = 0

                    for idx in selected_indices:
                        email = emails[idx]
                        doc_id = email.get('id')

                        # レビュー済みとしてマーク
                        if db_client.mark_document_reviewed(doc_id):
                            success_count += 1
                        else:
                            fail_count += 1

                    if success_count > 0:
                        st.success(f"✅ {success_count}件のメールを承認しました")
                    if fail_count > 0:
                        st.error(f"❌ {fail_count}件の承認に失敗しました")

                    st.balloons()
                    import time
                    time.sleep(1)
                    st.rerun()
        else:
            st.button("✅ まとめて承認", use_container_width=True, disabled=True)

    with col_delete:
        # 一括削除確認用のセッション状態
        if 'bulk_delete_confirm_email' not in st.session_state:
            st.session_state.bulk_delete_confirm_email = False

        if selected_count > 0:
            if not st.session_state.bulk_delete_confirm_email:
                if st.button(f"🗑️ まとめて削除 ({selected_count}件)", use_container_width=True, type="secondary"):
                    st.session_state.bulk_delete_confirm_email = True
                    st.rerun()
            else:
                if st.button(f"⚠️ 削除を実行 ({selected_count}件)", use_container_width=True, type="primary"):
                    with st.spinner(f"{selected_count}件のメールを削除中..."):
                        success_count = 0
                        fail_count = 0

                        for idx in selected_indices:
                            email = emails[idx]
                            doc_id = email.get('id')
                            file_id = email.get('source_id')
                            metadata = email.get('metadata', {})
                            if isinstance(metadata, str):
                                try:
                                    metadata = json.loads(metadata)
                                except:
                                    metadata = {}

                            # 1. Gmailのメッセージをゴミ箱に移動
                            message_id = metadata.get('message_id')
                            if message_id:
                                try:
                                    user_email = os.getenv('GMAIL_USER_EMAIL', 'ookubo.y@workspace-o.com')
                                    gmail_connector = GmailConnector(user_email)
                                    gmail_connector.trash_message(message_id)
                                    logger.info(f"Gmailメッセージをゴミ箱に移動: {message_id}")
                                except Exception as e:
                                    logger.error(f"Gmailゴミ箱移動エラー: {e}")
                            else:
                                logger.warning(f"message_idが見つかりません（古いデータの可能性）: doc_id={doc_id}")

                            # 2. Google DriveからHTMLファイルを削除
                            if file_id:
                                try:
                                    drive_connector.trash_file(file_id)
                                except Exception as e:
                                    logger.error(f"Google Drive削除エラー: {e}")

                            # 3. データベースから削除
                            if db_client.delete_document(doc_id):
                                success_count += 1
                            else:
                                fail_count += 1

                        if success_count > 0:
                            st.success(f"✅ {success_count}件のメールを削除しました")
                        if fail_count > 0:
                            st.error(f"❌ {fail_count}件の削除に失敗しました")

                        st.session_state.bulk_delete_confirm_email = False
                        st.balloons()
                        import time
                        time.sleep(1)
                        st.rerun()
        else:
            st.button("🗑️ まとめて削除", use_container_width=True, disabled=True)

    # 削除確認中はキャンセルボタン表示
    if st.session_state.get('bulk_delete_confirm_email', False):
        if st.button("❌ キャンセル", use_container_width=True):
            st.session_state.bulk_delete_confirm_email = False
            st.rerun()

    # メール詳細表示
    st.markdown("---")
    st.subheader("📧 メール詳細")

    selected_email = emails[selected_index]

    # レイアウト: 左にHTMLプレビュー、右に詳細情報
    col_left, col_right = st.columns([1, 1.2])

    with col_left:
        st.markdown("### 📄 メールプレビュー")
        render_email_html_preview(selected_email, drive_connector)

    with col_right:
        st.markdown("### ✏️ メール情報 & メタデータ編集")

        # metadataを取得してパース
        metadata = selected_email.get('metadata') or {}
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except json.JSONDecodeError:
                logger.error(f"Failed to parse metadata JSON for email: {selected_email.get('id')}")
                metadata = {}

        # スキーマ検出器の初期化
        schema_detector = SchemaDetector()
        doc_type = selected_email.get('doc_type', '')
        doc_id = selected_email.get('id')

        # スキーマを検出
        detected_schema = schema_detector.detect_schema(doc_type, metadata)

        if detected_schema:
            st.info(f"🎯 検出されたスキーマ: **{detected_schema}**")
            editable_fields = schema_detector.get_editable_fields(detected_schema)
        else:
            editable_fields = []

        # 構造化フィールドを検出
        structured_fields = detect_structured_fields(metadata)

        # 構造化フィールドのキーセットを作成（フォーム編集から除外するため）
        structured_field_keys = {field["key"] for field in structured_fields}

        # タブリストを動的に構築
        tab_names = ["📝 基本情報"]  # 基本情報タブ

        # 構造化データごとにタブを追加
        for field in structured_fields:
            tab_names.append(field["label"])

        # 固定タブ：表を追加、JSONプレビュー
        tab_names.append("➕ 表を追加")
        tab_names.append("🔍 JSONプレビュー")

        # タブを動的に生成
        tabs = st.tabs(tab_names)
        edited_metadata = None

        # タブ1: 基本情報 & フォーム編集
        with tabs[0]:
            # 基本情報を表示
            render_email_detail(selected_email)

            st.markdown("---")
            st.markdown("#### メタデータ編集")

            if editable_fields:
                # 構造化データフィールドをフォームから除外
                form_fields = [f for f in editable_fields if f["name"] not in structured_field_keys]

                if form_fields:
                    edited_metadata = render_form_editor(metadata, form_fields, doc_id)
                else:
                    st.info("このメールのフィールドは全て専用タブで編集できます")
            else:
                st.info("フォーム編集には対応するスキーマが必要です。JSONプレビュータブをご利用ください。")

        # タブ2以降: 構造化データタブ（動的に生成）
        for idx, field in enumerate(structured_fields):
            with tabs[idx + 1]:  # 基本情報の次から
                st.markdown(f"### {field['label']}")
                st.markdown("表形式で編集できます")
                st.markdown("---")

                # 表エディタでレンダリング
                edited_value = _render_array_table(
                    f"{field['key']}_{doc_id}",
                    field["data"],
                    field["label"]
                )

                # edited_metadataを初期化（必要に応じて）
                if edited_metadata is None:
                    edited_metadata = metadata.copy()

                edited_metadata[field["key"]] = edited_value

        # 最後から2番目のタブ: 表を追加
        with tabs[-2]:
            updated_metadata = render_table_creator(doc_id, metadata.copy())

            if updated_metadata:
                edited_metadata = updated_metadata
                st.info("💡 追加した表を保存するには、下の「💾 保存」ボタンを押してください")

        # 最後のタブ: JSONプレビュー
        with tabs[-1]:
            edited_metadata = render_json_preview(metadata, editable=True, key_suffix=doc_id)

        # 保存ボタンエリア
        st.markdown("---")

        col_save, col_validate, col_cancel = st.columns([1, 1, 1])

        with col_validate:
            if st.button("🔍 変更を確認", use_container_width=True, key=f"validate_{doc_id}"):
                if edited_metadata:
                    with st.expander("変更内容の詳細", expanded=True):
                        render_json_diff(metadata, edited_metadata)

        with col_save:
            if st.button("💾 保存", type="primary", use_container_width=True, key=f"save_{doc_id}"):
                if edited_metadata is None:
                    st.error("編集されたデータがありません")
                else:
                    # スキーマ検証
                    if detected_schema:
                        is_valid, errors = schema_detector.validate_metadata(detected_schema, edited_metadata)
                        if not is_valid:
                            st.error("❌ スキーマ検証エラー:")
                            for error in errors:
                                st.error(f"  - {error}")
                        else:
                            # データベース更新（修正履歴を記録）
                            success = db_client.record_correction(
                                doc_id=doc_id,
                                new_metadata=edited_metadata,
                                new_doc_type=doc_type,
                                corrector_email=None,
                                notes="Email Inbox UIからの手動修正"
                            )

                            if success:
                                st.success("✅ 保存に成功しました！")
                                st.balloons()
                                import time
                                time.sleep(1)
                                st.rerun()
                            else:
                                st.error("❌ 保存に失敗しました")
                    else:
                        # スキーマなしでも保存可能
                        success = db_client.record_correction(
                            doc_id=doc_id,
                            new_metadata=edited_metadata,
                            new_doc_type=doc_type,
                            corrector_email=None,
                            notes="Email Inbox UIからの手動修正"
                        )

                        if success:
                            st.success("✅ 保存に成功しました！")
                            st.balloons()
                            import time
                            time.sleep(1)
                            st.rerun()
                        else:
                            st.error("❌ 保存に失敗しました")

        with col_cancel:
            if st.button("🔄 リセット", use_container_width=True, key=f"reset_{doc_id}"):
                st.rerun()

    # 削除機能（危険な操作のため、別セクションに配置）
    st.markdown("---")
    st.markdown("### ⚠️ 危険な操作")

    doc_id = selected_email.get('id')

    # 削除確認用のセッション状態
    delete_confirm_key = f"delete_confirm_email_{doc_id}"
    if delete_confirm_key not in st.session_state:
        st.session_state[delete_confirm_key] = False

    col_delete1, col_delete2, col_spacer = st.columns([1, 1, 2])

    with col_delete1:
        if not st.session_state[delete_confirm_key]:
            if st.button("🗑️ このメールを削除", use_container_width=True, type="secondary", key="single_delete"):
                st.session_state[delete_confirm_key] = True
                st.rerun()
        else:
            st.warning("本当に削除しますか？")

    with col_delete2:
        if st.session_state[delete_confirm_key]:
            if st.button("✅ 削除を実行", use_container_width=True, type="primary", key="single_delete_confirm"):
                with st.spinner("削除中..."):
                    metadata = selected_email.get('metadata', {})
                    if isinstance(metadata, str):
                        try:
                            metadata = json.loads(metadata)
                        except:
                            metadata = {}

                    # 1. Gmailのメッセージをゴミ箱に移動
                    message_id = metadata.get('message_id')
                    if message_id:
                        try:
                            user_email = os.getenv('GMAIL_USER_EMAIL', 'ookubo.y@workspace-o.com')
                            gmail_connector = GmailConnector(user_email)
                            gmail_connector.trash_message(message_id)
                            st.success(f"✅ Gmailのメッセージをゴミ箱に移動しました")
                        except Exception as e:
                            st.error(f"Gmailゴミ箱移動エラー: {e}")
                            st.warning(f"⚠️ Gmailメッセージのゴミ箱移動に失敗しましたが、続行します")
                    else:
                        st.warning(f"⚠️ message_idが見つかりません（古いデータのため、Gmailの削除はスキップされました）")

                    # 2. Google DriveからHTMLファイルを削除
                    file_id = selected_email.get('source_id')
                    if file_id:
                        try:
                            drive_connector.trash_file(file_id)
                            st.success(f"✅ Google Driveのファイルをゴミ箱に移動しました")
                        except Exception as e:
                            st.error(f"Google Drive削除エラー: {e}")
                            st.warning(f"⚠️ Google Driveファイルの削除に失敗しましたが、データベースからは削除します")

                    # 3. データベースから削除
                    db_success = db_client.delete_document(doc_id)

                    if db_success:
                        st.success("✅ データベースからメールを削除しました")
                        st.balloons()
                        st.session_state[delete_confirm_key] = False
                        import time
                        time.sleep(1)
                        st.rerun()
                    else:
                        st.error("❌ データベースからの削除に失敗しました")
                        st.session_state[delete_confirm_key] = False

            if st.button("❌ キャンセル", use_container_width=True, key="single_delete_cancel"):
                st.session_state[delete_confirm_key] = False
                st.rerun()

    # フッター
    st.markdown("---")
    st.caption("Document Management System - Email Inbox App v1.0")


def main():
    """メインUIロジック"""
    st.set_page_config(
        page_title="Email Inbox - Document Management System",
        page_icon="📬",
        layout="wide",
        initial_sidebar_state="expanded"
    )

    # メール受信トレイUIを表示
    email_inbox_ui()


if __name__ == "__main__":
    main()
