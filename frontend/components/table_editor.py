"""
Table Editor Component
データフレーム形式での表編集UI
"""
import streamlit as st
import pandas as pd
from typing import Dict, Any, List
import re  # 追加: ソート用
from frontend.utils.table_parser import parse_extracted_tables

def render_table_editor(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """
    メタデータを表形式で編集

    Args:
        metadata: 現在のメタデータ

    Returns:
        編集後のメタデータ
    """
    st.markdown("### 📊 表エディタ")
    st.markdown("配列データを表形式で編集できます")
    st.markdown("---")

    edited_metadata = metadata.copy()

    # 配列型のフィールドを検出して表示
    array_fields = _find_array_fields(metadata)

    # structured_tables が存在する場合は強制的に追加
    if not array_fields and "structured_tables" in metadata:
        if isinstance(metadata["structured_tables"], list):
            array_fields = [{
                "name": "structured_tables",
                "value": metadata["structured_tables"],
                "label": _format_field_name("structured_tables")
            }]

    if not array_fields:
        st.info("表形式で編集可能な配列データが見つかりません")
        return edited_metadata

    # タブで配列ごとに表示
    if len(array_fields) > 1:
        tabs = st.tabs([field["label"] for field in array_fields])
        for tab, field in zip(tabs, array_fields):
            with tab:
                edited_value = _render_array_table(
                    field["name"],
                    field["value"],
                    field["label"]
                )
                edited_metadata[field["name"]] = edited_value
    else:
        # 配列が1つの場合はタブなしで表示
        field = array_fields[0]
        edited_value = _render_array_table(
            field["name"],
            field["value"],
            field["label"]
        )
        edited_metadata[field["name"]] = edited_value

    return edited_metadata


def _find_array_fields(metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    メタデータから配列フィールドを抽出

    Returns:
        [{"name": フィールド名, "value": 配列値, "label": 表示名}, ...]
    """
    array_fields = []

    for key, value in metadata.items():
        # structured_tables, extracted_tables は無条件で検出対象にする
        if key in ["structured_tables", "extracted_tables"] and isinstance(value, list):
            # extracted_tablesの場合、パースして構造化データに変換
            if key == "extracted_tables":
                parsed_tables = parse_extracted_tables(value)
                if parsed_tables:
                    array_fields.append({
                        "name": key,
                        "value": parsed_tables,
                        "label": _format_field_name(key)
                    })
            else:
                array_fields.append({
                    "name": key,
                    "value": value,
                    "label": _format_field_name(key)
                })
        elif isinstance(value, list) and len(value) > 0:
            # 配列の要素が辞書の場合のみ表エディタで扱う
            if isinstance(value[0], dict):
                array_fields.append({
                    "name": key,
                    "value": value,
                    "label": _format_field_name(key)
                })

    return array_fields


def _format_field_name(field_name: str) -> str:
    """
    フィールド名を表示用に整形

    動的フィールド名の整形ルール:
    - monthly_schedule_list → 📅 月間予定
    - learning_content_list → 📚 学習予定
    - weekly_timetable_matrix → 📅 週間時間割
    - xxx_list → xxx（_listを除去）
    - xxx_blocks → xxx（_blocksを除去）
    - xxx_matrix → xxx（_matrixを除去）
    """
    # 既知のフィールド名マッピング
    name_map = {
        # 新しい構造化フィールド（優先）
        "monthly_schedule_list": "📅 月間予定",
        "learning_content_list": "📚 学習予定",
        "weekly_timetable_matrix": "📅 週間時間割",
        # 汎用フィールド
        "text_blocks": "📝 文章セクション",
        "special_events": "🎉 特別イベント",
        "requirements": "📦 持ち物・準備",
        "important_points": "⚠️ 重要事項",
        # その他の既存フィールド
        "daily_schedule": "日別時間割",
        "weekly_schedule": "週間予定",
        "periods": "時限別科目",
        "class_schedules": "クラス別時間割",
        "structured_tables": "📋 その他リスト",
        "monthly_schedule_blocks": "📅 月間予定表",
        "learning_content_blocks": "📚 教科別学習予定",
        "extracted_tables": "📊 その他表形式"
    }

    # マッピングに存在する場合はそれを返す
    if field_name in name_map:
        return name_map[field_name]

    # 動的フィールド名の整形
    # _list, _blocks, または _matrix で終わる場合は除去
    if field_name.endswith("_list"):
        base_name = field_name[:-5]  # _list を除去
        # アンダースコアをスペースに変換して整形
        formatted = base_name.replace("_", " ").title()
        return f"📊 {formatted}"
    elif field_name.endswith("_blocks"):
        base_name = field_name[:-7]  # _blocks を除去
        formatted = base_name.replace("_", " ").title()
        return f"📊 {formatted}"
    elif field_name.endswith("_matrix"):
        base_name = field_name[:-7]  # _matrix を除去
        formatted = base_name.replace("_", " ").title()
        return f"📅 {formatted}"

    # その他の場合はそのまま返す
    return field_name


# --- 追加機能: スケジュールデータのフラット化とソート ---
def _flatten_and_sort_schedule(data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    スケジュールデータをクラス単位・日付順にフラット化してソート
    Example:
      In: [{'date': '12/1', 'class_schedules': [{'class': '5A', ...}, {'class': '5B', ...}]}]
      Out: [{'date': '12/1', 'class': '5A', ...}, {'date': '12/1', 'class': '5B', ...}]
    """
    if not data:
        return data

    # class_schedulesが含まれているかチェック
    has_class_schedule = False
    for item in data:
        if "class_schedules" in item and isinstance(item["class_schedules"], list):
            has_class_schedule = True
            break

    if not has_class_schedule:
        return data

    flattened_data = []

    for item in data:
        if "class_schedules" in item and isinstance(item["class_schedules"], list):
            # class_schedules以外の基本情報を取得（日付や曜日など）
            base_info = {}

            for k, v in item.items():
                if k == "class_schedules":
                    continue
                # eventsは配列なので文字列に変換
                elif k == "events" and isinstance(v, list):
                    base_info[k] = ", ".join(str(e) for e in v) if v else ""
                # day_of_weekは不要（dayと重複）
                elif k == "day_of_week":
                    continue
                else:
                    base_info[k] = v

            for class_sched in item["class_schedules"]:
                row = base_info.copy()

                # クラス名を追加
                if "class" in class_sched:
                    row["class"] = str(class_sched["class"])

                # periodsとsubjectsを統合して処理
                period_data = {}  # {display_key: subject_name} の辞書

                # 1. periodsから時限データを取得
                if "periods" in class_sched and isinstance(class_sched["periods"], list):
                    for p in class_sched["periods"]:
                        if isinstance(p, dict):
                            period_key = str(p.get("period", ""))
                            subject = str(p.get("subject", ""))
                            if period_key:
                                # 時限番号に「時限目」を追加（例: "1" -> "1時限目"）
                                if period_key.isdigit():
                                    display_key = f"{period_key}時限目"
                                else:
                                    display_key = period_key
                                period_data[display_key] = subject

                # 2. subjectsから時限データを取得（periodsで設定されていない時限のみ追加）
                if "subjects" in class_sched and isinstance(class_sched["subjects"], list):
                    for i, subject in enumerate(class_sched["subjects"], 1):
                        subject_str = str(subject)

                        # "時限:科目" 形式の場合は分割して処理
                        if ":" in subject_str:
                            parts = subject_str.split(":", 1)
                            period_label = parts[0].strip()  # 例: "朝", "1限", "2限"
                            subject_name = parts[1].strip() if len(parts) > 1 else ""

                            # "1限" -> "1時限目", "朝" -> "朝" のように変換
                            if period_label == "朝":
                                display_key = "朝"
                            elif period_label.replace("限", "").isdigit():
                                # "1限" -> "1時限目"
                                num = period_label.replace("限", "")
                                display_key = f"{num}時限目"
                            else:
                                # その他の場合はそのまま使用
                                display_key = period_label

                            # periodsで既に設定されていない場合のみ追加
                            if display_key not in period_data:
                                period_data[display_key] = subject_name
                        else:
                            # 通常の形式（科目名のみ）の場合
                            display_key = f"{i}時限目"
                            # periodsで既に設定されていない場合のみ追加
                            if display_key not in period_data:
                                period_data[display_key] = subject_str

                # 3. 統合した時限データをrowに追加
                row.update(period_data)

                flattened_data.append(row)
        else:
            # class_schedulesがない行も一応そのまま保持
            flattened_data.append(item)

    # ソート: 第一キー=class, 第二キー=date
    def sort_key(row):
        c = row.get("class", "")
        d = row.get("date", "")
        return (str(c), str(d))

    try:
        flattened_data.sort(key=sort_key)
    except:
        pass # ソートに失敗した場合はそのまま

    return flattened_data


# --- 追加機能: 列の並び替え ---
def _render_single_structured_table(field_key: str, table_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    structured_tables の1つのテーブルをレンダリング

    Args:
        field_key: 一意なキー (st.data_editor用)
        table_data: {table_title, table_type, headers, rows} 形式のデータ

    Returns:
        編集後のテーブルデータ
    """
    table_title = table_data.get("table_title", "表")
    table_type = table_data.get("table_type", "")
    rows = table_data.get("rows", [])

    if not rows:
        st.info(f"{table_title}: データがありません")
        return table_data

    # データフレームに変換
    try:
        df = pd.DataFrame(rows)

        # PyArrow エラー対策: 型強制とデータクリーニング
        df = df.astype(str)
        df = df.fillna("")
        df = df.replace(["None", "nan", "NaN", "null"], "")

    except Exception as e:
        st.error(f"データフレーム変換エラー: {e}")
        st.json(rows)
        return table_data

    # 表のメタ情報を表示
    st.markdown(f"#### {table_title}")
    if table_type:
        st.caption(f"種類: {table_type} | 全 {len(df)} 行")
    else:
        st.caption(f"全 {len(df)} 行")

    # 編集可能な表を表示
    edited_df = st.data_editor(
        df,
        use_container_width=True,
        num_rows="dynamic",
        key=f"table_{field_key}",
        height=600  # 増加: より多くの行を表示
    )

    # データフレームを辞書のリストに戻す
    try:
        edited_rows = edited_df.to_dict('records')

        # データクリーニング: 空文字列を削除
        cleaned_rows = []
        for record in edited_rows:
            cleaned_record = {k: v for k, v in record.items() if v != ""}
            if cleaned_record:
                cleaned_rows.append(cleaned_record)

        # 編集後のデータで table_data を更新
        edited_table_data = table_data.copy()
        edited_table_data["rows"] = cleaned_rows
        return edited_table_data

    except Exception as e:
        st.error(f"データ変換エラー: {e}")
        return table_data


def _render_extracted_table(field_key: str, table_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    extracted_tables の1つのテーブルをレンダリング

    Args:
        field_key: 一意なキー (st.data_editor用)
        table_data: {page, table_number, headers, rows} 形式のデータ

    Returns:
        編集後のテーブルデータ
    """
    page = table_data.get("page", 1)
    table_number = table_data.get("table_number", 1)
    headers = table_data.get("headers", [])
    rows = table_data.get("rows", [])

    if not rows and not headers:
        st.info(f"ページ{page} 表{table_number}: データがありません")
        return table_data

    # headersとrowsを使ってDataFrameを作成
    try:
        # rowsが既に辞書のリストの場合（新形式）
        if rows and isinstance(rows[0], dict):
            df = pd.DataFrame(rows)
            # ヘッダーの順序を調整
            if headers:
                # headersで指定された順序に並び替え（存在するカラムのみ）
                available_cols = [h for h in headers if h in df.columns]
                if available_cols:
                    df = df[available_cols]
        elif headers:
            # rowsがリストのリストの場合（旧形式）
            df_data = []
            for row in rows:
                # rowの長さがheadersと異なる場合は調整
                row_dict = {}
                for i, header in enumerate(headers):
                    if i < len(row):
                        row_dict[header] = row[i]
                    else:
                        row_dict[header] = ""
                df_data.append(row_dict)
            df = pd.DataFrame(df_data)
        else:
            # ヘッダーがない場合：rowsをそのまま使用
            df = pd.DataFrame(rows)

        # PyArrow エラー対策: 型強制とデータクリーニング
        df = df.astype(str)
        df = df.fillna("")
        df = df.replace(["None", "nan", "NaN", "null"], "")

    except Exception as e:
        st.error(f"データフレーム変換エラー: {e}")
        st.json(table_data)
        return table_data

    # 表のメタ情報を表示
    st.markdown(f"#### ページ{page} 表{table_number}")
    st.caption(f"全 {len(df)} 行 × {len(df.columns)} 列")

    # 編集可能な表を表示
    edited_df = st.data_editor(
        df,
        use_container_width=True,
        num_rows="dynamic",
        key=f"table_{field_key}",
        height=600  # 増加: より多くの行を表示
    )

    # データフレームを元の形式に戻す
    try:
        # ヘッダーを更新
        new_headers = edited_df.columns.tolist()

        # 行データを更新
        new_rows = []
        for _, row in edited_df.iterrows():
            row_data = [str(cell) if cell != "" else "" for cell in row.values]
            # 空でない行のみ追加
            if any(cell != "" for cell in row_data):
                new_rows.append(row_data)

        # 編集後のデータで table_data を更新
        edited_table_data = {
            "page": page,
            "table_number": table_number,
            "headers": new_headers,
            "rows": new_rows
        }
        return edited_table_data

    except Exception as e:
        st.error(f"データ変換エラー: {e}")
        return table_data


def _reorder_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    データフレームの列を見やすく並び替え
    並び順: class -> date -> day -> 朝 -> 1, 2, 3... -> その他
    """
    if df.empty:
        return df
        
    cols = df.columns.tolist()
    
    # 固定列（左側に表示したい列）
    fixed_start = []
    target_cols = ["class", "date", "day", "day_of_week"]
    for c in target_cols:
        if c in cols:
            fixed_start.append(c)
            cols.remove(c)
            
    # 時限列（数字、"1時限目"、または "朝"）
    period_cols = []
    other_cols = []

    for c in cols:
        # "1", "2" などの数字、"1時限目"、"2時限目"、または "朝"
        if c == "朝" or c.isdigit() or c.endswith("時限目"):
            period_cols.append(c)
        else:
            other_cols.append(c)

    # 時限列のソートロジック（朝を先頭、あとは数字順）
    def period_sort_key(k):
        if k == "朝":
            return -1
        if k.isdigit():
            return int(k)
        # "1時限目" -> 1 を抽出してソート
        if k.endswith("時限目"):
            try:
                num = int(k.replace("時限目", ""))
                return num
            except:
                return 999
        return 999
        
    period_cols.sort(key=period_sort_key)
    
    # 最終的な列順を結合
    new_order = fixed_start + period_cols + other_cols
    
    return df[new_order]


def _render_array_table(field_name: str, array_value: List[Dict], label: str) -> List[Dict]:
    """
    配列データを表形式で編集

    Args:
        field_name: フィールド名
        array_value: 配列データ
        label: 表示ラベル

    Returns:
        編集後の配列データ
    """
    if not array_value:
        st.info(f"{label}のデータがありません")
        return []

    # --- extracted_tables の特別処理 ---
    # extracted_tables は {page, table_number, headers, rows} の形式
    if "extracted_tables" in field_name and isinstance(array_value, list):
        # 複数の表がある場合、それぞれを個別のタブで表示
        if len(array_value) > 1:
            # タブ名を生成: description > table_id > デフォルト
            tab_names = []
            for i, table in enumerate(array_value):
                if 'description' in table and table['description']:
                    tab_names.append(table['description'])
                elif 'table_id' in table and table['table_id']:
                    tab_names.append(table['table_id'])
                elif 'page' in table or 'table_number' in table:
                    tab_names.append(f"ページ{table.get('page', i+1)} 表{table.get('table_number', i+1)}")
                else:
                    tab_names.append(f"表{i+1}")
            table_tabs = st.tabs(tab_names)
            edited_tables = []
            for i, (tab, table_data) in enumerate(zip(table_tabs, array_value)):
                with tab:
                    edited_table = _render_extracted_table(
                        f"{field_name}_{i}",
                        table_data
                    )
                    edited_tables.append(edited_table)
            return edited_tables
        elif len(array_value) == 1:
            # 1つの表のみの場合はタブなしで表示
            edited_table = _render_extracted_table(
                field_name,
                array_value[0]
            )
            return [edited_table]

    # --- structured_tables の特別処理 ---
    # structured_tables は {table_title, table_type, headers, rows} の形式
    # rows フィールドを直接表示する
    if "structured_tables" in field_name and isinstance(array_value, list):
        # 複数の表がある場合、それぞれを個別のタブで表示
        if len(array_value) > 1:
            table_tabs = st.tabs([
                table.get("table_title", f"表{i+1}")
                for i, table in enumerate(array_value)
            ])
            edited_tables = []
            for i, (tab, table_data) in enumerate(zip(table_tabs, array_value)):
                with tab:
                    edited_table = _render_single_structured_table(
                        f"{field_name}_{i}",
                        table_data
                    )
                    edited_tables.append(edited_table)
            return edited_tables
        elif len(array_value) == 1:
            # 1つの表のみの場合はタブなしで表示
            edited_table = _render_single_structured_table(
                field_name,
                array_value[0]
            )
            return [edited_table]

    # --- 変更点1: ここでデータをフラット化・ソートする ---
    processed_value = _flatten_and_sort_schedule(array_value)

    # データフレームに変換
    try:
        df = pd.DataFrame(processed_value)

        # PyArrow エラー対策: 型強制とデータクリーニング
        # すべての列を文字列型に変換して混合型を解消
        df = df.astype(str)

        # NaN, None を空文字列に置き換え
        df = df.fillna("")

        # 文字列化された "None", "nan", "NaN" も空文字列に置き換え
        df = df.replace(["None", "nan", "NaN", "null"], "")
        
        # --- 変更点2: ここで列を並び替える ---
        df = _reorder_columns(df)

    except Exception as e:
        st.error(f"データフレーム変換エラー: {e}")
        st.json(array_value)
        return array_value

    # st.data_editorで編集可能な表を表示
    st.markdown(f"#### {label}")
    st.caption(f"全 {len(df)} 行")

    edited_df = st.data_editor(
        df,
        use_container_width=True,
        num_rows="dynamic",  # 行の追加・削除を許可
        key=f"table_{field_name}",
        height=600  # 増加: より多くの行を表示
    )

    # データフレームを辞書のリストに戻す
    try:
        edited_array = edited_df.to_dict('records')

        # データクリーニング: 空文字列を削除（オプショナルなフィールド用）
        cleaned_array = []
        for record in edited_array:
            cleaned_record = {k: v for k, v in record.items() if v != ""}
            # 空のレコードは除外
            if cleaned_record:
                cleaned_array.append(cleaned_record)
        
        # 注意: ネスト構造には戻さず、フラット化された状態のまま返します
        # (ユーザーが編集しやすくするため)
        return cleaned_array

    except Exception as e:
        st.error(f"データ変換エラー: {e}")
        return array_value


def render_nested_table_editor(metadata: Dict[str, Any], path: List[str] = None) -> Dict[str, Any]:
    """
    ネストした配列データを再帰的に表示・編集

    Args:
        metadata: メタデータ
        path: 現在のパス（再帰用）

    Returns:
        編集後のメタデータ
    """
    if path is None:
        path = []

    edited_metadata = {}

    for key, value in metadata.items():
        current_path = path + [key]

        if isinstance(value, list) and len(value) > 0 and isinstance(value[0], dict):
            # 配列データ: 展開して表示
            with st.expander(f"📋 {_format_field_name(key)} ({len(value)}件)", expanded=True):
                edited_metadata[key] = _render_array_table(
                    "_".join(current_path),
                    value,
                    _format_field_name(key)
                )

        elif isinstance(value, dict):
            # ネストしたオブジェクト: 再帰的に処理
            with st.expander(f"📂 {_format_field_name(key)}", expanded=False):
                edited_metadata[key] = render_nested_table_editor(value, current_path)

        else:
            # その他のフィールドは表示のみ
            edited_metadata[key] = value

    return edited_metadata