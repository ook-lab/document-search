"""
Document Review App (独立アプリ版)
人間がAIの抽出結果を確認・修正するための管理画面

機能:
- タブベースUI (フォーム編集 / 表エディタ / JSONプレビュー)
- スキーマベースのフォーム編集
- データフレームによる表形式編集
- JSON差分表示
"""
import sys
from pathlib import Path

# プロジェクトのルートディレクトリをPythonパスに追加
root_dir = Path(__file__).parent.parent
sys.path.insert(0, str(root_dir))

import streamlit as st
import json
import tempfile
from typing import Dict, Any, Optional, List
import pandas as pd
from loguru import logger

from A_common.database.client import DatabaseClient
from A_common.connectors.google_drive import GoogleDriveConnector

# 新しいコンポーネントとユーティリティをインポート
from ui.utils.schema_detector import SchemaDetector
from ui.components.form_editor import render_form_editor
from ui.components.table_editor import render_table_editor, _render_array_table, _format_field_name
from ui.components.json_preview import render_json_preview, render_json_diff
from ui.components.email_viewer import (
    render_email_list,
    render_email_detail,
    render_email_html_preview,
    render_email_filters
)


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


def download_file_from_drive(source_id: str, file_name: str) -> Optional[str]:
    """
    Google Driveからファイルを一時ディレクトリにダウンロード

    Args:
        source_id: Google DriveのファイルID
        file_name: ファイル名

    Returns:
        ダウンロードされたファイルのパス、失敗時はNone
    """
    try:
        drive_connector = GoogleDriveConnector()
        temp_dir = tempfile.gettempdir()
        file_path = drive_connector.download_file(source_id, file_name, temp_dir)
        return file_path
    except Exception as e:
        error_type = type(e).__name__
        error_msg = str(e)
        logger.error(f"ファイルのダウンロードに失敗: source_id={source_id}, file_name={file_name}", exc_info=True)
        st.error(f"❌ Google Driveエラー")
        st.error(f"エラータイプ: {error_type}")
        st.error(f"エラー詳細: {error_msg}")
        st.code(f"file_id: {source_id}\nfile_name: {file_name}")
        return None


