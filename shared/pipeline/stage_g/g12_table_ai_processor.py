"""
G-12: Table AI Processor（表のAI処理）

設計思想（Ver 2.0 - ヘッダー理解の本質）:

【表の2つの基本パターン】
1. パターン1: ヘッダー無し（完全リスト）
   - 横串に対応関係があるだけ
   - 上から順に行を処理するだけ

2. パターン2: ヘッダー有り
   - データ部分：行方向も列方向も必ず共通の種類
   - ヘッダー部分：データ範囲の共通点から溢れた行・列

【ヘッダーとデータの本質的な違い】
- ヘッダー：カテゴリー名（抽象的）
  例：身長、血液型、科目、役職名
- データ：具体的な値
  例：170、A、国語、学級委員長

【重要な注意点】
1. 暗黙的ヘッダー：明示されていなくても、データの共通性からヘッダーを推測
2. 抽象化レベル：身長（数値）と血液型（文字）は、「カテゴリー名」として共通
3. 判定基準：表面的な型（数値/文字）ではなく、意味的な役割で判断

【判定アルゴリズム】
1. 各行/列のセルを分析
2. 抽象化レベルを判定（カテゴリー名 vs 具体値）
3. 共通性を検出
4. データ範囲から溢れた行/列 = ヘッダー
"""

from typing import Dict, Any, List, Optional, Tuple
from loguru import logger
import json
import re
from shared.common.database.client import DatabaseClient


