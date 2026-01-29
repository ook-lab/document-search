"""
Stage H1: Table Specialist (表処理専門)

【設計 2026-01-27】Stage HI分割: H1 + H2

役割: Stage G の table_inventory から定型表・構造化表を処理
      スキーマやテンプレートに基づいて表データを構造化

============================================
入力:
  - table_inventory: REF_ID付き表リスト（Stage G出力）
  - doc_type: ドキュメントタイプ
  - workspace: ワークスペース

出力:
  - processed_tables: 処理済み表データ
  - extracted_metadata: 表から抽出したメタデータ
  - table_text_fragments: H2から削除すべきテキスト断片

特徴:
  - 軽量モデル使用（Flash-Lite）または LLMなしのルールベース処理
  - カラムナ形式を辞書リストに復元
  - H2への入力量削減のため、処理済み表のテキストを返す
============================================
"""
import json
from typing import Dict, Any, List, Optional, Set
from loguru import logger

from .utils.table_parser import recompose_columnar_data, is_columnar_format, extract_table_text_for_removal


class StageH1Table:
    """Stage H1: 表処理専門"""

    # 定型表のスキーマ定義（doc_type別）
    TABLE_SCHEMAS = {
        "school_letter": {
            "weekly_schedule": {
                "required_columns": ["曜日", "時間", "科目"],
                "alt_columns": [["日", "時限", "教科"], ["曜", "時間割", "授業"]],
                "table_type": "schedule"
            },
            "event_list": {
                "required_columns": ["日付", "行事"],
                "alt_columns": [["日", "イベント"], ["月日", "予定"]],
                "table_type": "event"
            },
            "持ち物リスト": {
                "required_columns": ["品目", "数量"],
                "alt_columns": [["持ち物", "個数"], ["もちもの", "かず"]],
                "table_type": "item_list"
            }
        },
        "flyer": {
            "price_list": {
                "required_columns": ["商品", "価格"],
                "alt_columns": [["品名", "金額"], ["メニュー", "値段"]],
                "table_type": "price"
            },
            "schedule": {
                "required_columns": ["日時", "内容"],
                "alt_columns": [["時間", "プログラム"]],
                "table_type": "schedule"
            }
        },
        "default": {
            "generic_table": {
                "required_columns": [],
                "table_type": "generic"
            }
        }
    }

    def __init__(self, llm_client=None):
        """
        Args:
            llm_client: LLMクライアント（オプション、複雑な表処理時に使用）
        """
        self.llm = llm_client

    def process(
        self,
        table_inventory: List[Dict[str, Any]],
        doc_type: str = "default",
        workspace: str = "default",
        unified_text: str = ""
    ) -> Dict[str, Any]:
        """
        表データを処理

        Args:
            table_inventory: Stage G の table_inventory
            doc_type: ドキュメントタイプ
            workspace: ワークスペース
            unified_text: Stage G の unified_text（テキスト断片抽出用）

        Returns:
            {
                'processed_tables': List[Dict],  # 処理済み表
                'extracted_metadata': Dict,       # 表から抽出したメタデータ
                'table_text_fragments': List[str], # H2で削除すべきテキスト
                'removed_table_ids': Set[str],    # H2から除外する表のID
                'statistics': Dict                 # 処理統計
            }
        """
        logger.info(f"[Stage H1] 表処理開始: {len(table_inventory)}表 (doc_type={doc_type})")

        if not table_inventory:
            logger.info("[Stage H1] 表なし、スキップ")
            return {
                'processed_tables': [],
                'extracted_metadata': {},
                'table_text_fragments': [],
                'removed_table_ids': set(),
                'statistics': {'total': 0, 'processed': 0, 'skipped': 0}
            }

        processed_tables = []
        extracted_metadata = {}
        table_text_fragments = []
        removed_table_ids = set()
        unrepairable_tables = []  # G1で修復不能と判定された表
        stats = {'total': len(table_inventory), 'processed': 0, 'skipped': 0, 'unrepairable': 0}

        # doc_type に対応するスキーマを取得
        schemas = self.TABLE_SCHEMAS.get(doc_type, self.TABLE_SCHEMAS['default'])

        for table in table_inventory:
            ref_id = table.get('ref_id', 'UNKNOWN')
            table_title = table.get('table_title', '')
            table_type = table.get('table_type', 'unknown')

            # ============================================
            # G1の「白旗」検知: unrepairable フラグ
            # ============================================
            if table.get('status') == 'unrepairable':
                reason = table.get('unrepairable_reason', '理由不明')
                logger.warning(f"[Stage H1] 修復不能表をスキップ: {ref_id} - {reason}")

                # 修復不能表の情報を記録（レポート用）
                unrepairable_tables.append({
                    'ref_id': ref_id,
                    'table_title': table_title,
                    'reason': reason,
                    'page': table.get('page', 0)
                })
                stats['unrepairable'] += 1
                stats['skipped'] += 1

                # H2への通知用：この表は処理できなかったことを伝える
                removed_table_ids.add(ref_id)
                continue  # 次の表へ（ハルシネーション防止）

            logger.debug(f"[Stage H1] 表処理: {ref_id} - {table_title} ({table_type})")

            # カラムナ形式を辞書リストに変換
            rows_data = self._normalize_table_rows(table)

            # スキーママッチング
            matched_schema = self._match_schema(table, schemas)

            if matched_schema:
                # 定型表として処理
                logger.info(f"[Stage H1] 定型表検出: {ref_id} → {matched_schema['table_type']}")

                processed_table = {
                    'ref_id': ref_id,
                    'table_title': table_title,
                    'table_type': matched_schema['table_type'],
                    'schema_matched': True,
                    'columns': table.get('columns', []) or table.get('headers', []),
                    'rows': rows_data,
                    'row_count': len(rows_data),
                    'source': table.get('source', 'stage_g')
                }
                processed_tables.append(processed_table)

                # メタデータ抽出（特定のtable_typeに対して）
                meta = self._extract_metadata_from_table(processed_table, matched_schema['table_type'])
                if meta:
                    extracted_metadata.update(meta)

                # H2から削除すべきテキスト断片を収集
                fragments = extract_table_text_for_removal(table)
                table_text_fragments.extend(fragments)

                # この表はH1で処理済みとしてマーク
                removed_table_ids.add(ref_id)
                stats['processed'] += 1

            else:
                # 定型外の表は軽量処理のみ
                logger.debug(f"[Stage H1] 汎用表: {ref_id}")

                processed_table = {
                    'ref_id': ref_id,
                    'table_title': table_title,
                    'table_type': table_type or 'generic',
                    'schema_matched': False,
                    'columns': table.get('columns', []) or table.get('headers', []),
                    'rows': rows_data,
                    'row_count': len(rows_data),
                    'source': table.get('source', 'stage_g')
                }
                processed_tables.append(processed_table)

                # 汎用表も大きければH2から削除対象に
                if len(rows_data) >= 3:
                    fragments = extract_table_text_for_removal(table)
                    table_text_fragments.extend(fragments)
                    removed_table_ids.add(ref_id)
                    stats['processed'] += 1
                else:
                    stats['skipped'] += 1

        logger.info(f"[Stage H1] 完了: processed={stats['processed']}, skipped={stats['skipped']}, unrepairable={stats['unrepairable']}")

        # 修復不能表があれば警告
        if unrepairable_tables:
            logger.warning(f"[Stage H1] 修復不能表: {len(unrepairable_tables)}件 → H2へ通知済み")

        return {
            'processed_tables': processed_tables,
            'extracted_metadata': extracted_metadata,
            'table_text_fragments': table_text_fragments,
            'removed_table_ids': removed_table_ids,
            'unrepairable_tables': unrepairable_tables,  # G1で修復不能と判定された表の一覧
            'statistics': stats
        }

    def _normalize_table_rows(self, table: Dict) -> List[Dict]:
        """
        表の行データを正規化（カラムナ形式→辞書リスト）

        Args:
            table: 表データ

        Returns:
            辞書リスト形式の行データ
        """
        # columns + rows 形式（カラムナ）
        if 'columns' in table and 'rows' in table:
            if is_columnar_format(table):
                return recompose_columnar_data(table)

        # headers + rows 形式
        if 'headers' in table and 'rows' in table:
            headers = table['headers']
            rows = table['rows']
            result = []
            for row in rows:
                if isinstance(row, list):
                    row_dict = dict(zip(headers, row))
                    result.append(row_dict)
                elif isinstance(row, dict):
                    result.append(row)
            return result

        # rows のみ（ヘッダーなし）
        if 'rows' in table:
            return table['rows'] if isinstance(table['rows'], list) else []

        return []

    def _match_schema(self, table: Dict, schemas: Dict) -> Optional[Dict]:
        """
        表がスキーマにマッチするかチェック

        Args:
            table: 表データ
            schemas: スキーマ定義

        Returns:
            マッチしたスキーマ、またはNone
        """
        columns = table.get('columns', []) or table.get('headers', [])
        if not columns:
            return None

        columns_lower = [str(c).lower() for c in columns]

        for schema_name, schema in schemas.items():
            required = schema.get('required_columns', [])
            alt_sets = schema.get('alt_columns', [])

            # 必須カラムのチェック
            if required:
                required_lower = [c.lower() for c in required]
                if all(any(req in col for col in columns_lower) for req in required_lower):
                    return schema

            # 代替カラムセットのチェック
            for alt_set in alt_sets:
                alt_lower = [c.lower() for c in alt_set]
                if all(any(alt in col for col in columns_lower) for alt in alt_lower):
                    return schema

        return None

    def _extract_metadata_from_table(self, table: Dict, table_type: str) -> Dict[str, Any]:
        """
        表からメタデータを抽出

        Args:
            table: 処理済み表データ
            table_type: 表タイプ

        Returns:
            抽出したメタデータ
        """
        metadata = {}
        rows = table.get('rows', [])

        if table_type == 'schedule':
            # 時間割/スケジュール表
            schedule_items = []
            for row in rows:
                if isinstance(row, dict):
                    schedule_items.append(row)
            if schedule_items:
                metadata['weekly_schedule'] = schedule_items

        elif table_type == 'event':
            # イベントリスト
            events = []
            for row in rows:
                if isinstance(row, dict):
                    events.append(row)
            if events:
                metadata['event_list'] = events

        elif table_type == 'item_list':
            # 持ち物リスト
            items = []
            for row in rows:
                if isinstance(row, dict):
                    items.append(row)
            if items:
                metadata['required_items'] = items

        elif table_type == 'price':
            # 価格表
            prices = []
            for row in rows:
                if isinstance(row, dict):
                    prices.append(row)
            if prices:
                metadata['price_list'] = prices

        return metadata

    def remove_table_text_from_unified(
        self,
        unified_text: str,
        table_text_fragments: List[str]
    ) -> str:
        """
        unified_text から表関連テキストを削除

        H2への入力量を削減するため、H1で処理済みの表の
        テキスト表現を削除

        Args:
            unified_text: Stage G の unified_text
            table_text_fragments: 削除すべきテキスト断片

        Returns:
            軽量化された unified_text
        """
        if not table_text_fragments:
            return unified_text

        result = unified_text

        # 断片を長い順にソート（部分一致を避けるため）
        sorted_fragments = sorted(table_text_fragments, key=len, reverse=True)

        for fragment in sorted_fragments:
            if len(fragment) < 10:
                continue  # 短すぎる断片はスキップ

            # 断片を削除（複数回出現する可能性あり）
            if fragment in result:
                result = result.replace(fragment, '')

        # 連続する空行を整理
        import re
        result = re.sub(r'\n{3,}', '\n\n', result)

        original_len = len(unified_text)
        reduced_len = len(result)
        reduction = original_len - reduced_len

        logger.info(f"[Stage H1] テキスト軽量化: {original_len}→{reduced_len}文字 (-{reduction}文字, -{reduction*100//original_len if original_len > 0 else 0}%)")

        return result.strip()
