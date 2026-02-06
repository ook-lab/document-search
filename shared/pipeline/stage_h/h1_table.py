"""
Stage H1: Table Specialist (表処理専門)

【Ver 10.8】ドメイン理解専門 + プラグイン式ドメインハンドラ

役割: Stage G の table_inventory から定型表・構造化表を処理
      スキーママッチング・メタデータ抽出・カラムナ復元のみ担当

============================================
入力:
  - table_inventory: REF_ID付き表リスト（Stage G出力、G4で座標ロック済み）
  - doc_type: ドキュメントタイプ
  - workspace: ワークスペース

出力:
  - processed_tables: 処理済み表データ
  - extracted_metadata: 表から抽出したメタデータ
  - table_text_fragments: H2から削除すべきテキスト断片

特徴:
  - 物理座標ロックはG4で完了済み（H1は一切関与しない）
  - スキーママッチング・メタデータ抽出のみ
  - カラムナ形式を辞書リストに復元
  - H2への入力量削減のため、処理済み表のテキストを返す
  - ドメイン固有ロジックは domains/ フォルダにプラグイン式で配置
============================================
"""
import re
from typing import Dict, Any, List, Optional, Set
from loguru import logger

from ..utils.table_parser import recompose_columnar_data, is_columnar_format, extract_table_text_for_removal
from .domains import DOMAIN_HANDLERS


