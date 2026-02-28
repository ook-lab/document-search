"""
G-17: Table AI Processor（表のAI処理）

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


class G17TableAIProcessor:
    """G-17: Table AI Processor（AI分析・後段処理専用）"""

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
                logger.info(f"[G-17] モデル初期化: {model_name}")
            else:
                logger.warning("[G-17] API key が設定されていません")
        except ImportError:
            logger.warning("[G-17] google-generativeai がインストールされていません")

    # =========================================================================
    # エントリーポイント
    # =========================================================================

    def process(self, g14_reconstructed: List[Dict[str, Any]], year_context: Optional[int] = None, log_file=None) -> Dict[str, Any]:
        """
        G-14の再構成済み表データをAI分析する。

        Args:
            g14_reconstructed: G-14出力
                [{'table_id': str, 'sub_tables': [{'data': list, 'group_name': str, 'split_axis': str}]}, ...]
            year_context: 年度コンテキスト（日付推定に使用）
            log_file: ログファイルパス（オプション）

        Returns:
            {
                'success': bool,
                'table_analyses': list,
                'tokens_used': int
            }
        """
        _sink_id = None
        if log_file:
            _sink_id = logger.add(
                str(log_file),
                format="{time:HH:mm:ss} | {level:<5} | {message}",
                filter=lambda r: "[G-17]" in r["message"],
                level="DEBUG",
                encoding="utf-8",
            )
        try:
            return self._process_impl(g14_reconstructed, year_context)
        finally:
            if _sink_id is not None:
                logger.remove(_sink_id)

    def _process_impl(self, g14_reconstructed: List[Dict[str, Any]], year_context: Optional[int] = None) -> Dict[str, Any]:
        """process() の実装本体"""
        logger.info("[G-17] ========== AI処理開始 ==========")
        logger.info(f"[G-17] モデル: {self.model_name}")
        logger.info(f"[G-17] 年度コンテキスト: {year_context if year_context else 'なし（AIが推定）'}")

        # ★年度情報を保存（プロンプト構築で使用）
        self.year_context = year_context
        logger.info(f"[G-17] 入力表数: {len(g14_reconstructed)}個")

        if not self.model:
            return self._error_result("Gemini API not available")

        if not g14_reconstructed:
            return {
                'success': True,
                'table_analyses': [],
                'tokens_used': 0
            }

        table_analyses = []
        total_tokens = 0

        for i, entry in enumerate(g14_reconstructed, 1):
            table_id = entry.get('table_id', f'Table_{i}')
            sub_tables = entry.get('sub_tables', [])
            logger.info(f"\n[G-17] 表 {i}/{len(g14_reconstructed)}: {table_id} ({len(sub_tables)}サブテーブル)")

            result, tokens = self._process_sub_tables(table_id, sub_tables)
            total_tokens += tokens

            # 複数セクションを個別の表として展開
            if result.get('table_type') == 'multi_section':
                sections = result.get('sections', [])
                logger.info(f"[G-17] ✂️ 表 {table_id} を {len(sections)}個のセクションに分割")

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
                result['table_id'] = table_id
                table_analyses.append(result)

        logger.info(f"\n[G-17] 完了: {len(table_analyses)}表, {total_tokens}トークン")

        result = {
            'success': True,
            'table_analyses': table_analyses,
            'tokens_used': total_tokens
        }

        # Supabaseに保存
        if self.document_id:
            try:
                db = DatabaseClient(use_service_role=True)
                db.client.table('Rawdata_FILE_AND_MAIL').update({
                    'g17_table_analyses': table_analyses,
                    'g14_reconstructed_tables': g14_reconstructed
                }).eq('id', self.document_id).execute()
                logger.info(f"[G-17] ✓ g17_table_analyses を Supabase に保存: {len(table_analyses)}表")
                logger.info(f"[G-17] ✓ g14_reconstructed_tables を Supabase に保存: {len(g14_reconstructed)}表")
            except Exception as e:
                logger.error(f"[G-17] Supabase保存エラー: {e}")

        return result

    # =========================================================================
    # サブテーブルのAI処理（G-14の再構成結果を受け取る）
    # =========================================================================

    def _process_sub_tables(self, table_id: str, sub_tables: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], int]:
        """G-14の再構成済みサブテーブルをAI分析する"""
        if not sub_tables:
            return {'table_type': 'empty', 'sections': []}, 0

        if len(sub_tables) > 1:
            logger.info(f"[G-17] ✂️ 分割あり: {len(sub_tables)}個のサブセクション")

        tokens = 0
        processed_sections = []
        total_records = 0

        for sub_idx, sub in enumerate(sub_tables):
            sub_data = sub.get("data", [])
            group_name = sub.get("group_name", "")
            split_axis = sub.get("split_axis", "none")

            # Step 0.5: 自動列名のみの行を除外（Row0汚染対策）
            def _is_auto_column_name_row(row):
                non_empty_cells = [cell for cell in row if cell and str(cell).strip()]
                if not non_empty_cells:
                    return False
                return all(re.match(r'^(Col|列)\d+$', str(cell).strip()) for cell in non_empty_cells)

            original_row_count = len(sub_data)
            sub_data = [row for row in sub_data if not _is_auto_column_name_row(row)]
            if len(sub_data) < original_row_count:
                logger.info(f"[G-17] 自動列名のみの行を除外: {original_row_count}行 → {len(sub_data)}行")

            if not sub_data:
                continue

            logger.info(f"[G-17] 入力データ全行:")
            for row_idx, row in enumerate(sub_data):
                logger.info(f"[G-17]   行{row_idx}: {row}")

            label = f"{sub_idx + 1}/{len(sub_tables)}: {group_name} ({split_axis})" if group_name else str(sub_idx + 1)
            logger.info(f"\n[G-17]   サブセクション {label}")

            commonality, tokens2 = self._analyze_data_commonality(
                sub_data,
                f"{table_id}_{group_name}" if group_name else table_id
            )
            tokens += tokens2

            if not commonality.get('success'):
                logger.warning(f"[G-17]   共通性分析失敗 → スキップ")
                continue

            header_info = self._detect_headers_from_commonality(sub_data, commonality)

            table_semantics = commonality.get('table_semantics', {
                'type': 'other', 'type_ja': 'その他', 'target': None,
                'scope': 'none', 'date_range': None, 'confidence': 0.5
            })

            # human-readable タイトルを生成（chunk_content の先頭に使用）
            sem_target = table_semantics.get('target') or ''
            sem_type_ja = table_semantics.get('type_ja') or ''
            if sem_target and sem_type_ja:
                semantic_title = f"{sem_target} {sem_type_ja}"
            elif sem_type_ja:
                semantic_title = sem_type_ja
            else:
                semantic_title = f"{table_id} - {group_name}" if group_name else table_id

            title = f"{table_id} - {group_name}" if group_name else table_id
            processed_sections.append({
                'title': title,
                'semantic_title': semantic_title,
                'table_type': header_info.get('table_type', 'structured'),
                'description': commonality.get('description', ''),
                'data': sub_data,
                'metadata': {
                    'header_rows': header_info['header_rows'],
                    'row_label_col': header_info['row_label_col'],
                    'data_start_row': header_info['data_start_row'],
                    'col_map': header_info['col_map'],
                    'header_meanings': header_info.get('header_meanings', {}),
                    'row_range': [0, len(sub_data) - 1],
                    'implicit_headers': header_info.get('implicit_headers', []),
                    'split_from': group_name,
                    'primary_header_candidate': group_name,
                    'table_semantics': table_semantics,
                }
            })
            total_records += len(sub_data)

        if len(processed_sections) == 0:
            return {'table_type': 'empty', 'sections': []}, tokens

        return {
            'table_type': 'multi_section' if len(processed_sections) > 1 else processed_sections[0].get('table_type', 'structured'),
            'description': f"{len(processed_sections)}個のセクションを含む表" if len(processed_sections) > 1 else processed_sections[0].get('description', ''),
            'sections': processed_sections,
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

            logger.info(f"[G-17] セクション分析 AI応答:\n{raw}")

            json_str = self._extract_json(raw)
            parsed = json.loads(json_str)
            parsed['success'] = True
            return parsed, tokens

        except Exception as e:
            logger.warning(f"[G-17] セクション分析失敗: {e}")
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
3. このテーブル全体の意味・種別・対象を分類する（table_semantics）

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

## table_semantics の判定基準

### type（テーブル種別）
- `timetable`：時間割（曜日×時限×科目）
- `schedule`：行事予定・日程表（日付×予定）
- `homework`：宿題・課題・提出物の一覧
- `checklist`：持ち物・準備・確認リスト
- `roster`：名簿・係・担当者表
- `price`：料金・費用・金額表
- `results`：成績・得点・採点結果
- `agenda`：議事録・議題・決定事項
- `contact`：連絡先・電話番号・メール
- `other`：上記以外

### target（対象）
- クラス名（例：5A、5B、3年1組）、学年（例：5年生、全学年）、グループ名、人名など
- 複数対象の場合はカンマ区切り（例：「5A,5B」）
- 対象が特定できない場合は null

### scope（時間粒度）
- `weekly`：週単位、`monthly`：月単位、`daily`：日単位
- `semester`：学期、`yearly`：年間
- `none`：時間に無関係

## 出力（JSONのみ）

```json
{{
  "success": true,
  "description": "この表の説明（1-2文）",
  "table_semantics": {{
    "type": "timetable",
    "type_ja": "週間時間割",
    "target": "5B",
    "scope": "weekly",
    "date_range": null,
    "confidence": 0.95
  }},
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
- table_semantics は必ず出力してください（type が不明な場合は "other"）
"""

        try:
            response = self.model.generate_content(prompt)
            raw = response.text
            tokens = len(prompt + raw) // 4

            logger.info(f"[G-17] 共通性分析 AI応答:\n{raw}")

            json_str = self._extract_json(raw)
            parsed = json.loads(json_str)
            parsed['success'] = True

            # table_semantics のバリデーション
            sem = parsed.get('table_semantics')
            if sem and isinstance(sem, dict):
                valid_types = {'timetable', 'schedule', 'homework', 'checklist', 'roster',
                               'price', 'results', 'agenda', 'contact', 'other'}
                if sem.get('type') not in valid_types:
                    sem['type'] = 'other'
                if not sem.get('type_ja'):
                    sem['type_ja'] = sem.get('type', 'other')
                logger.info(f"[G-17] table_semantics: type={sem.get('type')}, target={sem.get('target')}, scope={sem.get('scope')}")
            else:
                parsed['table_semantics'] = {'type': 'other', 'type_ja': 'その他', 'target': None, 'scope': 'none', 'date_range': None, 'confidence': 0.5}

            return parsed, tokens

        except Exception as e:
            logger.warning(f"[G-17] 共通性分析失敗: {e}")
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
            logger.info("[G-17] 明示的ヘッダー行なし → 暗黙的ヘッダーを生成")
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

        logger.info(f"[G-17] ヘッダー検出: header_rows={header_rows}, row_label_col={row_label_col}, data_start={data_start_row}")
        if implicit_headers:
            logger.info(f"[G-17] 暗黙的ヘッダー: {implicit_headers}")

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