def pdf_review_ui():
    """ドキュメントレビューUIロジック（全てのファイルタイプ対応）"""
    # データベースクライアントとスキーマ検出器の初期化
    try:
        db_client = DatabaseClient()
        schema_detector = SchemaDetector()
    except Exception as e:
        st.error(f"初期化エラー: {e}")
        st.stop()

    # サイドバー: 検索とフィルタ設定
    st.sidebar.header("🔍 検索 & フィルタ")

    # Workspaceフィルタ（動的に取得）
    available_workspaces = db_client.get_available_workspaces()
    workspace_options = ["全て"] + available_workspaces
    workspace_filter = st.sidebar.selectbox(
        "Workspace",
        options=workspace_options,
        index=0,
        help="ワークスペースでフィルタリング"
    )

    # ファイルタイプフィルタ
    file_type_options = ["全て", "pdf", "email", "text", "markdown", "csv", "json"]
    file_type_filter = st.sidebar.selectbox(
        "ファイルタイプ",
        options=file_type_options,
        index=0,
        help="ファイルタイプでフィルタリング"
    )

    # レビューステータスフィルタ
    review_status_options = ["全て", "未確認", "確認済み"]
    review_status_filter = st.sidebar.selectbox(
        "レビューステータス",
        options=review_status_options,
        index=0,
        help="レビュー状態でフィルタリング"
    )

    # 検索ボックス
    search_query = st.sidebar.text_input(
        "キーワード検索",
        placeholder="例: 学年通信, abc123...",
        help="検索ワードを入力すると、レビュー状態に関係なく全データから検索します"
    )

    # 取得件数
    limit = st.sidebar.number_input(
        "取得件数",
        min_value=10,
        max_value=500,
        value=50,
        step=10,
        help="表示するドキュメントの最大件数"
    )

    # モード表示
    if search_query:
        st.sidebar.info("🔎 **検索モード**: 全データから検索中")
    else:
        st.sidebar.success("📝 **通常モード**: 未レビューのみ表示")

    # 進捗表示
    st.sidebar.markdown("---")
    st.sidebar.header("📊 レビュー進捗")
    progress_data = db_client.get_review_progress()

    col_p1, col_p2 = st.sidebar.columns(2)
    with col_p1:
        st.metric("未レビュー", f"{progress_data['unreviewed']} 件")
    with col_p2:
        st.metric("完了", f"{progress_data['reviewed']} 件")

    st.sidebar.progress(progress_data['progress_percent'] / 100)
    st.sidebar.caption(f"進捗率: {progress_data['progress_percent']}%")

    # リスト更新ボタン
    st.sidebar.markdown("---")
    if st.sidebar.button("🔄 リストを更新", use_container_width=True, key="refresh_pdf_list"):
        st.rerun()

    # レビュー対象ドキュメントを取得（全てのファイルタイプ）
    with st.spinner("ドキュメントを取得中..."):
        # Workspaceフィルタの値を変換（"全て"の場合はNone）
        workspace_value = workspace_filter if workspace_filter != "全て" else None

        # ファイルタイプフィルタの値を変換（"全て"の場合はNone）
        file_type_value = file_type_filter if file_type_filter != "全て" else None

        # レビューステータスフィルタの値を変換
        if review_status_filter == "確認済み":
            review_status_value = "reviewed"
        elif review_status_filter == "未確認":
            review_status_value = "pending"
        else:  # "全て"
            review_status_value = "all"

        documents = db_client.get_documents_for_review(
            limit=limit,
            search_query=search_query if search_query else None,
            workspace=workspace_value,
            file_type=file_type_value,
            review_status=review_status_value,
            exclude_workspace='gmail'  # Gmailワークスペースを除外
        )

    # デバッグログ: 取得後の確認
    logger.info(f"DBから取得したドキュメント数: {len(documents)}件")

    if not documents:
        st.info("レビュー対象のドキュメントがありません")
        return

    st.sidebar.success(f"✅ {len(documents)}件のドキュメント")

    # ドキュメントリストをDataFrameで表示（チェックボックス付き）
    df_data = []
    for idx, doc in enumerate(documents):
        # タイトルを表示（NULLの場合は空）
        display_name = doc.get('title', '')
        df_data.append({
            '選択': False,  # チェックボックス用
            'ID': doc.get('id', '')[:8],
            'タイトル': display_name,
            '文書タイプ': doc.get('doc_type', ''),
            '作成日時': doc.get('created_at', '')[:10]
        })

    df = pd.DataFrame(df_data)

    # デバッグログ: DataFrame作成後の確認
    logger.info(f"表示用DataFrameの行数: {len(df)}件")

    st.subheader("📁 レビュー対象ドキュメント一覧")

    # データエディタでチェックボックス付きの表を表示
    edited_df = st.data_editor(
        df,
        use_container_width=True,
        height=200,
        hide_index=True,
        column_config={
            "選択": st.column_config.CheckboxColumn(
                "選択",
                help="削除するドキュメントを選択",
                default=False,
            )
        },
        disabled=["ID", "ファイル名", "文書タイプ", "作成日時"],
        key="document_list_editor"
    )

    # 選択されたドキュメントを取得
    selected_indices = edited_df[edited_df['選択'] == True].index.tolist()
    selected_count = len(selected_indices)

    # まとめて操作ボタン（一覧の直下に常に表示）
    col_approve, col_delete, col_spacer = st.columns([1, 1, 2])

    with col_approve:
        if selected_count > 0:
            if st.button(f"✅ まとめて承認 ({selected_count}件)", use_container_width=True, type="primary"):
                with st.spinner(f"{selected_count}件のドキュメントを承認中..."):
                    success_count = 0
                    fail_count = 0

                    for idx in selected_indices:
                        doc = documents[idx]
                        doc_id = doc.get('id')

                        # レビュー済みとしてマーク
                        if db_client.mark_document_reviewed(doc_id):
                            success_count += 1
                        else:
                            fail_count += 1

                    if success_count > 0:
                        st.success(f"✅ {success_count}件のドキュメントを承認しました")
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
        if 'bulk_delete_confirm' not in st.session_state:
            st.session_state.bulk_delete_confirm = False

        if selected_count > 0:
            if not st.session_state.bulk_delete_confirm:
                if st.button(f"🗑️ まとめて削除 ({selected_count}件)", use_container_width=True, type="secondary"):
                    st.session_state.bulk_delete_confirm = True
                    st.rerun()
            else:
                if st.button(f"⚠️ 削除を実行 ({selected_count}件)", use_container_width=True, type="primary"):
                    with st.spinner(f"{selected_count}件のドキュメントを削除中..."):
                        success_count = 0
                        fail_count = 0

                        for idx in selected_indices:
                            doc = documents[idx]
                            doc_id = doc.get('id')
                            file_id = doc.get('drive_file_id') or doc.get('source_id')

                            # Google Driveから削除
                            if file_id:
                                try:
                                    drive_connector = GoogleDriveConnector()
                                    drive_connector.trash_file(file_id)
                                except Exception as e:
                                    logger.error(f"Google Drive削除エラー: {e}")

                            # データベースから削除
                            if db_client.delete_document(doc_id):
                                success_count += 1
                            else:
                                fail_count += 1

                        if success_count > 0:
                            st.success(f"✅ {success_count}件のドキュメントを削除しました")
                        if fail_count > 0:
                            st.error(f"❌ {fail_count}件の削除に失敗しました")

                        st.session_state.bulk_delete_confirm = False
                        st.balloons()
                        import time
                        time.sleep(1)
                        st.rerun()
        else:
            st.button("🗑️ まとめて削除", use_container_width=True, disabled=True)

    # 削除確認中はキャンセルボタン表示
    if st.session_state.get('bulk_delete_confirm', False):
        if st.button("❌ キャンセル", use_container_width=True):
            st.session_state.bulk_delete_confirm = False
            st.rerun()

    # ドキュメント選択
    st.subheader("🔍 ドキュメント詳細")

    # セレクトボックスのキーに検索クエリを含めることで、モード変更時にリセット
    selector_key = f"document_selector_{search_query or 'normal'}"

    selected_index = st.selectbox(
        "編集するドキュメントを選択",
        range(len(documents)),
        format_func=lambda i: documents[i].get('title', '') or '(タイトルなし)',
        key=selector_key
    )

    selected_doc = documents[selected_index]
    doc_id = selected_doc.get('id')

    # デバッグ: 選択されたドキュメントを確認
    logger.info(f"=== 選択されたドキュメント ===")
    logger.info(f"selected_index: {selected_index}")
    logger.info(f"doc_id: {doc_id}")
    logger.info(f"file_name: {selected_doc.get('file_name')}")

    # 先にドキュメント情報を取得（st.rerun()の前に）
    drive_file_id = selected_doc.get('drive_file_id')
    source_id = selected_doc.get('source_id')
    file_id = drive_file_id or source_id
    file_name = selected_doc.get('file_name') or 'unknown'
    doc_type = selected_doc.get('doc_type', '')

    # metadataをパース（JSON文字列の場合と辞書の場合の両方に対応）
    metadata = selected_doc.get('metadata') or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except json.JSONDecodeError:
            logger.error(f"Failed to parse metadata JSON for doc_id: {doc_id}")
            metadata = {}

    # extracted_tables カラムの内容を metadata に統合
    if 'extracted_tables' in selected_doc and selected_doc['extracted_tables']:
        extracted_tables = selected_doc['extracted_tables']
        if isinstance(extracted_tables, str):
            try:
                extracted_tables = json.loads(extracted_tables)
            except json.JSONDecodeError:
                logger.error(f"Failed to parse extracted_tables JSON for doc_id: {doc_id}")
                extracted_tables = None
        if extracted_tables:
            metadata['extracted_tables'] = extracted_tables
            logger.info(f"Added extracted_tables to metadata: {len(extracted_tables)} tables")

    # デバッグ: メタデータの状態を確認
    logger.info(f"metadata keys: {list(metadata.keys())}")
    logger.info(f"metadata size: {len(str(metadata))} bytes")
    if 'extracted_tables' in metadata:
        logger.info(f"extracted_tables found in metadata: {len(metadata['extracted_tables'])} tables")

    # ドキュメント変更を検出して、セッション状態をリセット
    if 'previous_doc_id' not in st.session_state:
        st.session_state.previous_doc_id = doc_id

    if st.session_state.previous_doc_id != doc_id:
        # ドキュメントが変更された場合、全ての編集関連のキーをクリア
        logger.info(f"ドキュメント変更を検出: {st.session_state.previous_doc_id} -> {doc_id}")
        logger.info(f"新しいファイル名: {file_name}")

        # 編集関連のセッション状態をクリア
        # 古いドキュメントのキーを削除
        old_doc_id = st.session_state.previous_doc_id
        keys_to_remove = [
            key for key in st.session_state.keys()
            if (key.startswith('form_') or
                key.startswith(f'json_editor_{old_doc_id}') or
                key.startswith(f'text_editor_{old_doc_id}') or
                key.startswith('table_editor_'))
        ]

        for key in keys_to_remove:
            del st.session_state[key]
            logger.debug(f"  削除: {key}")

        st.session_state.previous_doc_id = doc_id
        logger.info(f"セッション状態をクリア: {len(keys_to_remove)} keys removed")

        # ページを再レンダリング
        st.rerun()

    # 基本情報表示
    title = selected_doc.get('title', '')

    col1, col2 = st.columns([2, 1])
    with col1:
        if title:
            st.markdown(f"**タイトル**: {title}")
        else:
            st.markdown(f"**タイトル**: (未生成)")
        st.caption(f"ファイル名: {file_name}")
    with col2:
        st.markdown(f"**文書タイプ**: {doc_type}")

    st.markdown("---")

    # 修正履歴とロールバック機能（Phase 2）
    latest_correction_id = selected_doc.get('latest_correction_id')
    if latest_correction_id:
        with st.expander("📜 修正履歴とロールバック", expanded=False):
            correction_history = db_client.get_correction_history(doc_id, limit=5)

            if correction_history:
                st.markdown(f"**修正回数**: {len(correction_history)}回")

                # 最新の修正情報
                latest_correction = correction_history[0]
                st.markdown(f"**最新の修正日時**: {latest_correction.get('corrected_at')}")
                if latest_correction.get('corrector_email'):
                    st.markdown(f"**修正者**: {latest_correction.get('corrector_email')}")

                # ロールバックボタン
                col_rollback, col_spacer = st.columns([1, 2])
                with col_rollback:
                    if st.button("⏮️ ロールバック（元に戻す）", use_container_width=True, type="secondary"):
                        with st.spinner("ロールバック中..."):
                            rollback_success = db_client.rollback_document(doc_id)

                        if rollback_success:
                            st.success("✅ ロールバックに成功しました！前の状態に戻りました。")
                            st.rerun()
                        else:
                            st.error("❌ ロールバックに失敗しました")

                # 修正履歴の詳細表示
                with st.expander("修正履歴の詳細を表示", expanded=False):
                    for idx, correction in enumerate(correction_history):
                        st.markdown(f"### 修正 #{idx + 1}")
                        st.markdown(f"**日時**: {correction.get('corrected_at')}")
                        if correction.get('notes'):
                            st.markdown(f"**メモ**: {correction.get('notes')}")

                        # 修正前後の差分を表示
                        col_before, col_after = st.columns(2)
                        with col_before:
                            st.markdown("**修正前**")
                            st.json(correction.get('old_metadata', {}), expanded=True)
                        with col_after:
                            st.markdown("**修正後**")
                            st.json(correction.get('new_metadata', {}), expanded=True)

                        st.markdown("---")
            else:
                st.info("修正履歴がありません")

    st.markdown("---")

    # レイアウト: 左にPDFプレビュー、右に編集タブ
    col_left, col_right = st.columns([1, 1.2])

    with col_left:
        st.markdown("### 📄 ドキュメントプレビュー")

        # デバッグ情報をログに記録
        logger.info(f"プレビュー準備: file_id={file_id}, file_name={file_name}")

        # ファイルのダウンロードと表示
        if file_id:
            with st.spinner("ファイルをダウンロード中..."):
                file_path = download_file_from_drive(file_id, file_name)

            # ダウンロード結果の確認
            if not file_path:
                st.error("❌ ファイルのダウンロードに失敗しました")
                st.info("Google Driveからファイルを取得できませんでした。アクセス権限を確認してください。")
                with st.expander("🔍 デバッグ情報"):
                    st.code(f"file_id: {file_id}\nfile_name: {file_name}")
            elif not Path(file_path).exists():
                st.error("❌ ダウンロードされたファイルが見つかりません")
                st.info("ファイルはダウンロードされましたが、保存先に見つかりませんでした。")
                with st.expander("🔍 デバッグ情報"):
                    st.code(f"パス: {file_path}")

            if file_path and Path(file_path).exists():
                # ダウンロードされたファイルから実際のファイル名を取得
                actual_file_name = Path(file_path).name
                if file_name == 'unknown' and actual_file_name:
                    file_name = actual_file_name
                    logger.info(f"実際のファイル名を取得: {file_name}")

                # ファイルタイプに応じてプレビュー表示
                # まず実際のファイルパスから拡張子を取得（より確実）
                file_extension = Path(file_path).suffix.lstrip('.').lower()

                # ファイルパスから取得できない場合、file_nameから取得
                if not file_extension and file_name and '.' in file_name:
                    file_extension = file_name.lower().split('.')[-1]

                # 拡張子が取得できない場合、ファイルの内容から判定（マジックナンバー）
                if not file_extension:
                    logger.info("拡張子なし。ファイルの内容から判定します")
                    try:
                        with open(file_path, 'rb') as f:
                            header = f.read(16)

                        # PDFファイルの判定
                        if header.startswith(b'%PDF-'):
                            file_extension = 'pdf'
                            logger.info("マジックナンバーからPDFと判定")
                        # その他のファイルタイプも追加可能
                        elif header.startswith(b'\x50\x4B\x03\x04'):  # ZIP/DOCX/XLSX等
                            file_extension = 'zip'
                            logger.info("マジックナンバーからZIPと判定")
                        elif header.startswith(b'\xff\xd8\xff'):  # JPEG
                            file_extension = 'jpg'
                            logger.info("マジックナンバーからJPEGと判定")
                        elif header.startswith(b'\x89PNG'):  # PNG
                            file_extension = 'png'
                            logger.info("マジックナンバーからPNGと判定")
                    except Exception as e:
                        logger.error(f"ファイルタイプ判定エラー: {e}")

                logger.info(f"ファイル拡張子: {file_extension}")

                # PDFの場合
                if file_extension == 'pdf' or file_name.lower().endswith('.pdf'):
                    import os
                    logger.info(f"PDFプレビュー開始。ローカルパス: {file_path}")
                    logger.info(f"ファイルサイズ: {os.path.getsize(file_path)} bytes")

                    try:
                        from streamlit_pdf_viewer import pdf_viewer
                        logger.info("streamlit_pdf_viewer を使用してPDF表示（ファイルパス直接渡し）")
                        pdf_viewer(file_path, height=700)
                    except ImportError:
                        logger.warning("streamlit_pdf_viewer がインストールされていません。ダウンロードボタンを表示します")
                        st.warning("PDFビューアーライブラリがインストールされていません")
                        with open(file_path, 'rb') as f:
                            pdf_bytes = f.read()
                        st.download_button(
                            label="📥 PDFをダウンロード",
                            data=pdf_bytes,
                            file_name=file_name,
                            mime="application/pdf",
                            use_container_width=True
                        )
                    except Exception as e:
                        logger.error(f"PDFプレビュー表示エラー: {e}", exc_info=True)
                        st.warning(f"PDFプレビュー表示エラー: {e}")

                # テキストファイルの場合（txt, md, csv, json, etc.）
                elif file_extension in ['txt', 'md', 'markdown', 'csv', 'json', 'log', 'py', 'js', 'html', 'css']:
                    try:
                        with open(file_path, 'r', encoding='utf-8') as f:
                            text_content = f.read()

                        st.markdown("#### 📝 テキストプレビュー")

                        # タブで表示を切り替え
                        text_tab1, text_tab2, text_tab3 = st.tabs(["原文", "構造化表示", "統計"])

                        with text_tab1:
                            # 原文表示
                            st.text_area(
                                "ファイル内容",
                                value=text_content,
                                height=500,
                                disabled=True,
                                key=f"text_preview_{doc_id}"
                            )

                        with text_tab2:
                            # 構造化表示
                            from ui.utils.text_structurer import TextStructurer
                            structured_blocks = TextStructurer.structure_text(text_content)

                            if structured_blocks:
                                # 構造化データをDataFrameで表示
                                df_structured = pd.DataFrame(structured_blocks)

                                # タイプを日本語に翻訳
                                df_structured['type_ja'] = df_structured['type'].apply(
                                    lambda t: TextStructurer._translate_type(t)
                                )

                                st.dataframe(
                                    df_structured[['line_number', 'type_ja', 'content', 'length']],
                                    column_config={
                                        "line_number": st.column_config.NumberColumn("行番号", width="small"),
                                        "type_ja": st.column_config.TextColumn("タイプ", width="small"),
                                        "content": st.column_config.TextColumn("内容", width="large"),
                                        "length": st.column_config.NumberColumn("文字数", width="small")
                                    },
                                    height=500,
                                    use_container_width=True
                                )

                                # metadataに構造化データを追加
                                if 'text_blocks' not in metadata:
                                    metadata['text_blocks'] = structured_blocks
                                    logger.info(f"テキスト構造化データをメタデータに追加: {len(structured_blocks)} ブロック")

                        with text_tab3:
                            # 統計情報
                            from ui.utils.text_structurer import TextStructurer
                            structured_blocks = TextStructurer.structure_text(text_content)
                            stats = TextStructurer.get_statistics(structured_blocks)

                            st.markdown("### 📊 テキスト統計")
                            col_stat1, col_stat2, col_stat3 = st.columns(3)
                            with col_stat1:
                                st.metric("総行数", stats['total_lines'])
                            with col_stat2:
                                st.metric("ブロックタイプ数", stats['unique_types'])
                            with col_stat3:
                                total_chars = sum(block['length'] for block in structured_blocks)
                                st.metric("総文字数", total_chars)

                            st.markdown("### 📋 ブロックタイプ別件数")
                            for block_type, count in sorted(stats['type_counts'].items(), key=lambda x: x[1], reverse=True):
                                type_ja = TextStructurer._translate_type(block_type)
                                st.write(f"- **{type_ja}**: {count} 行")

                        # ダウンロードボタン
                        st.download_button(
                            label="📥 テキストファイルをダウンロード",
                            data=text_content,
                            file_name=file_name,
                            mime="text/plain",
                            use_container_width=True
                        )
                    except UnicodeDecodeError:
                        st.error("❌ テキストファイルのエンコーディングエラー。UTF-8でデコードできません。")
                    except Exception as e:
                        logger.error(f"テキストファイル読み込みエラー: {e}", exc_info=True)
                        st.error(f"❌ ファイル読み込みエラー: {e}")

                # その他のファイル
                else:
                    st.info(f"このファイルタイプ（.{file_extension}）のプレビューには対応していません")

                    # デバッグ情報を表示
                    with st.expander("🔍 デバッグ情報"):
                        st.code(f"""
ファイルパス: {file_path}
ファイル名（DB）: {selected_doc.get('file_name')}
ファイル名（使用中）: {file_name}
ファイル拡張子: '{file_extension}'
Path.suffix: '{Path(file_path).suffix}'
                        """.strip())
                    try:
                        with open(file_path, 'rb') as f:
                            file_bytes = f.read()
                        st.download_button(
                            label="📥 ファイルをダウンロード",
                            data=file_bytes,
                            file_name=file_name,
                            use_container_width=True
                        )
                    except Exception as e:
                        logger.error(f"ファイル読み込みエラー: {e}", exc_info=True)
                        st.error("ファイルの読み込みに失敗しました")
            else:
                logger.warning(f"ファイルを読み込めませんでした。file_path={file_path}, exists={Path(file_path).exists() if file_path else False}")
                st.warning("ファイルを読み込めませんでした")
        else:
            st.warning("⚠️ ファイルIDが見つかりません")
            st.info("このドキュメントにはファイルIDが設定されていないため、プレビューを表示できません。")
            with st.expander("🔍 デバッグ情報"):
                st.json({
                    "drive_file_id": selected_doc.get('drive_file_id'),
                    "source_id": selected_doc.get('source_id'),
                    "file_name": file_name,
                    "doc_type": doc_type
                })

        # ============================================
        # 【新機能】手動テキスト補正（Human-in-the-loop）
        # ============================================
        # PDFまたはテキストファイルの場合、手動補正機能を表示
        if file_path and Path(file_path).exists():
            from ui.components.manual_text_correction import (
                render_manual_text_correction,
                execute_stageh_reprocessing
            )

            # テキストコンテンツを取得
            # 必ずこの順で結合: 1. display_post_text (投稿本文) → 2. attachment_text (添付ファイル)
            display_text = selected_doc.get('display_post_text', '') or ''
            attachment_text = selected_doc.get('attachment_text', '') or ''

            # 両方を結合（空の場合も含む）
            parts = []
            if display_text:
                parts.append(display_text)
            if attachment_text:
                parts.append(attachment_text)

            extracted_text = '\n\n'.join(parts) if parts else ''

            # 手動補正UIを表示
            corrected_texts = render_manual_text_correction(
                doc_id=doc_id,
                file_name=file_name,
                extracted_text=extracted_text,
                metadata=metadata,
                doc_type=doc_type,
                display_post_text=display_text,
                attachment_text=attachment_text
            )

            # Stage H再実行が要求された場合
            if corrected_texts:
                with st.spinner("🔄 補正されたテキストでStage H（構造化）+ 全チャンク再生成中..."):
                    try:
                        # 結合されたテキスト（Stage C用）
                        corrected_combined_text = '\n\n'.join([
                            corrected_texts.get('display_post_text', ''),
                            corrected_texts.get('attachment_text', '')
                        ]).strip()

                        # Stage H再実行
                        reprocessed_result = execute_stageh_reprocessing(
                            corrected_text=corrected_combined_text,
                            file_name=file_name,
                            metadata=metadata,
                            workspace=selected_doc.get('workspace', 'personal')
                        )

                        # メタデータを更新
                        new_metadata = reprocessed_result['metadata']

                        # データベースに保存
                        logger.info(f"[Stage H再実行] データベース保存開始: doc_id={doc_id}")
                        logger.info(f"[Stage H再実行] new_metadata keys: {list(new_metadata.keys())}")
                        logger.info(f"[Stage H再実行] new_doc_type: {doc_type}")

                        # display_post_text と attachment_text を更新
                        update_fields = {
                            'metadata': new_metadata,
                            'doc_type': doc_type,
                            'display_post_text': corrected_texts.get('display_post_text', ''),
                            'attachment_text': corrected_texts.get('attachment_text', '')
                        }

                        success = db_client.update_document(doc_id, update_fields)

                        # 補正履歴を記録
                        if success:
                            db_client.record_correction(
                                doc_id=doc_id,
                                new_metadata=new_metadata,
                                new_doc_type=doc_type,
                                corrector_email=None,
                                notes="手動テキスト補正によるStage H再実行 + 全チャンク再生成"
                            )

                            # search_indexの全チャンクを削除して再生成
                            try:
                                logger.info(f"[チャンク再生成] 開始: doc_id={doc_id}")

                                # 1. 既存チャンクを削除
                                delete_result = db_client.supabase.table('10_ix_search_index').delete().eq('document_id', doc_id).execute()
                                logger.info(f"[チャンク再生成] 既存チャンク削除完了")

                                # 2. チャンク化対象テキストを準備
                                chunk_target_text = corrected_combined_text

                                # 3. メタデータチャンク生成
                                from A_common.processing.metadata_chunker import MetadataChunker
                                from C_ai_common.llm_client.llm_client import LLMClient

                                llm_client = LLMClient()
                                metadata_chunker = MetadataChunker()

                                document_data = {
                                    'file_name': file_name,
                                    'summary': reprocessed_result.get('summary', ''),
                                    'document_date': reprocessed_result.get('document_date'),
                                    'tags': reprocessed_result.get('tags', []),
                                    'doc_type': doc_type,
                                    'display_post_text': corrected_texts.get('display_post_text', ''),
                                    'display_subject': selected_doc.get('display_subject', '')
                                }
                                metadata_chunks = metadata_chunker.create_metadata_chunks(document_data)

                                current_chunk_index = 0
                                for meta_chunk in metadata_chunks:
                                    meta_text = meta_chunk.get('chunk_text', '')
                                    if not meta_text:
                                        continue
                                    meta_embedding = llm_client.generate_embedding(meta_text)
                                    meta_doc = {
                                        'document_id': doc_id,
                                        'chunk_index': current_chunk_index,
                                        'chunk_content': meta_text,
                                        'chunk_size': len(meta_text),
                                        'chunk_type': meta_chunk.get('chunk_type', 'metadata'),
                                        'search_weight': meta_chunk.get('search_weight', 1.0),
                                        'embedding': meta_embedding
                                    }
                                    db_client.supabase.table('10_ix_search_index').insert(meta_doc).execute()
                                    current_chunk_index += 1

                                # 4. 小チャンク生成
                                from A_common.utils.chunking import TextChunker
                                chunker = TextChunker(chunk_size=150, chunk_overlap=30)
                                small_chunks = chunker.split_text(chunk_target_text)

                                for small_chunk in small_chunks:
                                    small_text = small_chunk.get('chunk_text', '')
                                    if not small_text:
                                        continue
                                    small_embedding = llm_client.generate_embedding(small_text)
                                    small_doc = {
                                        'document_id': doc_id,
                                        'chunk_index': current_chunk_index,
                                        'chunk_content': small_text,
                                        'chunk_size': len(small_text),
                                        'chunk_type': 'content_small',
                                        'search_weight': 1.0,
                                        'embedding': small_embedding
                                    }
                                    db_client.supabase.table('10_ix_search_index').insert(small_doc).execute()
                                    current_chunk_index += 1

                                # 5. 合成チャンク生成
                                from A_common.utils.synthetic_chunks import create_all_synthetic_chunks
                                synthetic_chunks = create_all_synthetic_chunks(new_metadata, file_name)

                                for synthetic in synthetic_chunks:
                                    synthetic_text = synthetic.get('content', '')
                                    if not synthetic_text:
                                        continue
                                    synthetic_embedding = llm_client.generate_embedding(synthetic_text)
                                    synthetic_doc = {
                                        'document_id': doc_id,
                                        'chunk_index': current_chunk_index,
                                        'chunk_content': synthetic_text,
                                        'chunk_size': len(synthetic_text),
                                        'chunk_type': 'synthetic',
                                        'search_weight': 1.0,
                                        'embedding': synthetic_embedding,
                                        'section_title': f'[合成チャンク: {synthetic.get("type", "unknown")}]'
                                    }
                                    db_client.supabase.table('10_ix_search_index').insert(synthetic_doc).execute()
                                    current_chunk_index += 1

                                logger.info(f"[チャンク再生成] 完了: {current_chunk_index}個のチャンク生成")

                            except Exception as chunk_error:
                                logger.error(f"[チャンク再生成] エラー: {chunk_error}", exc_info=True)
                                st.warning(f"⚠️ チャンク再生成中にエラーが発生しました: {chunk_error}")

                        if success:
                            st.success("✅ Stage H再実行が完了しました！構造化データが更新されました。")
                            logger.info(f"[Stage H再実行] データベース保存成功")
                            st.balloons()

                            # 補正前後の比較を表示
                            with st.expander("📊 補正前後の比較", expanded=True):
                                col_before, col_after = st.columns(2)

                                with col_before:
                                    st.markdown("**補正前**")
                                    st.metric("投稿本文", f"{len(display_text)} 文字")
                                    st.metric("添付ファイル", f"{len(attachment_text)} 文字")
                                    st.metric("合計", f"{len(extracted_text)} 文字")

                                with col_after:
                                    st.markdown("**補正後**")
                                    st.metric("投稿本文", f"{len(corrected_texts.get('display_post_text', ''))} 文字")
                                    st.metric("添付ファイル", f"{len(corrected_texts.get('attachment_text', ''))} 文字")
                                    st.metric("合計", f"{len(corrected_combined_text)} 文字")

                            # ページをリロード
                            import time
                            time.sleep(2)
                            st.rerun()
                        else:
                            logger.error(f"[Stage H再実行] データベース保存失敗: doc_id={doc_id}")
                            logger.error(f"[Stage H再実行] metadata type: {type(new_metadata)}")
                            logger.error(f"[Stage H再実行] metadata sample: {str(new_metadata)[:500]}")
                            st.error("❌ データベースへの保存に失敗しました。詳細はログを確認してください。")

                    except Exception as e:
                        logger.error(f"Stage H再実行エラー: {e}", exc_info=True)
                        st.error(f"❌ Stage H再実行エラー: {e}")

    with col_right:
        st.markdown("### ✏️ メタデータ編集")

        # スキーマを検出
        detected_schema = schema_detector.detect_schema(doc_type, metadata)

        if detected_schema:
            st.info(f"🎯 検出されたスキーマ: **{detected_schema}**")
            editable_fields = schema_detector.get_editable_fields(detected_schema)
        else:
            st.warning("⚠️ スキーマが検出されませんでした。JSON編集モードを使用してください。")
            editable_fields = []

        # 【動的タブ生成】構造化データフィールドを自動検出
        structured_fields = detect_structured_fields(metadata)

        # 構造化フィールドのキーセットを作成（フォーム編集から除外するため）
        structured_field_keys = {field["key"] for field in structured_fields}

        # タブリストを動的に構築
        tab_names = ["📝 フォーム編集"]  # フォーム編集タブのみ

        # 構造化データごとにタブを追加
        logger.info(f"🏷️ タブ生成: 構造化データタブを {len(structured_fields)} 個追加します")
        for field in structured_fields:
            logger.info(f"  タブ追加: {field['label']} (キー: {field['key']})")
            tab_names.append(field["label"])

        # 固定タブ：表を追加、JSONプレビュー
        tab_names.append("➕ 表を追加")
        tab_names.append("🔍 JSONプレビュー")

        logger.info(f"📑 生成されるタブ一覧 ({len(tab_names)} 個): {tab_names}")

        # タブを動的に生成
        tabs = st.tabs(tab_names)
        edited_metadata = None

        # タブ1: フォーム編集
        with tabs[0]:
            if editable_fields:
                # 構造化データフィールドをフォームから除外
                form_fields = [f for f in editable_fields if f["name"] not in structured_field_keys]

                # デバッグ情報を表示
                with st.expander("🔍 デバッグ情報（開発用）", expanded=False):
                    st.code(f"""
編集可能フィールド数: {len(editable_fields)}
フォームフィールド数: {len(form_fields)}
構造化フィールド数: {len(structured_fields)}

メタデータのキー: {list(metadata.keys())}
text_blocks の存在: {'text_blocks' in metadata}
text_blocks の値: {metadata.get('text_blocks', 'なし')}

editable_fields:
{[f['name'] for f in editable_fields]}

form_fields:
{[f['name'] for f in form_fields]}

structured_field_keys:
{structured_field_keys}
                    """.strip())

                if form_fields:
                    edited_metadata = render_form_editor(metadata, form_fields, doc_id)
                else:
                    st.info("このドキュメントのフィールドは全て専用タブで編集できます")
                    st.markdown("各データタブまたはJSONプレビュータブをご利用ください")
            else:
                st.info("フォーム編集には対応するスキーマが必要です")

        # タブ2以降: 構造化データタブ（動的に生成）
        for idx, field in enumerate(structured_fields):
            with tabs[idx + 1]:  # フォーム編集の次から
                logger.info(f"📊 タブ {idx + 1} をレンダリング: {field['label']} ({field['key']})")
                logger.info(f"  データ件数: {len(field['data'])} 件")

                st.markdown(f"### {field['label']}")
                st.markdown("表形式で編集できます")
                st.markdown("---")

                # 表エディタでレンダリング（ドキュメントごとにユニークなキーを使用）
                edited_value = _render_array_table(
                    f"{field['key']}_{doc_id}",
                    field["data"],
                    field["label"]
                )

                # edited_metadataを初期化（必要に応じて）
                if edited_metadata is None:
                    edited_metadata = metadata.copy()

                edited_metadata[field["key"]] = edited_value
                logger.info(f"  ✓ {field['label']} タブのレンダリング完了")

        # 最後から2番目のタブ: 表を追加
        with tabs[-2]:
            from ui.components.table_creator import render_table_creator

            updated_metadata = render_table_creator(doc_id, metadata.copy())

            if updated_metadata:
                edited_metadata = updated_metadata
                st.info("💡 追加した表を保存するには、下の「💾 保存」ボタンを押してください")

        # 最後のタブ: JSONプレビュー
        with tabs[-1]:
            edited_metadata = render_json_preview(metadata, editable=True, key_suffix=doc_id)

        # 保存ボタンエリア
        st.markdown("---")

        # レビュー状態の表示
        is_reviewed = selected_doc.get('is_reviewed', False)
        if is_reviewed:
            reviewed_at = selected_doc.get('reviewed_at', '')
            reviewed_by = selected_doc.get('reviewed_by', '')
            st.info(f"✅ レビュー済み（{reviewed_at[:10] if reviewed_at else '日時不明'}）" +
                   (f" by {reviewed_by}" if reviewed_by else ""))

        col_save, col_validate, col_review, col_cancel = st.columns([1, 1, 1, 1])

        with col_validate:
            if st.button("🔍 変更を確認", use_container_width=True):
                if edited_metadata:
                    with st.expander("変更内容の詳細", expanded=True):
                        render_json_diff(metadata, edited_metadata)

        with col_save:
            if st.button("💾 保存", type="primary", use_container_width=True):
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
                            st.stop()

                    # データベース更新（修正履歴を記録）
                    success = db_client.record_correction(
                        doc_id=doc_id,
                        new_metadata=edited_metadata,
                        new_doc_type=doc_type,
                        corrector_email=None,  # 将来的に認証情報から取得
                        notes="Review UIからの手動修正"
                    )

                    if success:
                        st.success("✅ 保存に成功しました！修正履歴が記録されました。")
                        st.balloons()
                        # ページをリロード
                        st.rerun()
                    else:
                        st.error("❌ 保存に失敗しました")

        with col_review:
            # レビュー状態切り替えボタン
            if is_reviewed:
                # レビュー済み → 未完了に戻す
                if st.button("↩️ 未完了に戻す", use_container_width=True, type="secondary"):
                    success = db_client.mark_document_unreviewed(doc_id)
                    if success:
                        st.success("✅ 未完了に戻しました")
                        st.rerun()
                    else:
                        st.error("❌ 操作に失敗しました")
            else:
                # 未レビュー → チェック完了
                if st.button("✅ チェック完了", use_container_width=True, type="primary"):
                    success = db_client.mark_document_reviewed(doc_id)
                    if success:
                        st.success("✅ レビュー完了としてマークしました")
                        st.balloons()
                        st.rerun()
                    else:
                        st.error("❌ 操作に失敗しました")

        with col_cancel:
            if st.button("🔄 リセット", use_container_width=True):
                st.rerun()

    # 削除機能（危険な操作のため、別セクションに配置）
    st.markdown("---")
    st.markdown("### ⚠️ 危険な操作")

    # 削除確認用のセッション状態
    delete_confirm_key = f"delete_confirm_{doc_id}"
    if delete_confirm_key not in st.session_state:
        st.session_state[delete_confirm_key] = False

    col_delete1, col_delete2, col_spacer = st.columns([1, 1, 2])

    with col_delete1:
        if not st.session_state[delete_confirm_key]:
            if st.button("🗑️ データを削除", use_container_width=True, type="secondary"):
                st.session_state[delete_confirm_key] = True
                st.rerun()
        else:
            st.warning("本当に削除しますか？")

    with col_delete2:
        if st.session_state[delete_confirm_key]:
            if st.button("✅ 削除を実行", use_container_width=True, type="primary"):
                with st.spinner("削除中..."):
                    # 1. まずGoogle Driveからファイルをゴミ箱に移動
                    drive_success = False
                    if file_id:
                        try:
                            drive_connector = GoogleDriveConnector()
                            drive_success = drive_connector.trash_file(file_id)
                            if drive_success:
                                st.success(f"✅ Google Driveのファイルをゴミ箱に移動しました")
                            else:
                                st.warning(f"⚠️ Google Driveファイルの削除に失敗しましたが、データベースからは削除します")
                        except Exception as e:
                            st.error(f"Google Drive削除エラー: {e}")
                            st.warning(f"⚠️ Google Driveファイルの削除に失敗しましたが、データベースからは削除します")
                    else:
                        st.warning("Google DriveのファイルIDが見つかりません。データベースのみ削除します。")

                    # 2. データベースから削除
                    db_success = db_client.delete_document(doc_id)

                    if db_success:
                        st.success("✅ データベースからドキュメントを削除しました")
                        st.balloons()
                        # 削除確認状態をリセット
                        st.session_state[delete_confirm_key] = False
                        # 少し待ってからリロード
                        import time
                        time.sleep(1)
                        st.rerun()
                    else:
                        st.error("❌ データベースからの削除に失敗しました")
                        st.session_state[delete_confirm_key] = False

            if st.button("❌ キャンセル", use_container_width=True):
                st.session_state[delete_confirm_key] = False
                st.rerun()

    # フッター
    st.markdown("---")
    col_footer1, col_footer2 = st.columns([3, 1])
    with col_footer1:
        st.caption("Document Management System - Document Review App v2.0")
    with col_footer2:
        st.caption(f"🎨 検出スキーマ: {detected_schema or 'N/A'}")


def main():
    """メインUIロジック"""
    st.set_page_config(
        page_title="Document Review - Document Management System",
        page_icon="📋",
        layout="wide",
        initial_sidebar_state="expanded"
    )

    # ドキュメントレビューUIを表示
    pdf_review_ui()


if __name__ == "__main__":
    main()