class StageH1Table:
    """Stage H1: 表処理専門（Ver 10.8: プラグイン式ドメインハンドラ）"""

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
        # ドメインハンドラをインスタンス化
        self.domain_handlers = [handler_class() for handler_class in DOMAIN_HANDLERS]
        logger.debug(f"[Stage H1] {len(self.domain_handlers)}個のドメインハンドラを登録")

    def process(
        self,
        table_inventory: List[Dict[str, Any]],
        doc_type: str = "default",
        workspace: str = "default",
        unified_text: str = "",
        raw_tokens: List[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        表データを処理

        Args:
            table_inventory: Stage G の table_inventory
            doc_type: ドキュメントタイプ
            workspace: ワークスペース
            unified_text: Stage G の unified_text（テキスト断片抽出用）
            raw_tokens: E8のトークン座標（肩付き注釈の精密判定用）

        Returns:
            {
                'processed_tables': List[Dict],  # 処理済み表
                'extracted_metadata': Dict,       # 表から抽出したメタデータ
                'table_text_fragments': List[str], # H2で削除すべきテキスト
                'removed_table_ids': Set[str],    # H2から除外する表のID
                'statistics': Dict                 # 処理統計
            }
        """
        raw_tokens = raw_tokens or []
        logger.info(f"[Stage H1] 表処理開始: {len(table_inventory)}表 (doc_type={doc_type}, raw_tokens={len(raw_tokens)})")

        if not table_inventory:
            logger.info("[Stage H1] 表なし、スキップ")
            return {
                'processed_tables': [],
                'extracted_metadata': {},
                'table_text_fragments': [],
                'removed_table_ids': [],
                'reduced_text': unified_text,  # 軽量化なし
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

            # ============================================
            # ドメインハンドラによる専用パース（プラグイン式）
            # ============================================
            domain_handled = False
            for handler in self.domain_handlers:
                if handler.detect(table_title, unified_text):
                    handler_name = handler.__class__.__name__
                    logger.info(f"[Stage H1] ドメイン検出: {handler_name} → {ref_id}")
                    # raw_tokensを渡して肩付き注釈の精密判定を可能に
                    processed_table = handler.process(
                        table, ref_id, table_title, raw_tokens=raw_tokens
                    )
                    if processed_table:
                        processed_tables.append(processed_table)
                        fragments = extract_table_text_for_removal(table)
                        table_text_fragments.extend(fragments)
                        removed_table_ids.add(ref_id)
                        stats['processed'] += 1
                        domain_handled = True
                        break  # 最初にマッチしたハンドラで処理完了

            if domain_handled:
                continue  # 次の表へ

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

                # 【Ver 6.7】スキップ禁止: どんな表も処理対象とする
                # F9/F10で「表」と判定されたものは必ず価値があるデータ
                fragments = extract_table_text_for_removal(table)
                table_text_fragments.extend(fragments)
                removed_table_ids.add(ref_id)
                stats['processed'] += 1

        logger.info(f"[Stage H1] 完了: processed={stats['processed']}, skipped={stats['skipped']}, unrepairable={stats['unrepairable']}")

        # 修復不能表があれば警告
        if unrepairable_tables:
            logger.warning(f"[Stage H1] 修復不能表: {len(unrepairable_tables)}件 → H2へ通知済み")

        # ============================================
        # 全表に flat_data / grid_data を付与（UI表示用）
        # ============================================
        for i, processed_table in enumerate(processed_tables):
            original_table = table_inventory[i] if i < len(table_inventory) else {}
            self._add_display_formats(processed_table, original_table)

        # H2用の軽量化テキストを生成
        reduced_text = self.remove_table_text_from_unified(unified_text, table_text_fragments)

        return {
            'processed_tables': processed_tables,
            'extracted_metadata': extracted_metadata,
            'table_text_fragments': table_text_fragments,
            'removed_table_ids': list(removed_table_ids),  # JSON用にlistへ変換
            'unrepairable_tables': unrepairable_tables,  # G1で修復不能と判定された表の一覧
            'reduced_text': reduced_text,  # H2用の軽量化テキスト
            'statistics': stats
        }

    def _normalize_table_rows(self, table: Dict) -> List[Dict]:
        """
        表の行データを正規化（カラムナ形式→辞書リスト）
        【Ver 11.2】F8/G6のrow/colインデックスを尊重し、左詰めを防止

        Args:
            table: 表データ

        Returns:
            辞書リスト形式の行データ
        """
        # cells形式（G6からの入力）
        if 'cells' in table and table['cells']:
            cells = table['cells']

            # F8/G6で付与された論理インデックス（row/col）を最優先で使用
            # 列位置を固定することで、空セルがあっても左詰めを防止
            indexed_rows = {}
            max_col = 0

            for cell in cells:
                text = cell.get('text', '').strip()
                # 空セルも位置情報として保持（左詰め防止のため）

                # F8/G6のrow/colを取得
                r = cell.get('row')
                c = cell.get('col')

                # インデックスがない場合のみ座標フォールバック（救済措置）
                if r is None:
                    bbox = cell.get('bbox', [0, 0, 0, 0])
                    r = int(bbox[1] / 10) * 10
                    logger.warning(f"[Stage H1] row欠落、座標フォールバック: y={bbox[1]} → row={r}")

                if c is None:
                    bbox = cell.get('bbox', [0, 0, 0, 0])
                    c = int(bbox[0] / 50) * 50  # X座標から仮列番号
                    logger.warning(f"[Stage H1] col欠落、座標フォールバック: x={bbox[0]} → col={c}")

                if r not in indexed_rows:
                    indexed_rows[r] = {}

                # 絶対的な列番号をキーにする（左詰めを防止）
                col_key = f'col_{c}'
                indexed_rows[r][col_key] = text
                if c > max_col:
                    max_col = c

            # 行番号順にソートして辞書リストを生成
            result = []
            for r in sorted(indexed_rows.keys()):
                row_data = indexed_rows[r]
                # テキストが1つでもある行を採用
                if any(v for v in row_data.values()):
                    result.append(row_data)

            logger.info(f"[Stage H1] F8/G6インデックス使用: {len(result)}行, 最大列={max_col}")
            return result

        # columns + rows 形式（カラムナ）
        if 'columns' in table and 'rows' in table:
            if is_columnar_format(table):
                return recompose_columnar_data(table)

        # headers + rows 形式
        if 'headers' in table and 'rows' in table:
            headers = table.get('headers', []) or []
            rows = table.get('rows', []) or []
            result = []

            for row_idx, row in enumerate(rows):
                if isinstance(row, list):
                    # 【データ救済】ヘッダーが不足している場合、仮ヘッダーを生成
                    effective_headers = list(headers)  # コピー
                    while len(effective_headers) < len(row):
                        effective_headers.append(f'column_{len(effective_headers) + 1}')

                    # 全データを辞書に格納（ヘッダーがなくても消さない）
                    row_dict = {}
                    for i, value in enumerate(row):
                        if i < len(effective_headers):
                            key = effective_headers[i] or f'column_{i + 1}'
                        else:
                            key = f'column_{i + 1}'
                        # 値が存在すれば必ず保持
                        if value is not None and str(value).strip():
                            row_dict[key] = value

                    # 空でない行のみ追加
                    if row_dict:
                        result.append(row_dict)

                elif isinstance(row, dict):
                    if row:  # 空でない辞書のみ
                        result.append(row)

            return result

        # rows のみ（ヘッダーなし）→ 仮ヘッダーで辞書化
        if 'rows' in table:
            rows = table['rows']
            if not isinstance(rows, list):
                return []

            result = []
            for row in rows:
                if isinstance(row, list):
                    # 仮ヘッダーで辞書化
                    row_dict = {f'column_{i + 1}': v for i, v in enumerate(row) if v is not None and str(v).strip()}
                    if row_dict:
                        result.append(row_dict)
                elif isinstance(row, dict) and row:
                    result.append(row)
            return result

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

    def _add_display_formats(
        self,
        processed_table: Dict[str, Any],
        original_table: Dict[str, Any]
    ) -> None:
        """
        処理済み表にUI表示用の2形式を追加（汎用）

        Args:
            processed_table: 処理済み表データ（変更される）
            original_table: 元の表データ（cells等を含む）
        """
        rows = processed_table.get('rows', [])
        columns = processed_table.get('columns', [])

        # ============================================
        # 1. flat_data: 正規化されたフラット表
        # ============================================
        flat_data = []
        flat_columns = []

        if rows:
            if isinstance(rows[0], dict):
                # オブジェクト配列 → キーを収集
                all_keys = set()
                for row in rows:
                    if isinstance(row, dict):
                        all_keys.update(row.keys())
                flat_columns = sorted(all_keys)
                flat_data = rows
            elif isinstance(rows[0], list):
                # 2D配列 → 辞書に変換
                flat_columns = columns if columns else [f'列{i+1}' for i in range(len(rows[0]))]
                for row in rows:
                    row_dict = {flat_columns[i]: v for i, v in enumerate(row) if i < len(flat_columns)}
                    flat_data.append(row_dict)
            else:
                flat_data = rows
                flat_columns = columns

        processed_table['flat_data'] = flat_data
        processed_table['flat_columns'] = flat_columns

        # ============================================
        # 2. grid_data: 元の表構造を保持
        # ============================================
        grid_data = {
            'rows': [],
            'columns': [],
            'cells': []
        }

        # 元のcells情報があれば使用（元の表構造を復元）
        cells = original_table.get('cells', [])
        if cells:
            # セルをY座標でグループ化
            rows_by_y = {}
            all_x = set()
            for cell in cells:
                bbox = cell.get('bbox', [0, 0, 0, 0])
                y_key = int(bbox[1] / 10) * 10
                x_key = int(bbox[0] / 10) * 10
                all_x.add(x_key)

                if y_key not in rows_by_y:
                    rows_by_y[y_key] = {}
                rows_by_y[y_key][x_key] = cell.get('text', '')

            sorted_y = sorted(rows_by_y.keys())
            sorted_x = sorted(all_x)

            grid_data['columns'] = [f'列{i+1}' for i in range(len(sorted_x))]
            for y in sorted_y:
                grid_data['rows'].append([rows_by_y[y].get(x, '') for x in sorted_x])

            grid_data['cells'] = cells

        else:
            # cellsがない場合はflat_dataから復元
            grid_data['columns'] = flat_columns
            for row in flat_data:
                if isinstance(row, dict):
                    grid_data['rows'].append([row.get(c, '') for c in flat_columns])
                elif isinstance(row, list):
                    grid_data['rows'].append(row)

        processed_table['grid_data'] = grid_data

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
