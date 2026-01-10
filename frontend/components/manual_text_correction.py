"""
手動テキスト補正コンポーネント (Human-in-the-loop)

Gemini Visionが取りこぼしたテキストを人間が補完し、
Stage H（Gemini 2.5 Flash）で再構造化する機能を提供します。

使用例:
- スキャンPDFで500文字のテキストがある
- Gemini Visionが一部しか拾えなかった（200文字）
- 人間が残りの300文字を手入力
- 完全なテキスト（500文字）+ Gemini Visionのレイアウト情報でStage H再実行
- → 高品質な構造化データが生成される
"""
import streamlit as st
from typing import Dict, Any, Optional
from loguru import logger
import difflib


def _highlight_diff(original: str, corrected: str) -> str:
    """
    2つのテキストの差分をハイライト表示用のマークダウンに変換

    Args:
        original: 元のテキスト
        corrected: 補正後のテキスト

    Returns:
        差分をハイライトしたマークダウン文字列
    """
    diff = list(difflib.unified_diff(
        original.split('\n'),
        corrected.split('\n'),
        lineterm='',
        n=0  # コンテキスト行数を0に
    ))

    if not diff:
        return "（変更なし）"

    result_lines = []
    for line in diff[2:]:  # 最初の2行はヘッダーなのでスキップ
        if line.startswith('+'):
            result_lines.append(f"**+ {line[1:]}**")  # 追加行を太字
        elif line.startswith('-'):
            result_lines.append(f"~~- {line[1:]}~~")  # 削除行を取り消し線
        else:
            result_lines.append(line)

    return '\n'.join(result_lines)


