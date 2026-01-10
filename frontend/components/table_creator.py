"""
表構造新規作成コンポーネント

AIが見逃した表を人間が追加できる機能を提供します。
"""
import streamlit as st
import pandas as pd
from typing import Dict, Any, List, Optional
from loguru import logger


def render_table_creator(doc_id: str, metadata: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    表構造を新規作成するUIをレンダリング

    Args:
        doc_id: ドキュメントID
        metadata: 既存のメタデータ

    Returns:
        新しく追加された表データ（追加された場合）、またはNone
    """
    st.markdown("### ➕ 表構造を新規追加")
    st.info("💡 AIが見逃した表を追加できます。表の種類を選択して、データを入力してください。")

    # 表の種類を選択
    table_types = {
        "weekly_schedule": "週間スケジュール（日付・曜日・イベント・時間割）",
        "monthly_schedule_blocks": "月間予定表（日付・曜日・行事・時刻・持ち物）",
        "learning_content_blocks": "学習予定表（教科・担当教員・学習内容・持ち物）",
        "structured_tables": "その他の表（持ち物リスト、成績表など）",
        "text_blocks": "テキストブロック（見出し・本文）",
        "custom": "カスタム表（自由に列を定義）"
    }

    selected_type = st.selectbox(
        "表の種類を選択",
        options=list(table_types.keys()),
        format_func=lambda x: table_types[x],
        key=f"table_type_selector_{doc_id}"
    )

    st.markdown("---")

    # 表の種類に応じたテンプレートを表示
    if selected_type == "weekly_schedule":
        return _render_weekly_schedule_creator(doc_id, metadata)
    elif selected_type == "monthly_schedule_blocks":
        return _render_monthly_schedule_creator(doc_id, metadata)
    elif selected_type == "learning_content_blocks":
        return _render_learning_content_creator(doc_id, metadata)
    elif selected_type == "structured_tables":
        return _render_structured_table_creator(doc_id, metadata)
    elif selected_type == "text_blocks":
        return _render_text_blocks_creator(doc_id, metadata)
    elif selected_type == "custom":
        return _render_custom_table_creator(doc_id, metadata)


def _render_weekly_schedule_creator(doc_id: str, metadata: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """週間スケジュール作成UI"""
    st.markdown("#### 📅 週間スケジュール")

    # テンプレート行数を指定
    num_rows = st.number_input("追加する日数", min_value=1, max_value=31, value=5, key=f"weekly_rows_{doc_id}")

    # テンプレートデータフレーム作成（DateColumn用にTextColumnを使用）
    template_data = []
    for i in range(num_rows):
        template_data.append({
            "date": f"2024-01-{i+1:02d}",
            "day_of_week": "月曜日",
            "events": "",
            "note": ""
        })

    df = pd.DataFrame(template_data)

    # データエディタで編集（DateColumnではなくTextColumnを使用）
    edited_df = st.data_editor(
        df,
        use_container_width=True,
        num_rows="dynamic",
        key=f"weekly_schedule_editor_{doc_id}",
        column_config={
            "date": st.column_config.TextColumn("日付 (YYYY-MM-DD)"),
            "day_of_week": st.column_config.SelectboxColumn(
                "曜日",
                options=["月曜日", "火曜日", "水曜日", "木曜日", "金曜日", "土曜日", "日曜日"]
            ),
            "events": st.column_config.TextColumn("行事・イベント"),
            "note": st.column_config.TextColumn("備考・持ち物")
        }
    )

    # 追加ボタン
    if st.button("➕ この表をメタデータに追加", type="primary", key=f"add_weekly_{doc_id}"):
        new_data = edited_df.to_dict('records')

        # 既存のweekly_scheduleに追加
        if 'weekly_schedule' not in metadata:
            metadata['weekly_schedule'] = []

        metadata['weekly_schedule'].extend(new_data)

        st.success(f"✅ {len(new_data)}行の週間スケジュールを追加しました！")
        logger.info(f"[表追加] weekly_schedule に {len(new_data)} 行追加")

        return metadata

    return None


def _render_monthly_schedule_creator(doc_id: str, metadata: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """月間予定表作成UI"""
    st.markdown("#### 📆 月間予定表")

    # テンプレート行数を指定
    num_rows = st.number_input("追加する行数", min_value=1, max_value=100, value=10, key=f"monthly_rows_{doc_id}")

    # テンプレートデータフレーム作成
    template_data = []
    for i in range(num_rows):
        template_data.append({
            "date": f"2024-01-{i+1:02d}",
            "day_of_week": "月",
            "event": "",
            "time": "",
            "notes": ""
        })

    df = pd.DataFrame(template_data)

    # データエディタで編集（TextColumnを使用）
    edited_df = st.data_editor(
        df,
        use_container_width=True,
        num_rows="dynamic",
        key=f"monthly_schedule_editor_{doc_id}",
        column_config={
            "date": st.column_config.TextColumn("日付 (YYYY-MM-DD)"),
            "day_of_week": st.column_config.TextColumn("曜日"),
            "event": st.column_config.TextColumn("行事名"),
            "time": st.column_config.TextColumn("時刻"),
            "notes": st.column_config.TextColumn("持ち物・備考")
        }
    )

    # 追加ボタン
    if st.button("➕ この表をメタデータに追加", type="primary", key=f"add_monthly_{doc_id}"):
        new_data = edited_df.to_dict('records')

        # 既存のmonthly_schedule_blocksに追加
        if 'monthly_schedule_blocks' not in metadata:
            metadata['monthly_schedule_blocks'] = []

        metadata['monthly_schedule_blocks'].extend(new_data)

        st.success(f"✅ {len(new_data)}行の月間予定を追加しました！")
        logger.info(f"[表追加] monthly_schedule_blocks に {len(new_data)} 行追加")

        return metadata

    return None


def _render_learning_content_creator(doc_id: str, metadata: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """学習予定表作成UI"""
    st.markdown("#### 📚 学習予定表")

    # テンプレート行数を指定
    num_rows = st.number_input("追加する教科数", min_value=1, max_value=20, value=5, key=f"learning_rows_{doc_id}")

    # テンプレートデータフレーム作成
    template_data = []
    subjects = ["国語", "算数", "理科", "社会", "英語"]
    for i in range(num_rows):
        template_data.append({
            "subject": subjects[i] if i < len(subjects) else "",
            "teacher": "",
            "content": "",
            "materials": ""
        })

    df = pd.DataFrame(template_data)

    # データエディタで編集
    edited_df = st.data_editor(
        df,
        use_container_width=True,
        num_rows="dynamic",
        key=f"learning_content_editor_{doc_id}",
        column_config={
            "subject": st.column_config.TextColumn("教科"),
            "teacher": st.column_config.TextColumn("担当教員"),
            "content": st.column_config.TextColumn("学習内容"),
            "materials": st.column_config.TextColumn("持ち物")
        }
    )

    # 追加ボタン
    if st.button("➕ この表をメタデータに追加", type="primary", key=f"add_learning_{doc_id}"):
        new_data = edited_df.to_dict('records')

        # 既存のlearning_content_blocksに追加
        if 'learning_content_blocks' not in metadata:
            metadata['learning_content_blocks'] = []

        metadata['learning_content_blocks'].extend(new_data)

        st.success(f"✅ {len(new_data)}行の学習予定を追加しました！")
        logger.info(f"[表追加] learning_content_blocks に {len(new_data)} 行追加")

        return metadata

    return None


def _render_structured_table_creator(doc_id: str, metadata: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """汎用構造化表作成UI"""
    st.markdown("#### 📊 構造化表")

    # 表のタイトル
    table_title = st.text_input("表のタイトル", key=f"table_title_{doc_id}")

    # 列名を指定
    col_headers = st.text_input(
        "列名（カンマ区切り）",
        placeholder="例: 項目,数量,価格",
        key=f"table_headers_{doc_id}"
    )

    if not col_headers:
        st.warning("列名を入力してください")
        return None

    headers = [h.strip() for h in col_headers.split(',')]

    # テンプレート行数を指定
    num_rows = st.number_input("追加する行数", min_value=1, max_value=100, value=5, key=f"struct_rows_{doc_id}")

    # テンプレートデータフレーム作成
    template_data = []
    for i in range(num_rows):
        row = {header: "" for header in headers}
        template_data.append(row)

    df = pd.DataFrame(template_data)

    # データエディタで編集
    edited_df = st.data_editor(
        df,
        use_container_width=True,
        num_rows="dynamic",
        key=f"structured_table_editor_{doc_id}"
    )

    # 追加ボタン
    if st.button("➕ この表をメタデータに追加", type="primary", key=f"add_struct_{doc_id}"):
        new_table = {
            "table_title": table_title,
            "table_type": "custom",
            "headers": headers,
            "rows": edited_df.to_dict('records')
        }

        # 既存のstructured_tablesに追加
        if 'structured_tables' not in metadata:
            metadata['structured_tables'] = []

        metadata['structured_tables'].append(new_table)

        st.success(f"✅ 表「{table_title}」を追加しました！（{len(edited_df)}行）")
        logger.info(f"[表追加] structured_tables に表追加: {table_title}")

        return metadata

    return None


def _render_text_blocks_creator(doc_id: str, metadata: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """テキストブロック作成UI"""
    st.markdown("#### 📝 テキストブロック")

    # テンプレート行数を指定
    num_rows = st.number_input("追加するブロック数", min_value=1, max_value=20, value=3, key=f"text_rows_{doc_id}")

    # テンプレートデータフレーム作成
    template_data = []
    for i in range(num_rows):
        template_data.append({
            "title": f"見出し {i+1}",
            "content": ""
        })

    df = pd.DataFrame(template_data)

    # データエディタで編集
    edited_df = st.data_editor(
        df,
        use_container_width=True,
        num_rows="dynamic",
        key=f"text_blocks_editor_{doc_id}",
        column_config={
            "title": st.column_config.TextColumn("見出し", width="small"),
            "content": st.column_config.TextColumn("本文", width="large")
        }
    )

    # 追加ボタン
    if st.button("➕ このテキストブロックをメタデータに追加", type="primary", key=f"add_text_{doc_id}"):
        new_data = edited_df.to_dict('records')

        # 既存のtext_blocksに追加
        if 'text_blocks' not in metadata:
            metadata['text_blocks'] = []

        metadata['text_blocks'].extend(new_data)

        st.success(f"✅ {len(new_data)}個のテキストブロックを追加しました！")
        logger.info(f"[表追加] text_blocks に {len(new_data)} ブロック追加")

        return metadata

    return None


def _render_custom_table_creator(doc_id: str, metadata: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """カスタム表作成UI"""
    st.markdown("#### 🔧 カスタム表")

    # フィールド名を指定
    field_name = st.text_input(
        "フィールド名（メタデータのキー）",
        placeholder="例: custom_schedule",
        key=f"custom_field_{doc_id}"
    )

    # 列名を指定
    col_headers = st.text_input(
        "列名（カンマ区切り）",
        placeholder="例: 日付,内容,担当者",
        key=f"custom_headers_{doc_id}"
    )

    if not field_name or not col_headers:
        st.warning("フィールド名と列名を入力してください")
        return None

    headers = [h.strip() for h in col_headers.split(',')]

    # テンプレート行数を指定
    num_rows = st.number_input("追加する行数", min_value=1, max_value=100, value=5, key=f"custom_rows_{doc_id}")

    # テンプレートデータフレーム作成
    template_data = []
    for i in range(num_rows):
        row = {header: "" for header in headers}
        template_data.append(row)

    df = pd.DataFrame(template_data)

    # データエディタで編集
    edited_df = st.data_editor(
        df,
        use_container_width=True,
        num_rows="dynamic",
        key=f"custom_table_editor_{doc_id}"
    )

    # 追加ボタン
    if st.button("➕ この表をメタデータに追加", type="primary", key=f"add_custom_{doc_id}"):
        new_data = edited_df.to_dict('records')

        # カスタムフィールドに追加
        if field_name not in metadata:
            metadata[field_name] = []

        metadata[field_name].extend(new_data)

        st.success(f"✅ {len(new_data)}行をフィールド「{field_name}」に追加しました！")
        logger.info(f"[表追加] {field_name} に {len(new_data)} 行追加")

        return metadata

    return None
