"""
統合Stage H再処理要求ユーティリティ

【設計原則】
- UI は処理を実行しない（投入のみ）
- 補正テキストをDBに保存し、再処理要求フラグを立てるだけ
- 実際の処理は Worker (process_queued_documents.py) が拾って実行

【3環境保証】
- Cloud Run / localhost / ターミナル で同じ挙動を保証
- UIから直接パイプラインを叩く経路を完全に排除
"""
import streamlit as st
from typing import Dict, Any
from datetime import datetime
from loguru import logger


def enqueue_reprocess_request(
    doc_id: str,
    corrected_text: str,
    db_client,
    trigger_source: str = "manual_edit"
) -> bool:
    """
    再処理要求をDBに登録（処理は実行しない）

    Args:
        doc_id: ドキュメントID
        corrected_text: 補正後のテキスト
        db_client: データベースクライアント
        trigger_source: 再処理のトリガー元（ログ用）

    Returns:
        成功した場合True、失敗した場合False
    """
    logger.info(f"[再処理要求] 登録開始 - トリガー: {trigger_source}")
    logger.info(f"  ドキュメントID: {doc_id}")
    logger.info(f"  テキスト長: {len(corrected_text)} 文字")

    try:
        # 再処理要求をDBに書き込む
        # Worker は processing_status='pending' のドキュメントを拾って処理する
        update_data = {
            # 補正済みテキストを保存（Stage E の出力として扱う）
            'stage_e1_text': corrected_text,

            # 再処理要求フラグ
            'processing_status': 'pending',
            'processing_stage': f'reprocess_from_h_requested:{trigger_source}',

            # エラー情報をクリア
            'processing_error': None,

            # 監査用メタデータ
            'manually_corrected': True,
            'correction_timestamp': datetime.now().isoformat(),
        }

        result = db_client.client.table('Rawdata_FILE_AND_MAIL')\
            .update(update_data)\
            .eq('id', doc_id)\
            .execute()

        if result.data:
            logger.info(f"[再処理要求] 登録成功 - doc_id: {doc_id}")
            return True
        else:
            logger.warning(f"[再処理要求] 更新結果が空 - doc_id: {doc_id}")
            return False

    except Exception as e:
        logger.error(f"[再処理要求] 登録失敗: {e}", exc_info=True)
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
    button_label: str = "📝 変更を保存して再処理要求を登録"
) -> bool:
    """
    再処理要求ボタンを表示

    【重要】このボタンは処理を実行しない。
    補正テキストをDBに保存し、Workerが拾うよう pending に戻すだけ。

    Args:
        doc_id: ドキュメントID
        attachment_text: 補正後の添付ファイルテキスト
        original_text: 元のテキスト
        file_name: ファイル名（表示用）
        metadata: 既存のメタデータ（表示用）
        workspace: ワークスペース（表示用）
        db_client: データベースクライアント
        trigger_source: 再処理のトリガー元（ログ用）
        button_label: ボタンのラベル

    Returns:
        再処理要求が登録された場合True
    """
    # 変更検知
    text_changed = attachment_text != original_text

    if not text_changed:
        st.info("💡 テキストは変更されていません。変更を加えてから再処理要求を登録できます。")

    # 変更量を表示
    char_diff = len(attachment_text) - len(original_text)
    if char_diff > 0:
        st.success(f"✅ {char_diff} 文字追加されました（合計: {len(attachment_text)} 文字）")
    elif char_diff < 0:
        st.warning(f"⚠️ {abs(char_diff)} 文字削除されました（合計: {len(attachment_text)} 文字）")

    # ボタンレイアウト
    col_btn1, col_btn2 = st.columns([3, 1])

    with col_btn1:
        if st.button(
            button_label,
            type="primary",
            use_container_width=True,
            key=f"enqueue_reprocess_{trigger_source}_{doc_id}",
            disabled=not text_changed
        ):
            with st.spinner("📤 再処理要求を登録中..."):
                success = enqueue_reprocess_request(
                    doc_id=doc_id,
                    corrected_text=attachment_text,
                    db_client=db_client,
                    trigger_source=trigger_source
                )

            if success:
                st.success("✅ 再処理要求を登録しました")
                st.info("""
                **次のステップ:**
                Worker が自動的に処理を実行します。
                または、ターミナルから以下のコマンドで即時実行できます:
                ```
                python scripts/processing/process_queued_documents.py --doc-id {doc_id} --execute
                ```
                """.format(doc_id=doc_id))

                import time
                time.sleep(2)
                st.rerun()

            else:
                st.error("❌ 再処理要求の登録に失敗しました")

            return success

    with col_btn2:
        if st.button(
            "↩️ リセット",
            use_container_width=True,
            key=f"reset_{trigger_source}_{doc_id}"
        ):
            st.rerun()

    return False


# ============================================================
# 後方互換性のための関数（廃止予定）
# ============================================================

def reprocess_with_stageh(*args, **kwargs):
    """
    【廃止】直接処理実行は廃止されました。

    代わりに enqueue_reprocess_request() を使用してください。
    この関数は後方互換性のために残していますが、
    呼び出すと警告を表示して False を返します。
    """
    st.error("""
    ⚠️ **この機能は廃止されました**

    UI から直接処理を実行することはできなくなりました。
    代わりに「再処理要求を登録」ボタンを使用し、
    Worker に処理を委任してください。

    **理由:**
    - Cloud Run / localhost / ターミナル での挙動差を防止
    - 意図しない処理実行事故を構造的に防止

    **代替手段:**
    1. 「再処理要求を登録」ボタンで pending に戻す
    2. Worker が自動で拾う、または CLI で即時実行:
       `python scripts/processing/process_queued_documents.py --doc-id <uuid> --execute`
    """)
    logger.warning("廃止された reprocess_with_stageh() が呼び出されました")
    return False
