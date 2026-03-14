"""
Table Parser Utilities

カラムナ形式（columns + rows）を辞書リスト形式に復元するユーティリティ
Stage F → Stage H1 間のデータ変換に使用
"""
from typing import Any, Dict, List, Union


def recompose_columnar_data(columnar_json: Union[Dict, List, Any]) -> List[Dict]:
    """
    軽量なカラムナ形式を通常の辞書リストに復元する

    Args:
        columnar_json: カラムナ形式のJSON
            {"columns": ["A", "B"], "rows": [["v1", "v2"], ["v3", "v4"]]}

    Returns:
        辞書リスト形式
            [{"A": "v1", "B": "v2"}, {"A": "v3", "B": "v4"}]

    Examples:
        >>> recompose_columnar_data({"columns": ["順位", "氏名"], "rows": [[1, "山田"], [2, "田中"]]})
        [{"順位": 1, "氏名": "山田"}, {"順位": 2, "氏名": "田中"}]

        >>> recompose_columnar_data([{"A": 1}])  # 既に辞書リスト形式
        [{"A": 1}]
    """
    # None や空の場合
    if not columnar_json:
        return []

    # 既に辞書リスト形式の場合はそのまま返す
    if isinstance(columnar_json, list):
        return columnar_json

    # 辞書でない場合
    if not isinstance(columnar_json, dict):
        return []

    # columns と rows を取得
    cols = columnar_json.get("columns", [])
    rows = columnar_json.get("rows", [])

    # columns がない場合（カラムナ形式ではない）
    if not cols:
        # headers + rows 形式の可能性をチェック
        headers = columnar_json.get("headers", [])
        if headers and rows:
            cols = headers
        else:
            return []

    # rows がない場合
    if not rows:
        return []

    # 辞書リストに変換
    result = []
    for row in rows:
        if isinstance(row, list):
            # 行の要素数が columns より少ない場合は空文字で補完
            row_dict = {}
            for i, col in enumerate(cols):
                row_dict[col] = row[i] if i < len(row) else ""
            result.append(row_dict)
        elif isinstance(row, dict):
            # 既に辞書形式の行はそのまま
            result.append(row)

    return result


def is_columnar_format(data: Any) -> bool:
    """
    データがカラムナ形式かどうかを判定

    Args:
        data: 判定対象のデータ

    Returns:
        カラムナ形式なら True
    """
    if not isinstance(data, dict):
        return False

    has_columns = "columns" in data and isinstance(data["columns"], list)
    has_rows = "rows" in data and isinstance(data["rows"], list)

    # rows の最初の要素が list であることを確認（辞書リストではない）
    if has_columns and has_rows and data["rows"]:
        first_row = data["rows"][0]
        return isinstance(first_row, list)

    return has_columns and has_rows


def extract_table_text_for_removal(table: Dict) -> List[str]:
    """
    表データからH2で削除すべきテキスト断片を抽出

    H1で処理した表の内容がH2のテキストに重複して含まれている場合、
    そのテキストを削除するための断片リストを生成

    Args:
        table: 表データ（columns/rows または headers/rows 形式）

    Returns:
        削除対象のテキスト断片リスト
    """
    fragments = []

    # テーブルタイトル
    title = table.get("table_title", "")
    if title:
        fragments.append(title)

    # columns/headers
    cols = table.get("columns", []) or table.get("headers", [])
    if cols:
        # ヘッダー行全体
        fragments.append(" ".join(str(c) for c in cols))

    # rows
    rows = table.get("rows", [])
    for row in rows:
        if isinstance(row, list):
            # 行全体のテキスト
            row_text = " ".join(str(cell) for cell in row)
            if len(row_text) > 5:  # 短すぎる断片は除外
                fragments.append(row_text)
            # 各セルの値（長いもののみ）
            for cell in row:
                cell_str = str(cell)
                if len(cell_str) > 10:
                    fragments.append(cell_str)
        elif isinstance(row, dict):
            # 辞書形式の行
            row_text = " ".join(str(v) for v in row.values())
            if len(row_text) > 5:
                fragments.append(row_text)

    return fragments