def render_manual_text_correction(
    doc_id: str,
    file_name: str,
    extracted_text: str,
    metadata: Dict[str, Any],
    doc_type: str,
    display_post_text: str = "",
    attachment_text: str = ""
) -> Optional[Dict[str, str]]:
    """
    手動テキスト補正UIをレンダリング

    このコンポーネントは以下の機能を提供します：
    1. Gemini Visionが抽出したテキストの表示
    2. 人間による手動補正・完全入力
    3. 補正前後の差分表示
    4. Stage H再実行ボタン

    Args:
        doc_id: ドキュメントID
        file_name: ファイル名
        extracted_text: 結合されたテキスト（表示用、下位互換性）
        metadata: 既存のメタデータ（Stage I統合の結果を含む）
        doc_type: ドキュメントタイプ
        display_post_text: Classroom投稿本文
        attachment_text: 添付ファイルのテキスト

    Returns:
        補正されたテキストの辞書（再実行が要求された場合）、またはNone
        {"display_post_text": str, "attachment_text": str}
    """
    st.markdown("---")
    st.markdown("### 🛠️ テキスト抽出の手動補正（Human-in-the-loop）")

    # 説明エリア
    with st.expander("💡 この機能について", expanded=False):
        st.markdown("""
        **Gemini Visionが取りこぼしたテキストを補完できます！**

        **使用例:**
        - スキャンされたPDFで、OCRが一部の文字を読めなかった場合
        - 手書き文字が含まれている場合
        - 複雑なレイアウトで抽出が不完全な場合

        **処理フロー:**
        1. 👇 下のエリアに正しいテキストを入力してください
        2. 🔄 「再構造化」ボタンを押すと...
        3. **完全なテキスト（人間）+ レイアウト情報（Vision）** でStage Hが再実行されます
        4. ✨ 構造化データの品質がレベルアップ！

        **ポイント:**
        - Gemini Visionのレイアウト情報（見出し、箇条書きなどの構造）は保持されます
        - Gemini 2.5 Flashが、完全なテキストとレイアウト情報を統合して構造化します
        """)

    # 現在の抽出状況
    col_info1, col_info2 = st.columns(2)
    with col_info1:
        st.metric("元の文字数", len(extracted_text))
    with col_info2:
        st.metric("ファイル名", file_name[:20] + "..." if len(file_name) > 20 else file_name)

    # Vision解析の情報を表示
    with st.expander("🔍 Gemini Visionの解析情報（保持されるレイアウト情報）"):
        st.json({
            "doc_type": doc_type,
            "summary": metadata.get('summary', '')[:200] + "...",
            "relevant_date": metadata.get('relevant_date')
        })

    st.markdown("---")

    # タブで編集方法を選択
    tab1, tab2 = st.tabs(["📝 全文編集", "📊 差分プレビュー"])

    # セッション状態でテキストを管理（2つのフィールドを別々に管理）
    if f'corrected_display_text_{doc_id}' not in st.session_state:
        st.session_state[f'corrected_display_text_{doc_id}'] = display_post_text
    if f'corrected_attachment_text_{doc_id}' not in st.session_state:
        st.session_state[f'corrected_attachment_text_{doc_id}'] = attachment_text

    corrected_texts = None

    with tab1:
        st.markdown("#### 全文を編集")
        st.info("💡 投稿本文と添付ファイルのテキストを別々に編集できます")

        # 投稿本文の編集
        st.markdown("**📧 投稿本文 (display_post_text)**")
        st.caption("Classroomの投稿本文、メールの件名・本文など")
        display_input = st.text_area(
            "投稿本文",
            value=st.session_state[f'corrected_display_text_{doc_id}'],
            height=200,
            key=f"manual_display_text_{doc_id}",
            help="Classroom投稿本文やメールの件名・本文を編集",
            label_visibility="collapsed"
        )
        st.session_state[f'corrected_display_text_{doc_id}'] = display_input

        # 文字数表示
        display_diff = len(display_input) - len(display_post_text)
        if display_diff > 0:
            st.success(f"✅ {display_diff} 文字追加（合計: {len(display_input)} 文字）")
        elif display_diff < 0:
            st.warning(f"⚠️ {abs(display_diff)} 文字削除（合計: {len(display_input)} 文字）")
        else:
            st.info("変更なし")

        st.markdown("---")

        # 添付ファイルテキストの編集
        st.markdown("**📎 添付ファイル (attachment_text)**")
        st.caption("PDFやOffice文書からGemini Visionが抽出したテキスト")
        attachment_input = st.text_area(
            "添付ファイルのテキスト",
            value=st.session_state[f'corrected_attachment_text_{doc_id}'],
            height=200,
            key=f"manual_attachment_text_{doc_id}",
            help="Gemini Visionが抽出したテキストを補正",
            label_visibility="collapsed"
        )
        st.session_state[f'corrected_attachment_text_{doc_id}'] = attachment_input

        # 文字数表示
        attachment_diff = len(attachment_input) - len(attachment_text)
        if attachment_diff > 0:
            st.success(f"✅ {attachment_diff} 文字追加（合計: {len(attachment_input)} 文字）")
        elif attachment_diff < 0:
            st.warning(f"⚠️ {abs(attachment_diff)} 文字削除（合計: {len(attachment_input)} 文字）")
        else:
            st.info("変更なし")

    with tab2:
        st.markdown("#### 変更内容のプレビュー")
        st.info("💡 元のテキストと補正後のテキストの差分を確認できます")

        current_display_text = st.session_state[f'corrected_display_text_{doc_id}']
        current_attachment_text = st.session_state[f'corrected_attachment_text_{doc_id}']

        # 投稿本文の差分
        st.markdown("**📧 投稿本文の変更:**")
        if current_display_text != display_post_text:
            diff_markdown = _highlight_diff(display_post_text, current_display_text)
            st.markdown(diff_markdown)
            col_stat1, col_stat2 = st.columns(2)
            with col_stat1:
                st.metric("元の文字数", len(display_post_text))
            with col_stat2:
                st.metric("補正後の文字数", len(current_display_text))
        else:
            st.info("変更なし")

        st.markdown("---")

        # 添付ファイルの差分
        st.markdown("**📎 添付ファイルの変更:**")
        if current_attachment_text != attachment_text:
            diff_markdown = _highlight_diff(attachment_text, current_attachment_text)
            st.markdown(diff_markdown)
            col_stat1, col_stat2 = st.columns(2)
            with col_stat1:
                st.metric("元の文字数", len(attachment_text))
            with col_stat2:
                st.metric("補正後の文字数", len(current_attachment_text))
        else:
            st.info("変更なし")

    st.markdown("---")

    # 再実行ボタン
    col_btn1, col_btn2, col_btn3 = st.columns([2, 2, 6])

    # 再実行フラグの初期化
    if f'trigger_reprocess_{doc_id}' not in st.session_state:
        st.session_state[f'trigger_reprocess_{doc_id}'] = False

    with col_btn1:
        if st.button(
            "🔄 Stage H 再実行",
            type="primary",
            use_container_width=True,
            key=f"reprocess_{doc_id}",
            help="補正されたテキストでGemini 2.5 Flashによる構造化 + 全チャンク再生成を実行します"
        ):
            current_display_text = st.session_state[f'corrected_display_text_{doc_id}']
            current_attachment_text = st.session_state[f'corrected_attachment_text_{doc_id}']

            display_changed = current_display_text != display_post_text
            attachment_changed = current_attachment_text != attachment_text

            if display_changed or attachment_changed:
                logger.info(f"[手動補正] テキスト補正完了:")
                logger.info(f"  投稿本文: {len(display_post_text)} → {len(current_display_text)} 文字")
                logger.info(f"  添付ファイル: {len(attachment_text)} → {len(current_attachment_text)} 文字")
            else:
                st.info("ℹ️ テキストは変更されていませんが、スキーマ変更を反映するため再実行します")
                logger.info(f"[手動補正] テキスト未変更だがStage H再実行を要求（スキーマ変更反映のため）")

            # 再実行フラグを立てる
            st.session_state[f'trigger_reprocess_{doc_id}'] = True
            st.rerun()

    with col_btn2:
        if st.button(
            "↩️ リセット",
            use_container_width=True,
            key=f"reset_{doc_id}",
            help="元のテキストに戻します"
        ):
            st.session_state[f'corrected_display_text_{doc_id}'] = display_post_text
            st.session_state[f'corrected_attachment_text_{doc_id}'] = attachment_text
            st.rerun()

    # 再実行フラグがセットされている場合、補正テキストを返す
    if st.session_state.get(f'trigger_reprocess_{doc_id}', False):
        corrected_texts = {
            "display_post_text": st.session_state[f'corrected_display_text_{doc_id}'],
            "attachment_text": st.session_state[f'corrected_attachment_text_{doc_id}']
        }
        # フラグをクリア
        st.session_state[f'trigger_reprocess_{doc_id}'] = False
        return corrected_texts

    return None


def execute_stageh_reprocessing(
    corrected_text: str,
    file_name: str,
    metadata: Dict[str, Any],
    workspace: str
) -> Dict[str, Any]:
    """
    補正されたテキストでStage Hを再実行

    このラッパー関数は後方互換性のために残されています。
    新しいコードでは ui.utils.stageh_reprocessor を使用してください。

    Args:
        corrected_text: 人間が補正したテキスト
        file_name: ファイル名
        metadata: Stage I統合の結果を含むメタデータ
        workspace: ワークスペース

    Returns:
        新しい構造化データ
    """
    logger.warning("[Deprecated] execute_stageh_reprocessing() は非推奨です。ui.utils.stage_h_reprocessor.reprocess_with_stageh() を使用してください。")

    # この関数は後方互換性のために残されていますが、実装は削除されました
    # 新しいコードでは ui.utils.stage_h_reprocessor.reprocess_with_stageh() を直接使用してください
    raise NotImplementedError(
        "execute_stageh_reprocessing() は非推奨です。"
        "ui.utils.stage_h_reprocessor.reprocess_with_stageh() を使用してください。"
    )
