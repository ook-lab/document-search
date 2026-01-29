"""
Stage G1: Table Refiner（表専用整理）- AI研磨対応版

【設計 2026-01-28】表データの統合・検算・整形 + AI研磨

役割: G-Gate から受け取った表データを検証・整形し、H1 用 JSON を出力
      必要に応じて AI（Flash-Lite）で「研磨」を行い、物理的な不整合を解消

============================================
入力（G-Gate から）:
  - tables: 表データリスト
  - table_page_context: 表ページのテキストコンテキスト

出力（H1 へ）:
  - tables: 検証済み表データ（anchor_id, page, headers, rows）
  - validation_results: 各表の検算結果
  - table_page_context: 表周辺のテキスト
  - token_usage: トークン使用量

処理フロー:
  1. ルールベース検算
  2. 自動修正（セル数調整等）
  3. AI研磨（必要な表のみ）← 投資ポイント
  4. 最終検証・整形
============================================
"""
import json
from typing import Dict, Any, List, Tuple, Optional
from dataclasses import dataclass, field
from loguru import logger
import re

# G1 で使用するモデル
G1_MODEL = "gemini-2.5-flash-lite"


@dataclass
class TableValidationResult:
    """表の検算結果"""
    anchor_id: str
    is_valid: bool
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    auto_fixed: bool = False
    ai_polished: bool = False
    fix_description: str = ""