class G12TableAIProcessor:
    """G-12: Table AI Processor"""

    def __init__(self, document_id=None, api_key: Optional[str] = None, model_name: str = "gemini-2.5-flash-lite"):
        self.document_id = document_id
        self.model_name = model_name
        self.api_key = api_key
        self.model = None

        try:
            import google.generativeai as genai
            if api_key:
                genai.configure(api_key=api_key)
                self.model = genai.GenerativeModel(model_name)
                logger.info(f"[G-12] モデル初期化: {model_name}")
            else:
                logger.warning("[G-12] API key が設定されていません")
        except ImportError:
            logger.warning("[G-12] google-generativeai がインストールされていません")

    # =========================================================================
    # エントリーポイント
    # =========================================================================

    def process(self, structured_tables: List[Dict[str, Any]], year_context: Optional[int] = None, log_file=None) -> Dict[str, Any]:
        """
        G-11の表データをAI理解に基づいて再構造化する。

        Args:
            structured_tables: G-11出力 [{'headers': list, 'rows': list, 'table_id': str}, ...]
            year_context: 年度コンテキスト（日付推定に使用）
            log_file: ログファイルパス（オプション）

        Returns:
            {
                'success': bool,
                'table_analyses': list,  # G-12の結果
                'structured_tables': list,  # G-11の結果（失われないように保持）
                'tokens_used': int
            }
        """
        _sink_id = None
        if log_file:
            _sink_id = logger.add(
                str(log_file),
                format="{time:HH:mm:ss} | {level:<5} | {message}",
                filter=lambda r: "[G-12]" in r["message"],
                level="DEBUG",
                encoding="utf-8",
            )
        try:
            return self._process_impl(structured_tables, year_context)
        finally:
            if _sink_id is not None:
                logger.remove(_sink_id)

    def _process_impl(self, structured_tables: List[Dict[str, Any]], year_context: Optional[int] = None) -> Dict[str, Any]:
        """process() の実装本体"""
        logger.info("[G-12] ========== AI処理開始 ==========")
        logger.info(f"[G-12] モデル: {self.model_name}")
        logger.info(f"[G-12] 年度コンテキスト: {year_context if year_context else 'なし（AIが推定）'}")

        # ★年度情報を保存（プロンプト構築で使用）
        self.year_context = year_context
        logger.info(f"[G-12] 入力表数: {len(structured_tables)}個")

        if not self.model:
            return self._error_result("Gemini API not available")

        if not structured_tables:
            return {
                'success': True,
                'table_analyses': [],
                'structured_tables': [],
                'tokens_used': 0
            }

        table_analyses = []
        total_tokens = 0

        for i, table in enumerate(structured_tables, 1):
            table_id = table.get('table_id', f'Table_{i}')
            logger.info(f"\n[G-12] 表 {i}/{len(structured_tables)}: {table_id}")

            result, tokens = self._process_one_table(table)
            total_tokens += tokens

            # 複数セクションを個別の表として展開
            if result.get('table_type') == 'multi_section':
                sections = result.get('sections', [])
                logger.info(f"[G-12] ✂️ 表 {table_id} を {len(sections)}個のセクションに分割")

                for sec_idx, section in enumerate(sections, 1):
                    section_table = {
                        'table_id': f"{table_id}_S{sec_idx}",
                        'table_type': section.get('table_type', 'structured'),
                        'description': section.get('title', f'セクション{sec_idx}'),
                        'sections': [section],  # UI契約: 常に sections を含める
                        'metadata': section.get('metadata', {})
                    }
                    table_analyses.append(section_table)
                    logger.info(f"  ├─ {section_table['table_id']}: {section_table['description']} ({len(section.get('data', []))}行)")
            else:
                # 単一セクション表はそのまま追加
                result['table_id'] = table_id
                table_analyses.append(result)

        logger.info(f"\n[G-12] 完了: {len(table_analyses)}表, {total_tokens}トークン")

        result = {
            'success': True,
            'table_analyses': table_analyses,
            'structured_tables': structured_tables,
            'tokens_used': total_tokens
        }

        # Supabaseに保存
        if self.document_id:
            try:
                db = DatabaseClient(use_service_role=True)
                db.client.table('Rawdata_FILE_AND_MAIL').update({
                    'g12_table_analyses': table_analyses
                }).eq('id', self.document_id).execute()
                logger.info(f"[G-12] ✓ g12_table_analyses を Supabase に保存: {len(table_analyses)}表")
            except Exception as e:
                logger.error(f"[G-12] Supabase保存エラー: {e}")

        return result

    # =========================================================================
    # 1表の処理
    # =========================================================================

    def _process_one_table(self, table: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
        """1つの表を処理して再構造化する（物理パターン優先）"""
        headers = table.get('headers', [])
        rows = table.get('rows', [])

        # G-11のheadersとrowsを結合して完全な2D配列を作る
        full = []
        if headers:
            full.append(headers)
        full.extend(rows)

        if not full:
            return {'table_type': 'empty', 'sections': []}, 0

        logger.info(f"[G-12] 入力データ全行:")
        for row_idx, row in enumerate(full):
            logger.info(f"[G-12]   行{row_idx}: {row}")

        # Step 0.5: 自動列名のみの行を除外（Row0汚染対策）
        import re
        def _is_auto_column_name_row(row):
            """行が自動列名のみで構成されているかチェック"""
            non_empty_cells = [cell for cell in row if cell and str(cell).strip()]
            if not non_empty_cells:
                return False  # 空行は除外しない
            # 全ての非空セルが Col\d+ または 列\d+ パターンか
            return all(re.match(r'^(Col|列)\d+$', str(cell).strip()) for cell in non_empty_cells)

        original_row_count = len(full)
        full = [row for row in full if not _is_auto_column_name_row(row)]

        if len(full) < original_row_count:
            logger.info(f"[G-12] 自動列名のみの行を除外: {original_row_count}行 → {len(full)}行")

        if not full:
            return {'table_type': 'empty', 'sections': []}, 0

        tokens = 0

        # Step 0: 物理的な繰り返しパターン検出（AI不要、最優先）
        logger.info(f"[G-12] Step 0: 物理的な繰り返しパターン検出")
        column_split_groups = self._detect_column_split_by_header_repetition(full, [])
        row_split_groups = self._detect_repeating_headers(full, [])

        # Step 1: 物理パターンに基づいてセクション分割
        processed_sections = []
        total_records = 0

        if column_split_groups:
            # 列分割が必要な場合、各列グループを個別のサブセクションとして処理
            logger.info(f"[G-12] ✂️ 列分割実行: {len(column_split_groups)}個のサブセクションに分割")

            for col_group_idx, col_group in enumerate(column_split_groups, 1):
                col_group_name = col_group['group_name']
                logger.info(f"\n[G-12]   サブセクション {col_group_idx}/{len(column_split_groups)}: {col_group_name}")

                # 列範囲でデータを切り出し
                col_section_data = self._extract_column_range(
                    full,
                    col_group['col_start'],
                    col_group['col_end']
                )

                if not col_section_data:
                    continue

                # 再度共通性分析
                col_commonality, tokens2 = self._analyze_data_commonality(
                    col_section_data,
                    f"{table.get('table_id', '')}_{col_group_name}"
                )
                tokens += tokens2

                if not col_commonality.get('success'):
                    logger.warning(f"[G-12]   列グループ {col_group_name} の共通性分析失敗 → スキップ")
                    continue

                # 再度ヘッダー検出
                col_header_info = self._detect_headers_from_commonality(col_section_data, col_commonality)

                # 列分割後に行分割を試みる（1回のみ）
                nested_row_split_groups = self._detect_repeating_headers(col_section_data, [])

                if nested_row_split_groups:
                    # 列グループ内でさらに行分割を実行
                    logger.info(f"[G-12]     └─ 列グループ内で行分割検出: {len(nested_row_split_groups)}個")

                    for nested_row_idx, nested_row_group in enumerate(nested_row_split_groups, 1):
                        nested_row_name = nested_row_group['group_name']

                        # 行範囲を特定
                        nested_row_start = nested_row_group['row_index']
                        if nested_row_idx < len(nested_row_split_groups):
                            nested_row_end = nested_row_split_groups[nested_row_idx]['row_index'] - 1
                        else:
                            nested_row_end = len(col_section_data) - 1

                        nested_section_data = col_section_data[nested_row_start:nested_row_end + 1]
                        if not nested_section_data:
                            continue

                        # 再度共通性分析・ヘッダー検出
                        nested_commonality, tokens3 = self._analyze_data_commonality(
                            nested_section_data,
                            f"{table.get('table_id', '')}_{col_group_name}_{nested_row_name}"
                        )
                        tokens += tokens3

                        if not nested_commonality.get('success'):
                            continue

                        nested_header_info = self._detect_headers_from_commonality(nested_section_data, nested_commonality)

                        # 物理構造はそのまま保持（禁止事項：組み替えない）
                        processed_sections.append({
                            'title': f"{table.get('table_id', 'テーブル')} - {col_group_name} - {nested_row_name}",
                            'table_type': nested_header_info.get('table_type', 'structured'),
                            'description': nested_commonality.get('description', ''),
                            'data': nested_section_data,  # 2次元配列をそのまま保存
                            'metadata': {
                                'header_rows': nested_header_info['header_rows'],
                                'row_label_col': nested_header_info['row_label_col'],
                                'data_start_row': nested_header_info['data_start_row'],
                                'col_map': nested_header_info['col_map'],
                                'header_meanings': nested_header_info.get('header_meanings', {}),
                                'row_range': [nested_row_start, nested_row_end],
                                'col_range': [col_group['col_start'], col_group['col_end']],
                                'implicit_headers': nested_header_info.get('implicit_headers', []),
                                'split_from': f"{col_group_name}_{nested_row_name}",
                                'primary_header_candidate': nested_row_name  # 繰り返しパターンから検出された最有力ヘッダー候補
                            }
                        })
                        total_records += len(nested_section_data)

                else:
                    # 行分割なしの場合、通常処理（物理構造はそのまま）
                    processed_sections.append({
                        'title': f"{table.get('table_id', 'テーブル')} - {col_group_name}",
                        'table_type': col_header_info.get('table_type', 'structured'),
                        'description': col_commonality.get('description', ''),
                        'data': col_section_data,  # 2次元配列をそのまま保存
                        'metadata': {
                            'header_rows': col_header_info['header_rows'],
                            'row_label_col': col_header_info['row_label_col'],
                            'data_start_row': col_header_info['data_start_row'],
                            'col_map': col_header_info['col_map'],
                            'header_meanings': col_header_info.get('header_meanings', {}),
                            'row_range': [0, len(col_section_data) - 1],
                            'col_range': [col_group['col_start'], col_group['col_end']],
                            'implicit_headers': col_header_info.get('implicit_headers', []),
                            'split_from': col_group_name,
                            'primary_header_candidate': col_group_name  # 繰り返しパターンから検出された最有力ヘッダー候補
                        }
                    })
                    total_records += len(col_section_data)

        elif row_split_groups:
            # 行分割が必要な場合、各グループを個別のサブセクションとして処理
            logger.info(f"[G-12] ✂️ 行分割実行: {len(row_split_groups)}個のサブセクションに分割")

            for row_group_idx, group_header in enumerate(row_split_groups, 1):
                row_group_name = group_header['group_name']
                logger.info(f"\n[G-12]   サブセクション {row_group_idx}/{len(row_split_groups)}: {row_group_name}")

                # 行範囲を特定
                row_start = group_header['row_index']
                if row_group_idx < len(row_split_groups):
                    row_end = row_split_groups[row_group_idx]['row_index'] - 1
                else:
                    row_end = len(full) - 1

                row_section_data = full[row_start:row_end + 1]
                if not row_section_data:
                    continue

                # 再度共通性分析
                row_commonality, tokens2 = self._analyze_data_commonality(
                    row_section_data,
                    f"{table.get('table_id', '')}_{row_group_name}"
                )
                tokens += tokens2

                if not row_commonality.get('success'):
                    logger.warning(f"[G-12]   行グループ {row_group_name} の共通性分析失敗 → スキップ")
                    continue

                # 再度ヘッダー検出
                row_header_info = self._detect_headers_from_commonality(row_section_data, row_commonality)

                # 行分割後に列分割を試みる（1回のみ）
                nested_col_split_groups = self._detect_column_split_by_header_repetition(
                    row_section_data,
                    []
                )

                if nested_col_split_groups:
                    # 行グループ内でさらに列分割を実行
                    logger.info(f"[G-12]     └─ 行グループ内で列分割検出: {len(nested_col_split_groups)}個")

                    for nested_col_idx, nested_col_group in enumerate(nested_col_split_groups, 1):
                        nested_col_name = nested_col_group['group_name']

                        # 列範囲でデータを切り出し
                        nested_section_data = self._extract_column_range(
                            row_section_data,
                            nested_col_group['col_start'],
                            nested_col_group['col_end']
                        )

                        if not nested_section_data:
                            continue

                        # 再度共通性分析・ヘッダー検出
                        nested_commonality, tokens3 = self._analyze_data_commonality(
                            nested_section_data,
                            f"{table.get('table_id', '')}_{row_group_name}_{nested_col_name}"
                        )
                        tokens += tokens3

                        if not nested_commonality.get('success'):
                            continue

                        nested_header_info = self._detect_headers_from_commonality(nested_section_data, nested_commonality)

                        # 物理構造はそのまま保持（禁止事項：組み替えない）
                        processed_sections.append({
                            'title': f"{table.get('table_id', 'テーブル')} - {row_group_name} - {nested_col_name}",
                            'table_type': nested_header_info.get('table_type', 'structured'),
                            'description': nested_commonality.get('description', ''),
                            'data': nested_section_data,  # 2次元配列をそのまま保存
                            'metadata': {
                                'header_rows': nested_header_info['header_rows'],
                                'row_label_col': nested_header_info['row_label_col'],
                                'data_start_row': nested_header_info['data_start_row'],
                                'col_map': nested_header_info['col_map'],
                                'header_meanings': nested_header_info.get('header_meanings', {}),
                                'row_range': [row_start, row_end],
                                'col_range': [nested_col_group['col_start'], nested_col_group['col_end']],
                                'implicit_headers': nested_header_info.get('implicit_headers', []),
                                'split_from': f"{row_group_name}_{nested_col_name}",
                                'primary_header_candidate': nested_col_name  # 繰り返しパターンから検出された最有力ヘッダー候補
                            }
                        })
                        total_records += len(nested_section_data)

                else:
                    # 列分割なしの場合、通常処理（物理構造はそのまま）
                    processed_sections.append({
                        'title': f"{table.get('table_id', 'テーブル')} - {row_group_name}",
                        'table_type': row_header_info.get('table_type', 'structured'),
                        'description': row_commonality.get('description', ''),
                        'data': row_section_data,  # 2次元配列をそのまま保存
                        'metadata': {
                            'header_rows': row_header_info['header_rows'],
                            'row_label_col': row_header_info['row_label_col'],
                            'data_start_row': row_header_info['data_start_row'],
                            'col_map': row_header_info['col_map'],
                            'header_meanings': row_header_info.get('header_meanings', {}),
                            'row_range': [row_start, row_end],
                            'implicit_headers': row_header_info.get('implicit_headers', []),
                            'split_from': row_group_name,
                            'primary_header_candidate': row_group_name  # 繰り返しパターンから検出された最有力ヘッダー候補
                        }
                    })
                    total_records += len(row_section_data)

        else:
            # 分割不要な場合は通常処理（AI分析実行）
            logger.info(f"[G-12] 繰り返しパターンなし → AI分析で通常処理")

            # AI分析: データ共通性
            commonality, tokens2 = self._analyze_data_commonality(full, table.get('table_id', 'テーブル'))
            tokens += tokens2

            if not commonality.get('success'):
                logger.warning(f"[G-12] 共通性分析失敗 → 空の結果を返す")
                return {'table_type': 'empty', 'sections': []}, tokens

            # ヘッダー検出
            header_info = self._detect_headers_from_commonality(full, commonality)

            logger.info(f"[G-12] 処理完了: {len(full)}行")

            processed_sections.append({
                'title': table.get('table_id', 'テーブル'),
                'table_type': header_info.get('table_type', 'structured'),
                'description': commonality.get('description', ''),
                'data': full,  # 2次元配列をそのまま保存
                'metadata': {
                    'header_rows': header_info['header_rows'],
                    'row_label_col': header_info['row_label_col'],
                    'data_start_row': header_info['data_start_row'],
                    'col_map': header_info['col_map'],
                    'header_meanings': header_info.get('header_meanings', {}),
                    'row_range': [0, len(full) - 1],
                    'implicit_headers': header_info.get('implicit_headers', [])
                }
            })
            total_records += len(full)

        if len(processed_sections) == 0:
            return {'table_type': 'empty', 'sections': []}, tokens

        return {
            'table_type': 'multi_section' if len(processed_sections) > 1 else processed_sections[0].get('table_type', 'structured'),
            'description': f"{len(processed_sections)}個のセクションを含む表" if len(processed_sections) > 1 else processed_sections[0].get('description', ''),
            'sections': processed_sections,
            'original_headers': headers,
            'original_rows': rows,
            'metadata': {
                'total_sections': len(processed_sections),
                'total_records': total_records
            }
        }, tokens

    # =========================================================================
    # AI: セクション分割
    # =========================================================================

    def _analyze_table_sections(self, full_table: List[List], table_id: str) -> Tuple[Dict[str, Any], int]:
        """
        表全体を分析して、複数のセクション（時間割、係分担など）を識別する。
        """
        preview = full_table[:min(30, len(full_table))]
        preview_text = ''
        for i, row in enumerate(preview):
            preview_text += f"行{i}: {row}\n"

        # 年度情報を追加
        from datetime import datetime
        if hasattr(self, 'year_context') and self.year_context:
            year_info = f"\n**年度ヒント**: {self.year_context}年の文書です。日付や年度に関連するデータがある場合、参考にしてください。\n"
        else:
            current_year = datetime.now().year
            year_info = f"\n**年度情報**: 年度が不明な場合、{current_year}年を参考にしてください。\n"

        prompt = f"""表（{table_id}）の中に複数の異なる内容（セクション）が含まれているか分析してください。
{year_info}
[表データ（最大30行）]
{preview_text}

## タスク
この表が「1つの内容」なのか、「複数の内容（セクション）」を含むのか判定してください。

## 複数セクションの例
- 時間割 + 係分担表
- 予定表 + 持ち物リスト
- メインデータ + 注釈・補足データ

## 判定基準
- 空行で区切られている
- ヘッダー行が複数回出現する
- 全く異なる構造の表が縦に並んでいる

## 出力（JSONのみ）

### 1つのセクションの場合:
```json
{{
  "success": true,
  "section_count": 1,
  "sections": [
    {{
      "title": "表のタイトル（内容を1-3語で）",
      "start_row": 0,
      "end_row": {len(full_table) - 1}
    }}
  ]
}}
```

### 複数セクションの場合:
```json
{{
  "success": true,
  "section_count": 2,
  "sections": [
    {{
      "title": "時間割",
      "start_row": 0,
      "end_row": 10
    }},
    {{
      "title": "係分担",
      "start_row": 11,
      "end_row": {len(full_table) - 1}
    }}
  ]
}}
```

重要: 各セクションに**明確で具体的なタイトル**をつけてください。"""

        try:
            response = self.model.generate_content(prompt)
            raw = response.text
            tokens = len(prompt + raw) // 4

            logger.info(f"[G-12] セクション分析 AI応答:\n{raw}")

            json_str = self._extract_json(raw)
            parsed = json.loads(json_str)
            parsed['success'] = True
            return parsed, tokens

        except Exception as e:
            logger.warning(f"[G-12] セクション分析失敗: {e}")
            return {'success': False}, 0

    # =========================================================================
    # AI: データの共通性分析（新アルゴリズム）
    # =========================================================================

    def _analyze_data_commonality(self, section_data: List[List], table_id: str) -> Tuple[Dict[str, Any], int]:
        """
        データの共通性を分析して、ヘッダーとデータを区別する。

        【重要な原則】
        1. データ部分：行方向も列方向も必ず共通の種類
        2. ヘッダー部分：データ範囲の共通点から溢れた行・列
        3. 抽象化レベル：身長（数値）と血液型（文字）は「カテゴリー名」として共通
        4. 暗黙的ヘッダー：明示されていなくても、データの共通性から推測

        Returns:
            {
                'success': bool,
                'description': str,  # 表の説明
                'row_analysis': [
                    {
                        'row_index': int,
                        'abstraction_level': 'category_name' | 'concrete_value',
                        'common_type': str  # 「科目」「人名」「カテゴリー名」など
                    }
                ],
                'col_analysis': [
                    {
                        'col_index': int,
                        'abstraction_level': 'category_name' | 'concrete_value',
                        'common_type': str
                    }
                ]
            }
        """
        preview = section_data[:min(10, len(section_data))]
        preview_text = ''
        for i, row in enumerate(preview):
            preview_text += f"行{i}: {row}\n"

        # 年度情報を追加
        from datetime import datetime
        if hasattr(self, 'year_context') and self.year_context:
            year_info = f"\n**年度ヒント**: {self.year_context}年の文書です。日付や年度に関連するデータがある場合、参考にしてください。\n"
        else:
            current_year = datetime.now().year
            year_info = f"\n**年度情報**: 年度が不明な場合、{current_year}年を参考にしてください。\n"

        prompt = f"""表（{table_id}）のデータの共通性を分析してください。
{year_info}
[表データ]
{preview_text}

## タスク
各行と各列について、以下を判定してください：
1. 抽象化レベル：「カテゴリー名」か「具体的な値」か
2. 共通の種類：その行/列が何を表すか

## 重要な原則

### ヘッダーとデータの本質的な違い
- **ヘッダー**：カテゴリー名（抽象的）
  例：身長、血液型、科目、役職名、クラス名、時限
- **データ**：具体的な値
  例：170、A、国語、学級委員長、5A、1限

### 抽象化レベルでの共通性
- 身長（数値）と血液型（文字）は表面的に異なる
- しかし、「カテゴリー名」という抽象化レベルでは共通
- 判定基準：表面的な型（数値/文字）ではなく、意味的な役割

### 暗黙的ヘッダー
- 明示的なヘッダー行がなくても、データの共通性からヘッダーを推測
- 例：1列目に人名、2列目に役職、3列目に人名
  → 本来は行0に「人名、役職名、人名」というヘッダーがあるべき（省略されている）

## 出力（JSONのみ）

```json
{{
  "success": true,
  "description": "この表の説明（1-2文）",
  "row_analysis": [
    {{
      "row_index": 0,
      "abstraction_level": "category_name",
      "common_type": "科目名"
    }},
    {{
      "row_index": 1,
      "abstraction_level": "concrete_value",
      "common_type": "授業内容"
    }}
  ],
  "col_analysis": [
    {{
      "col_index": 0,
      "abstraction_level": "category_name",
      "common_type": "クラス名"
    }},
    {{
      "col_index": 1,
      "abstraction_level": "concrete_value",
      "common_type": "人名"
    }}
  ]
}}
```

注意:
- すべての行とすべての列について分析してください
- 「カテゴリー名」と「具体的な値」の区別を明確にしてください
- 暗黙的ヘッダーがある場合は、その存在を示してください
"""

        try:
            response = self.model.generate_content(prompt)
            raw = response.text
            tokens = len(prompt + raw) // 4

            logger.info(f"[G-12] 共通性分析 AI応答:\n{raw}")

            json_str = self._extract_json(raw)
            parsed = json.loads(json_str)
            parsed['success'] = True
            return parsed, tokens

        except Exception as e:
            logger.warning(f"[G-12] 共通性分析失敗: {e}")
            return {'success': False}, 0

    # =========================================================================
    # ヘッダー検出（新アルゴリズム）
    # =========================================================================

    def _detect_headers_from_commonality(self, section_data: List[List], commonality: Dict[str, Any]) -> Dict[str, Any]:
        """
        データの共通性分析結果から、ヘッダーとデータを分離する。

        【アルゴリズム】
        1. データ範囲を特定（行方向・列方向で「具体的な値」が共通）
        2. 共通性から溢れた行・列 = ヘッダー
        3. 暗黙的ヘッダーを生成（必要に応じて）

        Returns:
            {
                'table_type': str,
                'header_rows': List[int],  # 明示的ヘッダー行
                'header_meanings': Dict[str, str],  # {行インデックス: 意味}
                'row_label_col': Optional[int],  # 行ラベル列
                'data_start_row': int,  # データ開始行
                'col_map': Dict[int, Dict],  # 列座標マップ
                'filled_headers': Dict[str, List],  # 補完済みヘッダー
                'implicit_headers': List[Dict]  # 暗黙的ヘッダー
            }
        """
        row_analysis = commonality.get('row_analysis', [])
        col_analysis = commonality.get('col_analysis', [])

        # Step 1: ヘッダー行を特定（「カテゴリー名」の行）
        header_rows = []
        header_meanings = {}
        data_start_row = 0

        for ra in row_analysis:
            row_idx = ra['row_index']
            if ra['abstraction_level'] == 'category_name':
                header_rows.append(row_idx)
                header_meanings[str(row_idx)] = ra['common_type']
            else:
                # 最初の「具体的な値」行がデータ開始
                if data_start_row == 0 or row_idx < data_start_row:
                    data_start_row = row_idx

        # ヘッダー行がない場合（暗黙的ヘッダー）
        implicit_headers = []
        if not header_rows:
            logger.info("[G-12] 明示的ヘッダー行なし → 暗黙的ヘッダーを生成")
            data_start_row = 0
            # 列分析から暗黙的ヘッダーを生成
            for ca in col_analysis:
                if ca['abstraction_level'] == 'category_name':
                    implicit_headers.append({
                        'col_index': ca['col_index'],
                        'type': ca['common_type']
                    })

        # Step 2: 行ラベル列を特定（「カテゴリー名」の列）
        row_label_col = None
        for ca in col_analysis:
            if ca['abstraction_level'] == 'category_name':
                # 最初のカテゴリー名列を行ラベル列とする
                # （複数ある場合は、データ列に挟まれたものを優先）
                row_label_col = ca['col_index']
                break

        # Step 3: 列ヘッダーを構築
        # ★重要: 最初の列（col 0 = 行ラベル列）は列ヘッダーに含めない
        filled_headers = {}
        for row_idx in header_rows:
            if row_idx < len(section_data):
                raw_row = section_data[row_idx]
                # ★ raw_row[1:] で最初の列をスキップ（行データを列ヘッダーに転置しない）
                if len(raw_row) > 1:
                    filled = self._fill_forward(raw_row[1:])
                    meaning = header_meanings.get(str(row_idx), f'header_{row_idx}')
                    filled_headers[meaning] = filled

        # Step 4: 列座標マップを構築
        col_map = self._build_col_map(filled_headers)

        # 暗黙的ヘッダーがある場合、col_mapに追加
        if implicit_headers:
            for ih in implicit_headers:
                col_idx = ih['col_index']
                if col_idx not in col_map:
                    col_map[col_idx] = {}
                col_map[col_idx]['implicit_type'] = ih['type']

        logger.info(f"[G-12] ヘッダー検出: header_rows={header_rows}, row_label_col={row_label_col}, data_start={data_start_row}")
        if implicit_headers:
            logger.info(f"[G-12] 暗黙的ヘッダー: {implicit_headers}")

        return {
            'table_type': 'structured',
            'header_rows': header_rows,
            'header_meanings': header_meanings,
            'row_label_col': row_label_col,
            'data_start_row': data_start_row,
            'col_map': col_map,
            'filled_headers': filled_headers,
            'implicit_headers': implicit_headers
        }

    # =========================================================================
    # 繰り返しヘッダー検出
    # =========================================================================

    def _detect_column_split_by_header_repetition(
        self,
        section_data: List[List],
        header_rows: List[int]
    ) -> Optional[List[Dict]]:
        """
        列方向のヘッダー繰り返しを検出し、表を列単位で分割する。

        例: 時間割表
          Row 0: ['列1', '5A', null, null, ..., '5B', null, null, ...]
          Row 1: ['',    '朝', '1',  '2',  ..., '朝', '1',  '2',  ...]
        → パターン "朝 1 2 3 4 5 6" が2回繰り返される → 2つの表に分割

        Args:
            section_data: 表の全データ（ヘッダー含む）
            header_rows: ヘッダー行のインデックスリスト

        Returns:
            列分割情報のリスト（分割不要な場合は None）
            [
                {'group_name': '5A', 'col_start': 1, 'col_end': 7},
                {'group_name': '5B', 'col_start': 8, 'col_end': 14}
            ]
        """
        if not section_data or len(section_data) < 2:
            return None

        # Step 1: 最上位ヘッダー行を取得（自動列名の場合はスキップ）
        top_header_row_idx = 0
        top_header_row = section_data[top_header_row_idx]

        # Row 0 が自動列名（Col1, Col2... または 列1, 列2...）の場合は Row 1 を使う
        import re
        if len(top_header_row) >= 2:
            # 最初の非空セル（通常は col 1）をチェック
            first_cell = None
            for cell in top_header_row[1:]:  # col 0 はスキップ
                if cell and str(cell).strip():
                    first_cell = str(cell).strip()
                    break

            # 自動列名パターン: Col\d+ または 列\d+
            if first_cell and re.match(r'^(Col|列)\d+$', first_cell):
                logger.info(f"[G-12] Row 0 は自動列名（{first_cell}）→ Row 1 を top_header として使用")
                top_header_row_idx = 1
                if top_header_row_idx < len(section_data):
                    top_header_row = section_data[top_header_row_idx]
                else:
                    return None

        if len(top_header_row) < 3:
            # 列が少なすぎる場合は分割不要
            return None

        # Step 2: 最上位行でグループ（セクション）を検出
        # null/空でない値 → 次のnull/空でない値までを1グループとする
        groups = []
        current_group_name = None
        current_group_start = None

        for col_idx, cell in enumerate(top_header_row):
            # 0列目（行ラベル列）はスキップ
            if col_idx == 0:
                continue

            if cell and str(cell).strip():
                # 新しいグループの開始
                if current_group_name is not None:
                    # 前のグループを確定
                    groups.append({
                        'group_name': current_group_name,
                        'col_start': current_group_start,
                        'col_end': col_idx - 1
                    })
                current_group_name = str(cell).strip()
                current_group_start = col_idx

        # 最後のグループを確定
        if current_group_name is not None:
            groups.append({
                'group_name': current_group_name,
                'col_start': current_group_start,
                'col_end': len(top_header_row) - 1
            })

        if len(groups) < 2:
            # グループが1つ以下なら分割不要
            return None

        logger.info(f"[G-12] 列グループ検出: {len(groups)}個")
        for g in groups:
            logger.info(f"  ├─ {g['group_name']}: 列{g['col_start']}~{g['col_end']}")

        # Step 3: 各グループの下位ヘッダー行のパターンを比較
        # top_header_row の次の行（1行のみ）を比較対象とする
        next_row_idx = top_header_row_idx + 1
        if next_row_idx >= len(section_data):
            # 次の行がない場合は分割不要
            logger.info(f"[G-12] top_header_row の次の行なし → 分割不要")
            return None

        lower_header_rows = [next_row_idx]  # top_header_row の次の行（1行のみ）

        # 各グループの下位ヘッダーパターンを抽出
        group_patterns = []
        for group in groups:
            pattern_rows = []
            for header_row_idx in lower_header_rows:
                header_row = section_data[header_row_idx]
                # グループの列範囲のセルを抽出
                pattern_cells = []
                for col_idx in range(group['col_start'], group['col_end'] + 1):
                    if col_idx < len(header_row):
                        cell = header_row[col_idx]
                        # null や空文字列を正規化
                        pattern_cells.append(str(cell).strip() if cell else '')
                    else:
                        pattern_cells.append('')
                pattern_rows.append(tuple(pattern_cells))

            # 複数行のパターンをタプルのタプルとして保存
            group_patterns.append(tuple(pattern_rows))

        # Step 4: パターンの重複を検出
        from collections import defaultdict
        pattern_groups = defaultdict(list)

        for idx, pattern in enumerate(group_patterns):
            pattern_groups[pattern].append(groups[idx])

        # パターンが2回以上出現する場合のみ分割
        repeating_patterns = {pattern: grps for pattern, grps in pattern_groups.items() if len(grps) >= 2}

        if not repeating_patterns:
            logger.info(f"[G-12] ヘッダーパターンの繰り返しなし → 分割不要")
            return None

        # 最も多く繰り返されるパターンを使用
        most_common_pattern = max(repeating_patterns.items(), key=lambda x: len(x[1]))
        split_groups = most_common_pattern[1]

        logger.info(f"[G-12] ✂️ ヘッダーパターン繰り返し検出: {len(split_groups)}個のグループに分割")
        for g in split_groups:
            logger.info(f"  ├─ {g['group_name']}: 列{g['col_start']}~{g['col_end']}")

        return split_groups

    def _extract_column_range(
        self,
        section_data: List[List],
        col_start: int,
        col_end: int
    ) -> List[List]:
        """
        セクションデータから指定列範囲を切り出す。

        Args:
            section_data: 元のセクションデータ
            col_start: 開始列インデックス
            col_end: 終了列インデックス

        Returns:
            切り出されたデータ（0列目は常に保持、col_start~col_endを追加）
        """
        extracted = []
        for row in section_data:
            new_row = []

            # 0列目（行ラベル列）を常に保持
            if len(row) > 0:
                new_row.append(row[0])

            # 指定範囲の列を追加
            if col_start < len(row):
                new_row.extend(row[col_start:min(col_end + 1, len(row))])

            extracted.append(new_row)

        return extracted

    def _detect_repeating_headers(self, section_data: List[List], header_row_indices: List[int]) -> List[Dict]:
        """
        データ行中の繰り返しヘッダーパターンを検出する（行単位）。

        例: 係分担表で
          行0: ["5A", "チームリーダー", "学習係", ...]
          行5: ["5B", "チームリーダー", "学習係", ...]
        → 同じパターン（最初のセル以外が一致）をグループ分割ヘッダーと判定
        """
        potential_headers = []

        for i, row in enumerate(section_data):
            if not row or len(row) < 2:
                continue

            first_cell = row[0]
            rest_cells = row[1:]

            if not any(c for c in rest_cells if c and c != ''):
                continue

            pattern_key = tuple(rest_cells)
            potential_headers.append({
                'row_index': i,
                'first_cell': first_cell,
                'pattern': pattern_key,
                'raw_row': row
            })

        from collections import defaultdict
        pattern_groups = defaultdict(list)

        for ph in potential_headers:
            pattern_groups[ph['pattern']].append(ph)

        group_headers = []
        for pattern, occurrences in pattern_groups.items():
            if len(occurrences) >= 2:
                for occ in occurrences:
                    group_name = occ['first_cell']
                    if group_name and group_name != '':
                        group_headers.append({
                            'row_index': occ['row_index'],
                            'group_name': group_name
                        })

        group_headers.sort(key=lambda x: x['row_index'])
        return group_headers

    # =========================================================================
    # データ再構造化
    # =========================================================================

    # =========================================================================
    # ユーティリティ
    # =========================================================================

    def _fill_forward(self, row: List) -> List:
        """None/空値を直前の値で補完（結合セル対応）"""
        result = []
        last = None
        for val in row:
            is_placeholder = (
                val is None or
                val == '' or
                (isinstance(val, str) and re.match(r'^列\d+$', val.strip()))
            )
            if is_placeholder:
                result.append(last)
            else:
                last = val
                result.append(val)
        return result

    def _build_col_map(self, filled_headers: Dict[str, List]) -> Dict[int, Dict]:
        """
        各列の座標マップを構築
        {col_index: {meaning: value, ...}}
        """
        if not filled_headers:
            return {}

        col_count = max((len(v) for v in filled_headers.values()), default=0)
        col_map = {}

        for col_idx in range(col_count):
            coord = {}
            for meaning, row in filled_headers.items():
                if col_idx < len(row) and row[col_idx] is not None:
                    coord[meaning] = row[col_idx]
            col_map[col_idx] = coord

        return col_map

    def _extract_json(self, text: str) -> str:
        """JSONブロックを抽出"""
        if '```json' in text:
            start = text.find('```json') + 7
            end = text.find('```', start)
            return text[start:end].strip()
        if '```' in text:
            start = text.find('```') + 3
            end = text.find('```', start)
            return text[start:end].strip()
        return text.strip()

    def _error_result(self, msg: str) -> Dict[str, Any]:
        """エラー結果を返す"""
        return {'success': False, 'error': msg, 'table_analyses': [], 'tokens_used': 0}
