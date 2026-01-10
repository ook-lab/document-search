"""
Markdown表をパースして構造化データに変換するユーティリティ
"""

from typing import List, Dict, Any
import re


def parse_markdown_table(markdown_table: str) -> Dict[str, Any]:
    """
    Markdown形式の表を構造化データに変換

    Args:
        markdown_table: Markdown形式の表文字列

    Returns:
        構造化された表データ: {"headers": List[str], "rows": List[List[str]]}
    """
    if not markdown_table or not markdown_table.strip():
        return {"headers": [], "rows": []}

    lines = markdown_table.strip().split('\n')

    if len(lines) < 2:
        # ヘッダーと区切り行がない場合
        return {"headers": [], "rows": []}

    # ヘッダー行を抽出
    header_line = lines[0]
    headers = [cell.strip() for cell in header_line.split('|') if cell.strip()]

    # データ行を抽出（区切り行をスキップ）
    rows = []
    for line in lines[2:]:  # 最初の2行（ヘッダーと区切り）をスキップ
        if line.strip():
            # パイプで分割し、空でないセルのみを保持
            cells = [cell.strip() for cell in line.split('|')]
            # 最初と最後の空セルを除去（Markdown形式の両端のパイプ）
            if cells and cells[0] == '':
                cells = cells[1:]
            if cells and cells[-1] == '':
                cells = cells[:-1]
            if cells:
                rows.append(cells)

    return {
        "headers": headers,
        "rows": rows
    }


def parse_extracted_tables(extracted_tables: Any) -> List[Dict[str, Any]]:
    """
    `extracted_tables`フィールドから構造化データを抽出

    Args:
        extracted_tables: データベースから取得した`extracted_tables`
                         - 新形式: 辞書のリスト [{"table_id": ..., "headers": [...], "rows": [...]}, ...]
                         - 旧形式: Markdown文字列のリストのリスト

    Returns:
        構造化された表データのリスト
    """
    if not extracted_tables:
        return []

    if not isinstance(extracted_tables, list):
        return []

    # 新形式: 既に構造化された辞書のリストの場合
    if len(extracted_tables) > 0 and isinstance(extracted_tables[0], dict):
        # 'headers'キーと'rows'キーが存在するか確認
        if 'headers' in extracted_tables[0] and 'rows' in extracted_tables[0]:
            # そのまま返す（既に正しい形式）
            return extracted_tables

    # 旧形式: extracted_tablesがリストのリストの場合（ページごとの表）
    all_tables = []
    for page_idx, page_tables in enumerate(extracted_tables, 1):
        if isinstance(page_tables, list):
            for table_idx, table_data in enumerate(page_tables, 1):
                if isinstance(table_data, str):
                    # Markdown形式の文字列をパース
                    parsed_table = parse_markdown_table(table_data)
                    if parsed_table["headers"] or parsed_table["rows"]:
                        all_tables.append({
                            "page": page_idx,
                            "table_number": table_idx,
                            "headers": parsed_table["headers"],
                            "rows": parsed_table["rows"]
                        })

    return all_tables