class StageG1TableRefiner:
    """G1: 表データの統合・検算・整形（AI研磨対応）"""

    # 検算の閾値
    MAX_EMPTY_CELL_RATIO = 0.5  # 空セル率がこれ以上なら警告
    MAX_DUPLICATE_ROW_RATIO = 0.3  # 重複行率がこれ以上なら警告

    # AI研磨の投入条件
    POLISH_THRESHOLD_CELL_MISMATCH = 2  # セル数不一致がこれ以上なら研磨
    POLISH_THRESHOLD_EMPTY_RATIO = 0.3  # 空セル率がこれ以上なら研磨

    def __init__(self, llm_client=None):
        """
        Args:
            llm_client: LLMクライアント（AI研磨に使用）
        """
        self.llm = llm_client
        self._token_usage: Dict[str, Any] = {
            'prompt_tokens': 0,
            'completion_tokens': 0,
            'total_tokens': 0,
            'model': G1_MODEL,
            'polished_tables': 0
        }

    def process(self, g1_input: Dict[str, Any]) -> Dict[str, Any]:
        """
        表データを検証・整形（AI研磨対応）

        Args:
            g1_input: G-Gate からの入力
                - tables: 表データリスト
                - table_page_context: 表ページのテキストコンテキスト

        Returns:
            H1 用の整形済み JSON
        """
        logger.info("[G1] 表整理開始（AI研磨対応）...")

        tables = g1_input.get('tables', [])
        table_page_context = g1_input.get('table_page_context', {})

        if not tables:
            logger.info("[G1] 表なし → スキップ")
            logger.info(f"[G1] トークン使用量: 0 (表なし)")
            return {
                'tables': [],
                'validation_results': [],
                'table_page_context': {},
                'statistics': {
                    'total_tables': 0,
                    'valid_tables': 0,
                    'fixed_tables': 0,
                    'polished_tables': 0,
                    'total_rows': 0
                },
                'token_usage': self._token_usage.copy()
            }

        # 各表を検証・整形
        validated_tables = []
        validation_results = []
        total_rows = 0
        fixed_count = 0
        polished_count = 0

        for table in tables:
            anchor_id = table.get('anchor_id', '')
            logger.debug(f"[G1] 検証: {anchor_id}")

            # Step 1: ルールベース検算
            validation = self._validate_table(table)

            # Step 2: 自動修正（セル数調整等）
            if validation.errors and not validation.is_valid:
                fixed_table, fix_result = self._auto_fix_table(table, validation)
                if fix_result.is_valid:
                    table = fixed_table
                    validation = fix_result
                    fixed_count += 1

            # Step 3: AI研磨（全件実行 - 常駐化）
            # 2026-01-28: 条件判定を撤廃し、全ての表をAIに通す
            # 理由: 1文字のズレ、金額の泣き別れ（￥と1,000が分かれる等）を
            #       H1に渡る前に100%仕留める
            if self.llm:  # LLMクライアントがあれば必ず実行
                logger.info(f"[G1] AI研磨実行（常駐）: {anchor_id}")
                polished_table, polish_result = self._polish_table_with_ai(
                    table,
                    validation,
                    table_page_context.get(f"page_{table.get('page', 0)}", "")
                )
                if polish_result.is_valid:
                    table = polished_table
                    validation = polish_result
                    polished_count += 1
                    self._token_usage['polished_tables'] += 1
                else:
                    # 研磨後も無効な場合、元のvalidationにai_polished=Trueを記録
                    validation.ai_polished = True
                    logger.warning(f"[G1] AI研磨後も検証失敗: {anchor_id}")

            validation_results.append(validation)

            # Step 4: 最終整形
            formatted_table = self._format_table_for_h1(table, validation)
            validated_tables.append(formatted_table)
            total_rows += len(formatted_table.get('rows', []))

            # 警告・エラーをログ
            for warn in validation.warnings:
                logger.warning(f"[G1] {anchor_id}: {warn}")
            for err in validation.errors:
                logger.error(f"[G1] {anchor_id}: {err}")

        # 統計
        valid_count = sum(1 for v in validation_results if v.is_valid)
        statistics = {
            'total_tables': len(tables),
            'valid_tables': valid_count,
            'fixed_tables': fixed_count,
            'polished_tables': polished_count,
            'total_rows': total_rows,
            'heavy_tables': sum(1 for t in validated_tables if t.get('is_heavy', False))
        }

        # トークン使用量をログ出力
        logger.info(f"[G1] トークン使用量: prompt={self._token_usage['prompt_tokens']}, "
                   f"completion={self._token_usage['completion_tokens']}, "
                   f"total={self._token_usage['total_tokens']} (model={self._token_usage['model']})")
        logger.info(f"[G1] AI研磨: {polished_count}表に適用")

        logger.info(f"[G1] 完了: {valid_count}/{len(tables)}表が有効, "
                   f"{fixed_count}表を自動修正, {polished_count}表をAI研磨, 計{total_rows}行")

        return {
            'tables': validated_tables,
            'validation_results': [self._validation_to_dict(v) for v in validation_results],
            'table_page_context': table_page_context,
            'statistics': statistics,
            'token_usage': self._token_usage.copy()
        }

    # ============================================
    # AI研磨機能
    # ============================================
    def _needs_ai_polish(self, table: Dict[str, Any], validation: TableValidationResult) -> bool:
        """
        AI研磨が必要かどうかを判定

        投入条件:
        1. セル数不一致が閾値以上
        2. 空セル率が閾値以上
        3. E から来た生テキストがある（構造化が不完全な可能性）
        4. 警告が複数ある
        """
        # LLMクライアントがなければ研磨不可
        if not self.llm:
            return False

        headers = table.get('headers', [])
        rows = table.get('rows', [])

        # 表が小さすぎる場合はスキップ（コスト対効果が低い）
        if len(rows) < 2 or len(headers) < 2:
            return False

        # 条件1: セル数不一致
        mismatch_count = 0
        for row in rows:
            if isinstance(row, list) and len(row) != len(headers):
                mismatch_count += 1
        if mismatch_count >= self.POLISH_THRESHOLD_CELL_MISMATCH:
            logger.debug(f"[G1] 研磨理由: セル数不一致 {mismatch_count}行")
            return True

        # 条件2: 空セル率
        if rows and headers:
            total_cells = len(rows) * len(headers)
            empty_cells = sum(
                1 for row in rows if isinstance(row, list)
                for cell in row if not str(cell).strip()
            )
            if total_cells > 0:
                empty_ratio = empty_cells / total_cells
                if empty_ratio >= self.POLISH_THRESHOLD_EMPTY_RATIO:
                    logger.debug(f"[G1] 研磨理由: 空セル率 {empty_ratio:.1%}")
                    return True

        # 条件3: E の生テキストがある
        if table.get('e_raw_text'):
            logger.debug(f"[G1] 研磨理由: E生テキストあり")
            return True

        # 条件4: 警告が多い
        if len(validation.warnings) >= 3:
            logger.debug(f"[G1] 研磨理由: 警告{len(validation.warnings)}件")
            return True

        return False

    def _polish_table_with_ai(
        self,
        table: Dict[str, Any],
        validation: TableValidationResult,
        context: str = ""
    ) -> Tuple[Dict[str, Any], TableValidationResult]:
        """
        AIで表を研磨

        Args:
            table: 研磨対象の表
            validation: 現在の検算結果
            context: 表周辺のテキスト（文脈判断用）

        Returns:
            (研磨後の表, 新しい検算結果)
        """
        anchor_id = table.get('anchor_id', 'unknown')

        try:
            # プロンプト構築
            prompt = self._build_polish_prompt(table, validation, context)

            # AI呼び出し
            logger.info(f"[G1] AI研磨実行中: {anchor_id}")
            response = self.llm.call_model(
                tier="default",
                prompt=prompt,
                model_name=G1_MODEL,
                temperature=0.0,
                response_format='json'
            )

            # トークン使用量を記録
            if hasattr(self.llm, 'last_usage') and self.llm.last_usage:
                usage = self.llm.last_usage
                self._token_usage['prompt_tokens'] += usage.get('prompt_tokens', 0)
                self._token_usage['completion_tokens'] += usage.get('completion_tokens', 0)
                self._token_usage['total_tokens'] += usage.get('total_tokens', 0)
                logger.info(f"[G1] AI研磨トークン: prompt={usage.get('prompt_tokens', 0)}, "
                           f"completion={usage.get('completion_tokens', 0)}")

            # レスポンス処理
            if not response.get('success'):
                logger.warning(f"[G1] AI研磨失敗: {response.get('error')}")
                return table, validation

            content = response.get('content', '')
            polished_data = self._parse_polish_response(content)

            if polished_data:
                # 修復不能フラグの処理（敗戦処理）
                if polished_data.get('is_unrepairable'):
                    reason = polished_data.get('reason', '理由不明')
                    logger.warning(f"[G1] 修復不能（白旗）: {anchor_id} - {reason}")

                    # 元のテーブルに修復不能フラグを付与
                    unrepairable_table = table.copy()
                    unrepairable_table['status'] = 'unrepairable'
                    unrepairable_table['unrepairable_reason'] = reason

                    # バリデーション結果も更新
                    new_validation = TableValidationResult(
                        anchor_id=anchor_id,
                        is_valid=False,
                        warnings=validation.warnings,
                        errors=validation.errors + [f"AI判定: 修復不能 - {reason}"],
                        auto_fixed=False,
                        ai_polished=True,
                        fix_description=f"UNREPAIRABLE: {reason}"
                    )
                    return unrepairable_table, new_validation

                # 修復成功時の処理
                polished_table = table.copy()
                polished_table['headers'] = polished_data.get('headers', table.get('headers', []))
                polished_table['rows'] = polished_data.get('rows', table.get('rows', []))
                polished_table['row_count'] = len(polished_table['rows'])
                polished_table['col_count'] = len(polished_table['headers'])
                polished_table['status'] = 'polished'  # 正常修復

                # 再検証
                new_validation = self._validate_table(polished_table)
                new_validation.ai_polished = True
                new_validation.fix_description = f"AI polished: {polished_data.get('changes_made', 'unknown')}"

                logger.info(f"[G1] AI研磨成功: {anchor_id} - {polished_data.get('changes_made', '')}")
                return polished_table, new_validation

        except Exception as e:
            logger.error(f"[G1] AI研磨エラー: {anchor_id} - {e}")

        return table, validation

    def _build_polish_prompt(
        self,
        table: Dict[str, Any],
        validation: TableValidationResult,
        context: str
    ) -> str:
        """
        AI研磨用プロンプトを構築

        最小限のデータのみをAIに渡し、浪費を防ぐ
        """
        headers = table.get('headers', [])
        rows = table.get('rows', [])
        e_raw_text = table.get('e_raw_text', '')

        # 問題点のサマリー
        issues = []
        for warn in validation.warnings[:5]:  # 最大5件
            issues.append(f"- {warn}")
        for err in validation.errors[:3]:  # 最大3件
            issues.append(f"- ERROR: {err}")

        prompt = f"""あなたは精密な表修復の職人です。
以下の【視覚枠組み（headers/rows）】と【生テキスト（OCR結果）】を照合し、正しい表に修復してください。

【絶対ルール】
1. 座標が微妙にズレている文字を、文脈判断で正しいセル(Row/Col)に割り振れ
2. OCRで分割された一つの単語（例: "￥" と "1,000"）は結合せよ
3. 空セルは空文字列 "" のままでよい（無理に埋めない）
4. 行の追加・削除は禁止（行数は維持）
5. 列の追加・削除は禁止（列数は維持）

【現在の枠組み】
- ヘッダー: {json.dumps(headers, ensure_ascii=False)}
- 行数: {len(rows)}行
- 列数: {len(headers)}列

【現在のデータ（修正前）】
```json
{json.dumps(rows[:20], ensure_ascii=False, indent=2)}
```
{f"（※全{len(rows)}行中、最初の20行のみ表示）" if len(rows) > 20 else ""}

【検出された問題】
{chr(10).join(issues) if issues else "軽微なズレの可能性"}

{f'''【OCR生テキスト（参考）】
```
{e_raw_text[:1500]}
```
''' if e_raw_text else ''}

{f'''【周辺コンテキスト】
{context[:500]}
''' if context else ''}

【出力形式】
以下のJSON形式のみを出力せよ。解説は一切不要。

■ 修復可能な場合：
```json
{{
  "headers": ["列1", "列2", ...],
  "rows": [
    ["値1", "値2", ...],
    ...
  ],
  "changes_made": "修正内容の簡潔な説明"
}}
```

■ 修復不能な場合（行列の整合が物理的に不可能）：
```json
{{
  "is_unrepairable": true,
  "reason": "修復不能の理由（例: 列数が行ごとにバラバラで統一不可能）"
}}
```
修復を諦める勇気も職人の技。無理に嘘のデータを作るな。
"""
        return prompt

    def _parse_polish_response(self, content: str) -> Optional[Dict[str, Any]]:
        """
        AI研磨レスポンスをパース

        Returns:
            - 修復成功時: {'headers': [...], 'rows': [...], 'changes_made': ...}
            - 修復不能時: {'is_unrepairable': True, 'reason': ...}
            - パース失敗時: None
        """
        try:
            # JSONブロックを抽出
            json_match = re.search(r'```json\s*(.*?)\s*```', content, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
            else:
                # 直接JSONの場合
                json_str = content.strip()

            data = json.loads(json_str)

            # 修復不能フラグの確認（敗戦処理）
            if data.get('is_unrepairable'):
                logger.warning(f"[G1] AI判定: 修復不能 - {data.get('reason', '理由不明')}")
                return data  # そのまま返す

            # 修復成功時の必須フィールド確認
            if 'headers' in data and 'rows' in data:
                return data

            logger.warning(f"[G1] AI研磨レスポンスに必須フィールドなし")
            return None

        except json.JSONDecodeError as e:
            logger.warning(f"[G1] AI研磨レスポンスのJSONパース失敗: {e}")
            try:
                import json_repair
                return json_repair.loads(content)
            except:
                return None

    # ============================================
    # 検算・修正機能（既存）
    # ============================================
    def _validate_table(self, table: Dict[str, Any]) -> TableValidationResult:
        """
        表の検算を実行

        チェック項目:
        1. headers と rows のセル数一致
        2. 空行の検出
        3. 空列の検出
        4. 重複行の検出
        5. 行数・列数の整合性
        """
        anchor_id = table.get('anchor_id', 'unknown')
        headers = table.get('headers', [])
        rows = table.get('rows', [])
        declared_row_count = table.get('row_count', len(rows))
        declared_col_count = table.get('col_count', len(headers))

        warnings = []
        errors = []

        # 1. ヘッダーと行のセル数一致チェック
        if headers:
            header_count = len(headers)
            mismatch_rows = []
            for i, row in enumerate(rows):
                if isinstance(row, list) and len(row) != header_count:
                    mismatch_rows.append(i)
            if mismatch_rows:
                if len(mismatch_rows) <= 3:
                    warnings.append(f"Cell count mismatch in rows: {mismatch_rows}")
                else:
                    warnings.append(f"Cell count mismatch in {len(mismatch_rows)} rows")

        # 2. 空行の検出
        empty_rows = []
        for i, row in enumerate(rows):
            if isinstance(row, list) and all(not str(cell).strip() for cell in row):
                empty_rows.append(i)
        if empty_rows:
            warnings.append(f"Empty rows: {empty_rows[:5]}{'...' if len(empty_rows) > 5 else ''}")

        # 3. 空列の検出（全ての行でその列が空）
        if headers and rows:
            empty_cols = []
            for col_idx in range(len(headers)):
                all_empty = True
                for row in rows:
                    if isinstance(row, list) and col_idx < len(row):
                        if str(row[col_idx]).strip():
                            all_empty = False
                            break
                if all_empty:
                    empty_cols.append(headers[col_idx] if col_idx < len(headers) else col_idx)
            if empty_cols:
                warnings.append(f"Empty columns: {empty_cols}")

        # 4. 重複行の検出
        if rows:
            row_strings = [str(row) for row in rows if isinstance(row, list)]
            seen = {}
            duplicates = []
            for i, rs in enumerate(row_strings):
                if rs in seen:
                    duplicates.append((seen[rs], i))
                else:
                    seen[rs] = i
            if duplicates:
                dup_ratio = len(duplicates) / len(rows)
                if dup_ratio > self.MAX_DUPLICATE_ROW_RATIO:
                    warnings.append(f"High duplicate row ratio: {dup_ratio:.1%}")

        # 5. 行数・列数の整合性
        actual_row_count = len(rows)
        actual_col_count = len(headers) if headers else (max(len(r) for r in rows if isinstance(r, list)) if rows else 0)

        if declared_row_count != actual_row_count:
            errors.append(f"Row count: declared={declared_row_count}, actual={actual_row_count}")

        if declared_col_count != actual_col_count and declared_col_count > 0:
            errors.append(f"Column count: declared={declared_col_count}, actual={actual_col_count}")

        # 6. 空セル率チェック
        if rows and headers:
            total_cells = len(rows) * len(headers)
            empty_cells = 0
            for row in rows:
                if isinstance(row, list):
                    empty_cells += sum(1 for cell in row if not str(cell).strip())
            if total_cells > 0:
                empty_ratio = empty_cells / total_cells
                if empty_ratio > self.MAX_EMPTY_CELL_RATIO:
                    warnings.append(f"High empty cell ratio: {empty_ratio:.1%}")

        # 判定
        is_valid = len(errors) == 0

        return TableValidationResult(
            anchor_id=anchor_id,
            is_valid=is_valid,
            warnings=warnings,
            errors=errors
        )

    def _auto_fix_table(
        self,
        table: Dict[str, Any],
        validation: TableValidationResult
    ) -> Tuple[Dict[str, Any], TableValidationResult]:
        """
        表の自動修正を試みる（ルールベース）

        修正内容:
        1. 行のセル数を揃える（不足は空文字で埋める、過剰は切り詰める）
        2. row_count / col_count を実際の値に更新
        """
        fixed_table = table.copy()
        fix_descriptions = []

        headers = fixed_table.get('headers', [])
        rows = fixed_table.get('rows', [])
        target_col_count = len(headers) if headers else 0

        # 1. 行のセル数を揃える
        if target_col_count > 0:
            fixed_rows = []
            for row in rows:
                if isinstance(row, list):
                    if len(row) < target_col_count:
                        row = row + [''] * (target_col_count - len(row))
                    elif len(row) > target_col_count:
                        row = row[:target_col_count]
                    fixed_rows.append(row)
                elif isinstance(row, dict):
                    fixed_rows.append([str(row.get(h, '')) for h in headers])

            fixed_table['rows'] = fixed_rows
            if fixed_rows != rows:
                fix_descriptions.append("Normalized cell counts")

        # 2. row_count / col_count を更新
        fixed_table['row_count'] = len(fixed_table.get('rows', []))
        fixed_table['col_count'] = len(fixed_table.get('headers', []))

        # 再検証
        new_validation = self._validate_table(fixed_table)
        new_validation.auto_fixed = True
        new_validation.fix_description = '; '.join(fix_descriptions) if fix_descriptions else 'No changes'

        return fixed_table, new_validation

    def _format_table_for_h1(
        self,
        table: Dict[str, Any],
        validation: TableValidationResult
    ) -> Dict[str, Any]:
        """H1 用に表を整形"""
        rows = table.get('rows', [])
        headers = table.get('headers', [])

        # is_heavy の再判定（20行以上 or 5列以上）
        is_heavy = len(rows) >= 20 or len(headers) >= 5 or table.get('is_heavy', False)

        return {
            'anchor_id': table.get('anchor_id', ''),
            'page': table.get('page', 0),
            'title': table.get('title', ''),
            'table_type': table.get('table_type', 'visual_table'),
            'headers': headers,
            'rows': rows,
            'row_count': len(rows),
            'col_count': len(headers),
            'source': table.get('source', 'unknown'),
            'is_heavy': is_heavy,
            'is_valid': validation.is_valid,
            'ai_polished': validation.ai_polished,
            'validation_warnings': validation.warnings
        }

    def _validation_to_dict(self, validation: TableValidationResult) -> Dict[str, Any]:
        """検算結果を辞書に変換"""
        return {
            'anchor_id': validation.anchor_id,
            'is_valid': validation.is_valid,
            'warnings': validation.warnings,
            'errors': validation.errors,
            'auto_fixed': validation.auto_fixed,
            'ai_polished': validation.ai_polished,
            'fix_description': validation.fix_description
        }

    # ============================================
    # ヘルパー関数
    # ============================================
    def structure_from_e_text(
        self,
        raw_text: str,
        context: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """E の生テキストから表構造を推測"""
        if not raw_text:
            return None

        lines = [l for l in raw_text.split('\n') if l.strip()]
        if len(lines) < 2:
            return None

        delimiter = self._detect_delimiter(raw_text)
        headers = [c.strip() for c in lines[0].split(delimiter) if c.strip()]
        rows = []

        for line in lines[1:]:
            cells = [c.strip() for c in line.split(delimiter)]
            if any(c for c in cells):
                rows.append(cells)

        if not headers or not rows:
            return None

        title = self._guess_table_title(raw_text, context)
        table_type = self._guess_table_type(headers, rows)

        return {
            'headers': headers,
            'rows': rows,
            'title': title,
            'table_type': table_type,
            'row_count': len(rows),
            'col_count': len(headers)
        }

    def _detect_delimiter(self, text: str) -> str:
        """区切り文字を検出"""
        tab_count = text.count('\t')
        pipe_count = text.count('|')
        comma_count = text.count(',')

        if tab_count > pipe_count and tab_count > comma_count:
            return '\t'
        elif pipe_count > comma_count:
            return '|'
        else:
            return '\t'

    def _guess_table_title(self, raw_text: str, context: Optional[str]) -> str:
        """表のタイトルを推測"""
        if context:
            patterns = [
                r'【(.+?)】',
                r'■\s*(.+)',
                r'◆\s*(.+)',
                r'^(.+?[表一覧リスト])\s*$',
            ]
            for pattern in patterns:
                match = re.search(pattern, context, re.MULTILINE)
                if match:
                    return match.group(1).strip()
        return ""

    def _guess_table_type(self, headers: List[str], rows: List[List[str]]) -> str:
        """表のタイプを推測"""
        header_text = ' '.join(headers).lower()
        all_text = header_text + ' ' + ' '.join(str(cell) for row in rows for cell in row).lower()

        if any(kw in header_text for kw in ['順位', '位', 'rank', '№']):
            return 'ranking'
        if any(kw in header_text for kw in ['日付', '日時', '時間', '曜日']):
            return 'schedule'
        if any(kw in all_text for kw in ['円', '¥', '料金', '価格', '金額']):
            return 'pricing'
        if any(kw in header_text for kw in ['氏名', '名前', '生徒', '参加者']):
            return 'roster'
        if any(kw in header_text for kw in ['点数', '得点', '成績', 'スコア']):
            return 'score'

        return 'visual_table'
