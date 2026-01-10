"""
統合Stage H再実行ユーティリティ

全ての編集箇所（全文編集、行単位編集、フォーム編集、表形式編集）から
Stage H（構造化）を再実行できる共通機能を提供します。
"""
import streamlit as st
from typing import Dict, Any, Optional
from loguru import logger


def reprocess_with_stageh(
    doc_id: str,
    attachment_text: str,
    file_name: str,
    metadata: Dict[str, Any],
    workspace: str,
    db_client,
    trigger_source: str = "manual_edit"
) -> bool:
    """
    Stage H（構造化）を再実行し、データベースに保存

    Args:
        doc_id: ドキュメントID
        attachment_text: 補正後の添付ファイルテキスト
        file_name: ファイル名
        metadata: 既存のメタデータ
        workspace: ワークスペース
        db_client: データベースクライアント
        trigger_source: 再実行のトリガー元（ログ用）

    Returns:
        成功した場合True、失敗した場合False
    """
    from G_unified_pipeline import UnifiedDocumentPipeline
    from pathlib import Path
    import tempfile
    import asyncio

    logger.info(f"[Stage H-K 再実行] 開始 - トリガー: {trigger_source}")
    logger.info(f"  ドキュメントID: {doc_id}")
    logger.info(f"  テキスト長: {len(attachment_text)} 文字")
    logger.info(f"  Workspace: {workspace}")

    try:
        # 統合パイプラインを初期化
        pipeline = UnifiedDocumentPipeline(db_client=db_client)

        # 補正されたテキストを一時ファイルとして保存
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as tmp:
            tmp.write(attachment_text)
            temp_file_path = tmp.name

        temp_path = Path(temp_file_path)

        try:
            # Stage H-K を再実行（テキストが既にあるので、Stage E-G はスキップ）
            with st.spinner(f"🔄 Stage H-K（構造化〜埋め込み）を再実行中... ({trigger_source})"):
                async def run_pipeline():
                    return await pipeline.process_document(
                        file_path=temp_path,
                        file_name=file_name,
                        doc_type=metadata.get('doc_type', 'other'),
                        workspace=workspace,
                        mime_type='text/plain',  # テキストファイルとして処理
                        source_id=doc_id,
                        existing_document_id=doc_id,  # 既存ドキュメントを更新
                        extra_metadata={
                            'manually_corrected': True,
                            'correction_trigger': trigger_source,
                            'correction_timestamp': __import__('datetime').datetime.now().isoformat(),
                            'corrected_text_length': len(attachment_text)
                        }
                    )

                # asyncioループで実行
                if asyncio.get_event_loop().is_running():
                    # 既にイベントループが動いている場合（Streamlit環境）
                    import nest_asyncio
                    nest_asyncio.apply()
                    result = asyncio.get_event_loop().run_until_complete(run_pipeline())
                else:
                    result = asyncio.run(run_pipeline())

        finally:
            # 一時ファイル削除
            if temp_path.exists():
                temp_path.unlink()

        if result.get('success'):
            st.success(f"✅ Stage H-K再実行が完了しました！（トリガー: {trigger_source}）")
            logger.info(f"[Stage H-K 再実行] 成功")
            logger.info(f"  チャンク数: {result.get('chunks_count', 0)}")

            # 補正前後の比較を表示
            with st.expander("📊 再実行結果の比較", expanded=True):
                col_before, col_after = st.columns(2)

                with col_before:
                    st.markdown("**補正前**")
                    st.metric("メタデータフィールド数", len(metadata.keys()))

                with col_after:
                    st.markdown("**補正後**")
                    st.metric("チャンク数", result.get('chunks_count', 0))

            return True
        else:
            st.error(f"❌ 再実行に失敗しました: {result.get('error')}")
            logger.error(f"[Stage H-K 再実行] 失敗: {result.get('error')}")
            return False

    except Exception as e:
        logger.error(f"[Stage H-K 再実行] エラー: {e}", exc_info=True)
        st.error(f"❌ Stage H-K再実行エラー: {e}")
        return False


def show_reprocess_button(
    doc_id: str,
    attachment_text: str,
    original_text: str,
    file_name: str,
    metadata: Dict[str, Any],
    workspace: str,
    db_client,
    trigger_source: str = "manual_edit",
    button_label: str = "🔄 変更を反映してStage H再実行"
) -> bool:
    """
    Stage H再実行ボタンを表示

    Args:
        doc_id: ドキュメントID
        attachment_text: 補正後の添付ファイルテキスト
        original_text: 元のテキスト
        file_name: ファイル名
        metadata: 既存のメタデータ
        workspace: ワークスペース
        db_client: データベースクライアント
        trigger_source: 再実行のトリガー元（ログ用）
        button_label: ボタンのラベル

    Returns:
        再実行が成功した場合True
    """
    # 変更検知
    text_changed = attachment_text != original_text

    if not text_changed:
        st.info("💡 テキストは変更されていませんが、スキーマ変更を反映するため再実行できます。")
        # スキーマ変更を反映するため、テキスト未変更でも処理を続行

    # 変更があることを表示
    char_diff = len(attachment_text) - len(original_text)
    if char_diff > 0:
        st.success(f"✅ {char_diff} 文字追加されました（合計: {len(attachment_text)} 文字）")
    elif char_diff < 0:
        st.warning(f"⚠️ {abs(char_diff)} 文字削除されました（合計: {len(attachment_text)} 文字）")

    # 再実行ボタン
    col_btn1, col_btn2 = st.columns([3, 1])

    with col_btn1:
        if st.button(
            button_label,
            type="primary",
            use_container_width=True,
            key=f"reprocess_{trigger_source}_{doc_id}"
        ):
            success = reprocess_with_stageh(
                doc_id=doc_id,
                attachment_text=attachment_text,
                file_name=file_name,
                metadata=metadata,
                workspace=workspace,
                db_client=db_client,
                trigger_source=trigger_source
            )

            if success:
                st.balloons()
                import time
                time.sleep(2)
                st.rerun()

            return success

    with col_btn2:
        if st.button(
            "↩️ リセット",
            use_container_width=True,
            key=f"reset_{trigger_source}_{doc_id}"
        ):
            st.rerun()

    return False
