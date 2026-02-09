"""
Stage H1: Table Specialist (表処理専門)

【Ver 16.0】プロセッサーパターン対応

G8が各データセルに col_header / row_header を付与済み。
H1は cells_enriched からドメインタイプを検出し、
適するドメイン定義ファイル（domains/definitions/）を読み込み、
対応するプロセッサー（domains/processors/）を動的にロードして
ドメイン固有の前処理を実行してからピボットテーブルを構築。

============================================
アーキテクチャ原則:
  - H1 = ドメイン非依存のピボットエンジン
  - ドメイン固有ロジック = プロセッサーが担当
  - H1はプロセッサーを検出・ロード・実行するのみ

============================================
入力:
  - table_inventory: G8出力済みテーブルリスト（cells_enriched + header_map 付き）
  - unified_text: H2用テキスト（テーブルタグ置換用）

処理:
  1. cells_enriched からドメインタイプを検出
  2. domains/definitions/{domain_id}.json を読み込む
  3. domains/processors/{domain_id}_processor.py を動的ロード
  4. プロセッサー.process() でドメイン固有前処理を実行
  5. ピボットテーブル構築

出力:
  - processed_tables: ピボット形式の処理済み表
  - reduced_text: テーブルタグ埋め込み済みテキスト
============================================
"""
import re
import json
import os
from collections import defaultdict, OrderedDict
from typing import Dict, Any, List, Optional
from loguru import logger

from ..utils.table_parser import extract_table_text_for_removal


