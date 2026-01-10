"""
JSON Preview Component
JSON形式でのプレビューと編集UI
"""
import streamlit as st
import json
from typing import Dict, Any, Optional


def render_json_preview(metadata: Dict[str, Any], editable: bool = True, key_suffix: str = "") -> Optional[Dict[str, Any]]:
    """
    JSONプレビュー/編集UI

    Args:
        metadata: メタデータ
        editable: 編集可能かどうか
        key_suffix: Streamlitウィジェットのキーに付けるサフィックス

    Returns:
        編集可能な場合: 編集後のメタデータ
        編集不可の場合: None
    """
    st.markdown("### 🔍 JSONプレビュー")

    if editable:
        st.markdown("JSON形式で直接編集できます")
    else:
        st.markdown("JSON形式でデータを確認できます")

    st.markdown("---")

    # メタデータをJSON文字列に変換
    json_str = _format_json(metadata)

    # 統計情報の表示
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("フィールド数", len(metadata))
    with col2:
        st.metric("文字数", len(json_str))
    with col3:
        lines = json_str.count('\n') + 1
        st.metric("行数", lines)

    st.markdown("---")

    if editable:
        # 編集可能なテキストエリア
        edited_json_str = st.text_area(
            "JSON形式で編集",
            value=json_str,
            height=500,
            help="JSON形式で直接編集できます。保存前に構文エラーがないか確認してください。",
            key=f"json_editor_{key_suffix}" if key_suffix else "json_editor"
        )

        # JSON検証
        is_valid, parsed_data, error_msg = _validate_json(edited_json_str)

        if not is_valid:
            st.error(f"❌ JSON形式エラー: {error_msg}")
            st.code(edited_json_str, language="json")
            return None

        if edited_json_str != json_str:
            st.success("✅ 変更が検出されました")

        return parsed_data

    else:
        # 読み取り専用のコードブロック
        st.code(json_str, language="json", line_numbers=True)

        # ダウンロードボタン
        st.download_button(
            label="📥 JSONをダウンロード",
            data=json_str,
            file_name="metadata.json",
            mime="application/json"
        )

        return None


def _format_json(data: Dict[str, Any], indent: int = 2) -> str:
    """
    データをきれいなJSON文字列に変換

    Args:
        data: 変換するデータ
        indent: インデント幅

    Returns:
        整形されたJSON文字列
    """
    try:
        return json.dumps(data, ensure_ascii=False, indent=indent, sort_keys=False)
    except Exception as e:
        st.error(f"JSON変換エラー: {e}")
        return "{}"


def _validate_json(json_str: str) -> tuple[bool, Optional[Dict], str]:
    """
    JSON文字列を検証してパース

    Args:
        json_str: JSON文字列

    Returns:
        (検証結果, パースされたデータ, エラーメッセージ)
    """
    try:
        parsed = json.loads(json_str)
        return True, parsed, ""
    except json.JSONDecodeError as e:
        return False, None, str(e)
    except Exception as e:
        return False, None, f"予期しないエラー: {str(e)}"


def render_json_diff(original: Dict[str, Any], edited: Dict[str, Any]):
    """
    JSONの差分を表示

    Args:
        original: 元のデータ
        edited: 編集後のデータ
    """
    st.markdown("### 📝 変更内容")

    # 差分を計算
    changes = _calculate_diff(original, edited)

    if not changes:
        st.info("変更はありません")
        return

    # 変更内容を表示
    for change_type, items in changes.items():
        if not items:
            continue

        if change_type == "added":
            st.markdown("#### ➕ 追加されたフィールド")
            for key, value in items.items():
                st.code(f"{key}: {json.dumps(value, ensure_ascii=False)}", language="json")

        elif change_type == "modified":
            st.markdown("#### ✏️ 変更されたフィールド")
            for key, (old_val, new_val) in items.items():
                col1, col2 = st.columns(2)
                with col1:
                    st.markdown("**変更前**")
                    st.code(json.dumps(old_val, ensure_ascii=False, indent=2), language="json")
                with col2:
                    st.markdown("**変更後**")
                    st.code(json.dumps(new_val, ensure_ascii=False, indent=2), language="json")

        elif change_type == "removed":
            st.markdown("#### ➖ 削除されたフィールド")
            for key in items:
                st.code(key, language="text")


def _calculate_diff(original: Dict[str, Any], edited: Dict[str, Any]) -> Dict[str, Any]:
    """
    2つの辞書の差分を計算

    Returns:
        {
            "added": {key: value},
            "modified": {key: (old_value, new_value)},
            "removed": [key]
        }
    """
    diff = {
        "added": {},
        "modified": {},
        "removed": []
    }

    # 追加されたフィールド
    for key in edited:
        if key not in original:
            diff["added"][key] = edited[key]

    # 削除されたフィールド
    for key in original:
        if key not in edited:
            diff["removed"].append(key)

    # 変更されたフィールド
    for key in original:
        if key in edited and original[key] != edited[key]:
            diff["modified"][key] = (original[key], edited[key])

    return diff