class StageH1Table:
    """Stage H1: 表処理専門（Ver 16.0: プロセッサーパターン）"""

    def __init__(self, llm_client=None, model=None):
        """
        Args:
            llm_client: 互換性維持（使用しない）
            model: 互換性維持（使用しない）
        """
        # LLMは使わない（シグネチャのみ互換維持）
        self._domain_definitions = {}  # ドメイン定義キャッシュ
        self._processors = {}          # プロセッサーキャッシュ

    def _detect_domain_type(self, all_tagged_texts: List[Dict]) -> Optional[str]:
        """
        all_tagged_texts からドメインタイプを検出

        表外テキストを優先して検索し、ドメイン定義ファイルの fingerprint と照合
        """
        # ドメイン定義ファイルのディレクトリ
        base_dir = os.path.dirname(__file__)
        def_dir = os.path.join(base_dir, 'domains', 'definitions')

        # 全ドメイン定義ファイルを取得
        try:
            domain_files = [f for f in os.listdir(def_dir) if f.endswith('.json')]
        except FileNotFoundError:
            logger.warning(f"[H1] ドメイン定義ディレクトリなし: {def_dir}")
            return None

        # 表外テキストを優先して結合（type != 'cell'）
        non_table_texts = [
            tt.get('text', '')
            for tt in all_tagged_texts
            if tt.get('type') != 'cell'
        ]
        table_texts = [
            tt.get('text', '')
            for tt in all_tagged_texts
            if tt.get('type') == 'cell'
        ]

        # 表外テキストを優先
        full_text = " ".join(non_table_texts + table_texts)

        # 各ドメイン定義の fingerprint と照合
        for domain_file in domain_files:
            domain_id = domain_file.replace('.json', '')
            def_path = os.path.join(def_dir, domain_file)

            try:
                with open(def_path, encoding='utf-8') as f:
                    # 最初の数行（fingerprint部分のみ）を読む
                    content = f.read()
                    definition = json.loads(content)

                fingerprint = definition.get('fingerprint', {})
                keywords = fingerprint.get('keywords', [])

                # キーワード照合
                if keywords and any(kw in full_text for kw in keywords):
                    logger.info(f"[H1] ドメイン検出: {domain_id} (keywords: {keywords})")
                    return domain_id

            except (FileNotFoundError, json.JSONDecodeError) as e:
                logger.warning(f"[H1] ドメイン定義読み込み失敗: {domain_file} - {e}")
                continue

        return None

    def _load_domain_definition(self, domain_id: str) -> Optional[Dict]:
        """
        ドメイン定義ファイルを読み込む
        """
        if domain_id in self._domain_definitions:
            return self._domain_definitions[domain_id]

        # パスを構築
        base_dir = os.path.dirname(__file__)
        def_path = os.path.join(
            base_dir, 'domains', 'definitions', f'{domain_id}.json'
        )

        try:
            with open(def_path, encoding='utf-8') as f:
                definition = json.load(f)
                self._domain_definitions[domain_id] = definition
                logger.info(f"[H1] ドメイン定義読み込み: {domain_id}")
                return definition
        except FileNotFoundError:
            logger.warning(f"[H1] ドメイン定義ファイルなし: {def_path}")
            return None

    def _load_processor(self, domain_id: str, domain_def: Dict) -> Optional[Any]:
        """
        ドメイン固有プロセッサーを動的ロード

        Args:
            domain_id: ドメインID（例: "yotsuya_hensachi"）
            domain_def: ドメイン定義辞書

        Returns:
            プロセッサーインスタンス、またはNone（プロセッサーなし）
        """
        if domain_id in self._processors:
            return self._processors[domain_id]

        # ドメインID から推定するプロセッサーモジュール名
        # 例: "yotsuya_hensachi" → "yotsuya"
        # 最初のアンダースコアまでをプロセッサー名とする
        processor_name = domain_id.split('_')[0] if '_' in domain_id else domain_id

        # プロセッサーモジュールを動的インポート
        try:
            from importlib import import_module
            module_path = f".domains.processors.{processor_name}_processor"
            module = import_module(module_path, package=__package__)

            # クラス名: YotsuyaProcessor （先頭を大文字化 + Processor）
            class_name = f"{processor_name.capitalize()}Processor"
            processor_class = getattr(module, class_name)

            # インスタンス化
            processor = processor_class(domain_def)
            self._processors[domain_id] = processor

            logger.info(f"[H1] プロセッサーロード: {class_name}")
            return processor

        except (ImportError, AttributeError) as e:
            logger.error(f"[H1] プロセッサーロード失敗: {domain_id} - {e}")
            import traceback
            logger.error(f"[H1] Traceback: {traceback.format_exc()}")
            self._processors[domain_id] = None
            return None

    def process(
        self,
        table_inventory: List[Dict[str, Any]],
        all_tagged_texts: List[Dict[str, Any]] = None,
        doc_type: str = "default",
        workspace: str = "default",
        unified_text: str = "",
        raw_tokens: List[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        G8 enriched セルからピボットテーブルを構築

        1. ドメインタイプを検出
        2. ドメイン定義ファイルを読み込む
        3. ドメイン定義に基づいて前処理（肩付き日付処理など）
        4. ピボットテーブル構築

        Args:
            table_inventory: G8出力済みテーブルリスト
            doc_type: ドキュメントタイプ
            workspace: ワークスペース
            unified_text: H2用テキスト
            raw_tokens: E8トークン座標（互換性維持、未使用）

        Returns:
            H2互換の出力構造
        """
        logger.info(f"[Stage H1] ピボット構築開始: {len(table_inventory)}表")

        if not table_inventory:
            logger.info("[Stage H1] 表なし、スキップ")
            return {
                'processed_tables': [],
                'extracted_metadata': {},
                'table_text_fragments': [],
                'removed_table_ids': [],
                'unrepairable_tables': [],
                'reduced_text': unified_text,
                'h2_hint': '',
                'statistics': {'total': 0, 'processed': 0, 'skipped': 0}
            }

        # ドメインタイプ検出（G4の読み順済みテキストから）
        domain_id = None
        domain_def = None
        processor = None

        if all_tagged_texts:
            domain_id = self._detect_domain_type(all_tagged_texts)

            if domain_id:
                logger.info(f"[H1] ドメイン検出: {domain_id}")
                domain_def = self._load_domain_definition(domain_id)
                if domain_def:
                    processor = self._load_processor(domain_id, domain_def)
            else:
                logger.info("[H1] ドメイン検出失敗、デフォルト処理")
        else:
            logger.warning("[H1] all_tagged_texts なし、ドメイン検出スキップ")

        processed_tables = []
        table_text_fragments = []
        removed_table_ids = []
        unrepairable_tables = []
        clean_text = unified_text
        stats = {'total': len(table_inventory), 'processed': 0, 'skipped': 0}

        for table in table_inventory:
            ref_id = table.get('ref_id', 'UNKNOWN')
            table_title = table.get('table_title', '')
            cells_enriched = table.get('cells_enriched', [])
            header_map = table.get('header_map', {})

            # unrepairable チェック
            if table.get('status') == 'unrepairable':
                reason = table.get('unrepairable_reason', '理由不明')
                logger.warning(f"[Stage H1] 修復不能表をスキップ: {ref_id} - {reason}")
                unrepairable_tables.append({
                    'ref_id': ref_id,
                    'table_title': table_title,
                    'reason': reason,
                })
                stats['skipped'] += 1
                removed_table_ids.append(ref_id)
                continue

            if not cells_enriched:
                # cells_flat フォールバック
                cells_enriched = table.get('cells_flat', [])

            if not cells_enriched:
                logger.warning(f"[Stage H1] セルなし: {ref_id}")
                stats['skipped'] += 1
                continue

            # ドメイン固有プロセッサーによる前処理
            if processor:
                logger.info(f"[Stage H1] {ref_id}: プロセッサー実行開始")
                processor.process(cells_enriched)
                logger.info(f"[Stage H1] {ref_id}: プロセッサー実行完了")
            else:
                logger.warning(f"[Stage H1] {ref_id}: プロセッサーなし（ドメイン固有処理スキップ）")

            # 入力ログ
            _total = len(cells_enriched)
            _headers = sum(1 for c in cells_enriched if c.get('is_header', False))
            _data = sum(1 for c in cells_enriched if not c.get('is_header', False) and str(c.get('text', '')).strip())
            _with_col = sum(1 for c in cells_enriched if c.get('col_header') is not None and not c.get('is_header', False))
            _with_row = sum(1 for c in cells_enriched if c.get('row_header') is not None and not c.get('is_header', False))
            logger.info(f"[Stage H1] {ref_id} 入力: total={_total}, header={_headers}, data={_data}, col_header付={_with_col}/{_data}, row_header付={_with_row}/{_data}")

            # ピボット構築（非表示セルはフィルタリング）
            visible_cells = [c for c in cells_enriched if not c.get('_hidden', False)]
            pivot = self._pivot_enriched_cells(visible_cells, header_map)
            columns = pivot['columns']
            rows = pivot['rows']

            logger.info(f"[Stage H1] {ref_id} 出力: columns={columns}, rows={len(rows)}行")
            for i, row in enumerate(rows[:3]):
                logger.info(f"[Stage H1]   row[{i}]: {row}")
            if len(rows) > 3:
                logger.info(f"[Stage H1]   ... 残り{len(rows) - 3}行")

            # テキスト断片を収集（H2での重複除去用）
            fragments = extract_table_text_for_removal(table)
            table_text_fragments.extend(fragments)

            # テキスト中のテーブル領域をタグに置換
            tag = f"[表{ref_id}: {table_title}]" if table_title else f"[表{ref_id}]"
            clean_text = self._replace_table_with_tag(clean_text, fragments, tag)

            # processed_table 構築
            processed_table = {
                'ref_id': ref_id,
                'table_title': table_title,
                'table_type': 'pivot',
                'columns': columns,
                'rows': rows,
                'row_count': len(rows),
                'source': 'stage_h1_g8_pivot',
            }

            # UI表示用フォーマット付与
            self._add_display_formats(processed_table, table)

            processed_tables.append(processed_table)
            removed_table_ids.append(ref_id)
            stats['processed'] += 1

        logger.info(f"[Stage H1] 完了: processed={stats['processed']}, skipped={stats['skipped']}")

        return {
            'processed_tables': processed_tables,
            'extracted_metadata': {},
            'table_text_fragments': table_text_fragments,
            'removed_table_ids': removed_table_ids,
            'unrepairable_tables': unrepairable_tables,
            'reduced_text': clean_text,
            'h2_hint': '',
            'statistics': stats,
        }

    # ------------------------------------------------------------------
    # ピボット構築
    # ------------------------------------------------------------------

    def _find_row_label(
        self,
        cells_enriched: List[Dict],
        header_map: Dict,
    ) -> str:
        """
        行ラベル名を特定する（例: "偏差値"）

        row_header_cols を持つパネルの row=0 ヘッダーセルのテキストを返す。
        row_header_cols の列自体に row=0 セルがない場合は、
        同パネル row=0 の任意のヘッダーセルを採用する。
        """
        panels = header_map.get('panels', {})
        for pk, cfg in panels.items():
            rh_cols = cfg.get('row_header_cols', [])
            if not rh_cols:
                continue

            # このパネルの panel_id を取得（"P0" → "0"）
            pid = pk.lstrip('P')

            # row=0 のセルを収集
            row0_cells = [
                c for c in cells_enriched
                if str(c.get('panel_id', 0) or 0) == pid and c.get('row', -1) == 0
            ]

            # まず row_header_cols に属する row=0 セルを探す
            for cell in row0_cells:
                if cell.get('col', -1) in rh_cols:
                    text = str(cell.get('text', '')).strip()
                    if text:
                        return text

            # なければ同パネル row=0 の任意のヘッダーセルを採用
            for cell in row0_cells:
                if cell.get('is_header', False):
                    text = str(cell.get('text', '')).strip()
                    if text:
                        return text

        return "行ヘッダー"

    def _pivot_enriched_cells(
        self,
        cells_enriched: List[Dict],
        header_map: Dict,
    ) -> Dict[str, Any]:
        """
        enriched セルからピボットテーブルを構築

        Returns:
            {"columns": [...], "rows": [{...}, ...]}
        """
        # データセルのみ抽出（非ヘッダー、テキストあり）
        data_cells = [
            c for c in cells_enriched
            if not c.get('is_header', False) and str(c.get('text', '')).strip()
        ]

        if not data_cells:
            return {'columns': [], 'rows': []}

        # 行ヘッダー: 出現順を保持した unique リスト
        row_headers = list(OrderedDict.fromkeys(
            c.get('row_header') for c in data_cells if c.get('row_header') is not None
        ))

        # 列ヘッダー: global_col の最小値でソート（左→右の物理順）
        col_header_min_gc = {}
        for c in data_cells:
            ch = c.get('col_header')
            if ch is not None:
                gc = c.get('global_col', c.get('col', 0))
                if ch not in col_header_min_gc or gc < col_header_min_gc[ch]:
                    col_header_min_gc[ch] = gc
        col_headers = sorted(col_header_min_gc.keys(), key=lambda h: col_header_min_gc[h])

        row_label = self._find_row_label(cells_enriched, header_map)

        # (row_header, col_header) でグルーピング
        groups = defaultdict(list)
        for c in data_cells:
            rh = c.get('row_header')
            ch = c.get('col_header')
            groups[(rh, ch)].append(str(c.get('text', '')).strip())

        # ピボット行を構築
        columns = [row_label] + col_headers
        rows = []
        for rh in row_headers:
            record = {row_label: rh}
            for ch in col_headers:
                record[ch] = groups.get((rh, ch), [])
            rows.append(record)

        return {'columns': columns, 'rows': rows}

    # ------------------------------------------------------------------
    # テキスト置換
    # ------------------------------------------------------------------

    def _replace_table_with_tag(
        self,
        text: str,
        fragments: List[str],
        tag: str,
    ) -> str:
        """
        テキスト中のテーブル断片をタグに置換

        長い断片から順に置換し、最初に見つかった位置にタグを挿入。
        """
        if not fragments or not text:
            return text

        tag_inserted = False
        sorted_fragments = sorted(fragments, key=len, reverse=True)

        for frag in sorted_fragments:
            if len(frag) < 10:
                continue
            if frag in text:
                if not tag_inserted:
                    text = text.replace(frag, tag, 1)
                    tag_inserted = True
                    # 残りの同じ断片も削除
                    text = text.replace(frag, '')
                else:
                    text = text.replace(frag, '')

        # 連続空行を整理
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()

    # ------------------------------------------------------------------
    # UI表示用フォーマット
    # ------------------------------------------------------------------

    def _add_display_formats(
        self,
        processed_table: Dict[str, Any],
        original_table: Dict[str, Any],
    ) -> None:
        """
        処理済み表にUI表示用の flat_data / grid_data を追加

        既にドメインハンドラ等で生成済みの場合はスキップ。
        """
        has_flat = 'flat_data' in processed_table and processed_table['flat_data']
        has_grid = 'grid_data' in processed_table and processed_table['grid_data']

        if has_flat and has_grid:
            return

        rows = processed_table.get('rows', [])
        columns = processed_table.get('columns', [])

        # flat_data: ピボット行をそのまま使用（値リストを文字列に展開）
        if not has_flat:
            flat_data = []
            for row in rows:
                if isinstance(row, dict):
                    flat_row = {}
                    for k, v in row.items():
                        if isinstance(v, list):
                            flat_row[k] = ", ".join(str(x) for x in v)
                        else:
                            flat_row[k] = v
                    flat_data.append(flat_row)
                else:
                    flat_data.append(row)

            processed_table['flat_data'] = flat_data
            processed_table['flat_columns'] = columns

        # grid_data: 2D配列表現
        if not has_grid:
            grid_data = {
                'columns': columns,
                'rows': [],
                'cells': original_table.get('cells', []),
            }
            for row in rows:
                if isinstance(row, dict):
                    grid_row = []
                    for col in columns:
                        val = row.get(col, [])
                        if isinstance(val, list):
                            grid_row.append(", ".join(str(x) for x in val))
                        else:
                            grid_row.append(str(val) if val else "")
                    grid_data['rows'].append(grid_row)

            processed_table['grid_data'] = grid_data
