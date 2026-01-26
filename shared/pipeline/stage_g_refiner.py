"""
Stage G: Integration Refiner (統合精錬)

【設計 2026-01-26】E + F 独立出力の統合

役割: Stage E（物理抽出）と Stage F（独立読解）の結果を統合し、
      後続ステージが引用しやすい「ID付き目録」を作成

============================================
入力:
  - stage_e_result: 物理抽出テキスト（PDF/Office/テキスト）
  - stage_f_payload: 独立読解結果（Path A + Path B）
  - post_body: 投稿本文

出力:
  - unified_text: 重複のない統合テキスト（正本）
  - source_inventory: REF_ID付きセグメントリスト
  - table_inventory: REF_ID付き表リスト
  - cross_validation: E vs F の突き合わせ結果

特徴:
  - Flash-Lite を使用（低コスト）
  - E と F が独立しているため、一致箇所 = 高信頼
  - 不一致箇所 = 要確認としてマーク
============================================
"""
import json
from typing import Dict, Any, List, Optional
from loguru import logger

from shared.ai.llm_client.llm_client import LLMClient
from .constants import STAGE_H_INPUT_SCHEMA_VERSION


class StageGRefiner:
    """Stage G: 統合精錬（E + F の突き合わせ）"""

    def __init__(self, llm_client: LLMClient):
        """
        Args:
            llm_client: LLMクライアント
        """
        self.llm = llm_client

    def process(
        self,
        stage_e_result: Dict[str, Any],
        stage_f_payload: Dict[str, Any],
        post_body: Optional[Dict[str, Any]] = None,
        model: str = "gemini-2.0-flash-lite",
        workspace: str = "default"
    ) -> Dict[str, Any]:
        """
        Stage E と Stage F の結果を統合

        Args:
            stage_e_result: Stage E の出力
            stage_f_payload: Stage F の出力
            post_body: 投稿本文
            model: 使用するモデル（デフォルト: Lite）
            workspace: ワークスペース

        Returns:
            {
                'unified_text': str,  # 統合Markdown全文
                'source_inventory': List[Dict],  # ID付きセグメント
                'table_inventory': List[Dict],  # ID付き表
                'cross_validation': Dict,  # E vs F 突き合わせ結果
                'ref_count': int,
                'warnings': List[str]
            }
        """
        logger.info(f"[Stage G] 統合精錬開始... (model={model})")

        # 入力データ取得
        e_content = stage_e_result.get('content', '')
        e_method = stage_e_result.get('method', 'unknown')

        f_path_a = stage_f_payload.get('path_a_result', {})
        f_path_b = stage_f_payload.get('path_b_result', {})
        f_processing_mode = stage_f_payload.get('processing_mode', 'unknown')

        logger.info(f"[Stage G] 入力:")
        logger.info(f"  ├─ Stage E: {len(e_content)}文字 (method={e_method})")
        logger.info(f"  ├─ Stage F Path A: {len(str(f_path_a))}文字")
        logger.info(f"  ├─ Stage F Path B: {len(str(f_path_b))}文字")
        logger.info(f"  └─ F processing_mode: {f_processing_mode}")

        # 添付なし（E, F 両方スキップ）の場合
        if not e_content and f_processing_mode == 'skipped':
            logger.info("[Stage G] 添付なし → post_body のみで処理")
            return self._process_post_body_only(post_body)

        # Stage F がスキップされた場合（ドキュメントのみ）
        if f_processing_mode == 'skipped':
            logger.info("[Stage G] Stage F スキップ → Stage E のみで処理")
            return self._process_e_only(stage_e_result, post_body)

        # 音声/動画の場合（Transcription のみ）
        if f_processing_mode == 'transcription_only':
            logger.info("[Stage G] Transcription モード → F-7 結果を使用")
            return self._process_transcription(stage_f_payload, post_body)

        # 通常の画像/PDF処理（E + F 統合）
        logger.info("[Stage G] Dual Vision モード → E + F 統合")

        # 入力が少ない場合はルールベースで処理
        total_input_size = len(e_content) + len(f_path_a.get('full_text', ''))
        if total_input_size < 200:
            logger.info("[Stage G] 入力が少ないためルールベース処理")
            return self._rule_based_merge(stage_e_result, stage_f_payload, post_body)

        # LLMによる統合
        try:
            result = self._llm_merge(stage_e_result, stage_f_payload, post_body, model)
            logger.info(f"[Stage G] LLM統合完了: ref_count={result.get('ref_count', 0)}")
            return result
        except Exception as e:
            logger.warning(f"[Stage G] LLM統合失敗、フォールバック: {e}")
            return self._rule_based_merge(stage_e_result, stage_f_payload, post_body)

    def _process_post_body_only(self, post_body: Optional[Dict]) -> Dict[str, Any]:
        """投稿本文のみの処理（添付なし）"""
        text = post_body.get('text', '') if post_body else ''

        source_inventory = []
        if text:
            source_inventory.append({
                'ref_id': 'REF_001',
                'text': text,
                'type': 'post_body',
                'source': 'post_body',
                'confidence': 'high'
            })

        return {
            'unified_text': text,
            'source_inventory': source_inventory,
            'table_inventory': [],
            'cross_validation': {'mode': 'post_body_only'},
            'ref_count': len(source_inventory),
            'warnings': [],
            'processing_mode': 'post_body_only',
            'post_body': post_body or {}
        }

    def _process_e_only(
        self,
        stage_e_result: Dict[str, Any],
        post_body: Optional[Dict]
    ) -> Dict[str, Any]:
        """Stage E のみの処理（ドキュメント、Stage F スキップ）"""
        e_content = stage_e_result.get('content', '')

        source_inventory = []
        ref_index = 1

        # post_body を先頭に
        if post_body and post_body.get('text'):
            source_inventory.append({
                'ref_id': f'REF_{ref_index:03d}',
                'text': post_body['text'],
                'type': 'post_body',
                'source': 'post_body',
                'confidence': 'high'
            })
            ref_index += 1

        # Stage E テキストを追加
        if e_content:
            # 段落に分割
            paragraphs = self._split_paragraphs(e_content)
            for para in paragraphs:
                if para.strip():
                    source_inventory.append({
                        'ref_id': f'REF_{ref_index:03d}',
                        'text': para.strip(),
                        'type': 'paragraph',
                        'source': 'stage_e',
                        'confidence': 'high'
                    })
                    ref_index += 1

        # unified_text 構築
        unified_parts = []
        if post_body and post_body.get('text'):
            unified_parts.append(post_body['text'])
        if e_content:
            unified_parts.append(e_content)
        unified_text = '\n\n'.join(unified_parts)

        return {
            'unified_text': unified_text,
            'source_inventory': source_inventory,
            'table_inventory': [],
            'cross_validation': {'mode': 'e_only'},
            'ref_count': len(source_inventory),
            'warnings': [],
            'processing_mode': 'e_only',
            'post_body': post_body or {}
        }

    def _process_transcription(
        self,
        stage_f_payload: Dict[str, Any],
        post_body: Optional[Dict]
    ) -> Dict[str, Any]:
        """音声/動画の Transcription 処理"""
        f_path_a = stage_f_payload.get('path_a_result', {})
        transcript = f_path_a.get('transcript', '')
        visual_log = f_path_a.get('visual_log', '')

        source_inventory = []
        ref_index = 1

        # post_body を先頭に
        if post_body and post_body.get('text'):
            source_inventory.append({
                'ref_id': f'REF_{ref_index:03d}',
                'text': post_body['text'],
                'type': 'post_body',
                'source': 'post_body',
                'confidence': 'high'
            })
            ref_index += 1

        # Transcript を追加
        if transcript:
            source_inventory.append({
                'ref_id': f'REF_{ref_index:03d}',
                'text': transcript,
                'type': 'transcript',
                'source': 'stage_f.path_a',
                'confidence': 'high'
            })
            ref_index += 1

        # Visual log を追加（動画の場合）
        if visual_log:
            source_inventory.append({
                'ref_id': f'REF_{ref_index:03d}',
                'text': visual_log,
                'type': 'visual_log',
                'source': 'stage_f.path_a',
                'confidence': 'high'
            })
            ref_index += 1

        # unified_text 構築
        unified_parts = []
        if post_body and post_body.get('text'):
            unified_parts.append(f"【投稿本文】\n{post_body['text']}")
        if transcript:
            unified_parts.append(f"【書き起こし】\n{transcript}")
        if visual_log:
            unified_parts.append(f"【映像ログ】\n{visual_log}")
        unified_text = '\n\n---\n\n'.join(unified_parts)

        return {
            'unified_text': unified_text,
            'source_inventory': source_inventory,
            'table_inventory': [],
            'cross_validation': {'mode': 'transcription'},
            'ref_count': len(source_inventory),
            'warnings': [],
            'processing_mode': 'transcription',
            'post_body': post_body or {}
        }

    def _rule_based_merge(
        self,
        stage_e_result: Dict[str, Any],
        stage_f_payload: Dict[str, Any],
        post_body: Optional[Dict]
    ) -> Dict[str, Any]:
        """ルールベースの統合（LLMなし）"""
        e_content = stage_e_result.get('content', '')

        # 新しい Stage F 出力形式に対応
        f_full_text = stage_f_payload.get('full_text', '')
        f_blocks = stage_f_payload.get('text_blocks', [])
        f_tables = stage_f_payload.get('tables', [])

        # フォールバック: 旧形式にも対応
        if not f_full_text:
            f_path_a = stage_f_payload.get('path_a_result', {})
            f_full_text = f_path_a.get('full_text', '')
            if not f_blocks:
                f_blocks = f_path_a.get('blocks', [])

        source_inventory = []
        table_inventory = []
        ref_index = 1
        tbl_index = 1

        # 1. post_body を先頭に
        if post_body and post_body.get('text'):
            source_inventory.append({
                'ref_id': f'REF_{ref_index:03d}',
                'text': post_body['text'],
                'type': 'post_body',
                'source': 'post_body',
                'confidence': 'high'
            })
            ref_index += 1

        # 2. Stage F のブロックを追加
        for block in f_blocks:
            text = block.get('text', '')
            if text and text.strip():
                source_inventory.append({
                    'ref_id': f'REF_{ref_index:03d}',
                    'text': text,
                    'type': block.get('block_type', 'paragraph'),
                    'source': f"stage_f.{block.get('block_id', 'unknown')}",
                    'confidence': block.get('confidence', 'medium')
                })
                ref_index += 1

        # 3. Stage E から差分追加（F に含まれないもの）
        # 【知能的重複排除】同じ内容が本文と表にある場合、表を優先
        if e_content:
            f_text_lower = f_full_text.lower() if f_full_text else ''
            e_paragraphs = self._split_paragraphs(e_content)

            for para in e_paragraphs:
                para_clean = para.strip()
                if para_clean and para_clean.lower() not in f_text_lower:
                    # 表データと重複していないかチェック
                    is_in_table = self._is_text_in_tables(para_clean, f_tables)
                    if not is_in_table:
                        source_inventory.append({
                            'ref_id': f'REF_{ref_index:03d}',
                            'text': para_clean,
                            'type': 'paragraph',
                            'source': 'stage_e.diff',
                            'confidence': 'medium',
                            'note': 'Stage F に含まれない追加情報'
                        })
                        ref_index += 1

        # 4. 表を TBL_ID 付きで追加（完全な構造を維持）
        for tbl in f_tables:
            block_id = tbl.get('block_id', '')

            # 表データの完全性を確認
            headers = tbl.get('headers', [])
            rows = tbl.get('rows', [])

            table_entry = {
                'ref_id': f'TBL_{tbl_index:03d}',
                'block_id': block_id,
                'table_title': tbl.get('table_title', ''),
                'table_type': tbl.get('table_type', 'visual_table'),
                'headers': headers,
                'rows': rows,
                'row_count': tbl.get('row_count', len(rows)),
                'col_count': tbl.get('col_count', len(headers)),
                'caption': tbl.get('caption', ''),
                'structure': tbl.get('structure', {}),
                'semantic_role': tbl.get('semantic_role', ''),
                'source': f"stage_f.{block_id}"
            }
            table_inventory.append(table_entry)
            tbl_index += 1

        # unified_text 構築（表データも含める）
        unified_parts = []
        for item in source_inventory:
            unified_parts.append(item['text'])

        # 表データをMarkdown形式で追加
        for tbl in table_inventory:
            table_md = self._table_to_markdown(tbl)
            if table_md:
                unified_parts.append(f"\n【{tbl.get('table_title', '表')}】\n{table_md}")

        unified_text = '\n\n'.join(unified_parts)

        # cross_validation（簡易版）
        cross_validation = {
            'mode': 'rule_based',
            'e_char_count': len(e_content),
            'f_char_count': len(f_full_text),
            'table_count': len(table_inventory),
            'total_table_rows': sum(t.get('row_count', 0) for t in table_inventory),
            'overlap_estimate': 'not_calculated'
        }

        return {
            'unified_text': unified_text,
            'source_inventory': source_inventory,
            'table_inventory': table_inventory,
            'cross_validation': cross_validation,
            'ref_count': len(source_inventory) + len(table_inventory),
            'warnings': [],
            'processing_mode': 'rule_based',
            'post_body': post_body or {}
        }

    def _is_text_in_tables(self, text: str, tables: List[Dict]) -> bool:
        """テキストが表データに含まれているかチェック"""
        text_lower = text.lower().strip()
        if len(text_lower) < 10:
            return False

        for table in tables:
            # headers をチェック
            headers = table.get('headers', [])
            for header in headers:
                if isinstance(header, str) and header.lower() in text_lower:
                    return True

            # rows をチェック
            rows = table.get('rows', [])
            for row in rows:
                if isinstance(row, list):
                    row_text = ' '.join(str(cell) for cell in row).lower()
                    if row_text in text_lower or text_lower in row_text:
                        return True

        return False

    def _table_to_markdown(self, table: Dict) -> str:
        """表をMarkdown形式に変換"""
        headers = table.get('headers', [])
        rows = table.get('rows', [])

        if not headers and not rows:
            return ""

        lines = []

        # ヘッダー行
        if headers:
            lines.append("| " + " | ".join(str(h) for h in headers) + " |")
            lines.append("|" + "|".join(["---"] * len(headers)) + "|")

        # データ行
        for row in rows:
            if isinstance(row, list):
                lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
            elif isinstance(row, dict):
                # dict形式の場合はheadersの順序で出力
                cells = [str(row.get(h, '')) for h in headers]
                lines.append("| " + " | ".join(cells) + " |")

        return "\n".join(lines)

    def _llm_merge(
        self,
        stage_e_result: Dict[str, Any],
        stage_f_payload: Dict[str, Any],
        post_body: Optional[Dict],
        model: str
    ) -> Dict[str, Any]:
        """LLMによる統合（重複排除とクロスバリデーション）"""
        e_content = stage_e_result.get('content', '')

        # 新しい Stage F 出力形式に対応
        f_path_a = {
            'full_text': stage_f_payload.get('full_text', ''),
            'blocks': stage_f_payload.get('text_blocks', []),
            'tables': stage_f_payload.get('tables', [])
        }

        # フォールバック: 旧形式にも対応
        if not f_path_a['full_text']:
            old_path_a = stage_f_payload.get('path_a_result', {})
            f_path_a['full_text'] = old_path_a.get('full_text', '')
            if not f_path_a['blocks']:
                f_path_a['blocks'] = old_path_a.get('blocks', [])

        f_path_b = stage_f_payload.get('visual_elements', {})

        # プロンプト構築
        prompt = self._build_merge_prompt(e_content, f_path_a, f_path_b, post_body)

        # LLM呼び出し
        response = self.llm.generate(
            prompt=prompt,
            model=model,
            temperature=0.1,
            response_format='json'
        )

        # JSON パース
        try:
            result = json.loads(response)
        except json.JSONDecodeError:
            import json_repair
            result = json_repair.repair_json(response, return_objects=True)

        # 結果を正規化
        return self._normalize_llm_result(result, stage_e_result, stage_f_payload, post_body)

    def _build_merge_prompt(
        self,
        e_content: str,
        f_path_a: Dict,
        f_path_b: Dict,
        post_body: Optional[Dict]
    ) -> str:
        """統合用プロンプト構築（表抽出強化版）"""
        f_full_text = f_path_a.get('full_text', '')[:5000]
        f_tables = f_path_a.get('tables', [])[:10]  # 最大10テーブル

        prompt = f"""あなたは文書統合の専門家です。
2つの独立したソース（Stage E: 物理抽出、Stage F: AI読解）から得られた情報を統合し、
「重複が一切なく、かつ1文字も情報の欠落がない」正本を作成してください。

【Stage E（物理抽出）】
{e_content[:3000] if e_content else '(なし)'}

【Stage F（AI読解 - テキスト）】
{f_full_text if f_full_text else '(なし)'}

【Stage F（AI読解 - 表データ）】
{json.dumps(f_tables, ensure_ascii=False, indent=2)[:4000] if f_tables else '(なし)'}

【投稿本文】
{post_body.get('text', '')[:1000] if post_body else '(なし)'}

【統合ルール】
1. **クロスバリデーション**: E と F で一致する情報 = 高信頼（confidence: high）
2. **差分追加**: E にあって F にない情報、または F にあって E にない情報も追加（confidence: medium）
3. **知能的重複排除**: 同じ内容が「本文」と「表」にある場合、**表データを優先**して二重書きを解消
4. **REF_ID付与**: 各テキストセグメントに REF_001, REF_002... を付与
5. **TBL_ID付与**: 各表に TBL_001, TBL_002... を付与

【⚠️ 表データ抽出の絶対ルール】

**表データは必ず headers と rows で構造化。テキスト要約は絶対禁止！**

❌ **禁止（data_summary でテキスト要約）**:
```json
{{"table_title": "成績優秀者", "data_summary": "1位は山田（520点）、2位は田中..."}}
```

✅ **正解（rows に全行を展開）**:
```json
{{
  "ref_id": "TBL_001",
  "table_title": "成績優秀者",
  "table_type": "ranking",
  "headers": ["順位", "氏名", "点数"],
  "rows": [
    ["1", "山田太郎", "520"],
    ["2", "田中花子", "515"]
  ]
}}
```

**表として構造化すべきデータ**:
- ランキング・順位表（全員分を rows に）
- 名簿・リスト（全項目を rows に）
- key-value ペア（項目名/値 の2列テーブルに）
- カンマ区切りデータ（各要素を別々の行に展開）
- マトリクス形式（時間割、予定表など）

**セル内にカンマやセミコロンが残っている = 構造化が不十分**

【出力JSON形式】
{{
  "unified_text": "統合された全文テキスト",
  "source_inventory": [
    {{"ref_id": "REF_001", "text": "...", "type": "post_body", "source": "post_body", "confidence": "high"}},
    {{"ref_id": "REF_002", "text": "...", "type": "paragraph", "source": "stage_e+stage_f", "confidence": "high"}}
  ],
  "table_inventory": [
    {{
      "ref_id": "TBL_001",
      "table_title": "表のタイトル",
      "table_type": "visual_table|ranking|requirements|item_list|schedule|metadata",
      "headers": ["列1", "列2"],
      "rows": [["値1", "値2"], ["値3", "値4"]],
      "row_count": 2,
      "col_count": 2,
      "source": "stage_f"
    }}
  ],
  "cross_validation": {{
    "matched_segments": 5,
    "e_only_segments": 1,
    "f_only_segments": 2,
    "table_count": 3,
    "total_table_rows": 15,
    "confidence_summary": "E と F の一致率が高く、信頼性が高い"
  }}
}}

重要:
- source_inventory には全てのテキスト情報を含めてください。削除や要約は厳禁です。
- table_inventory には全ての表を、全行を含めて格納してください。
- 表の一部だけを抽出することは禁止です。"""

        return prompt

    def _normalize_llm_result(
        self,
        llm_result: Dict[str, Any],
        stage_e_result: Dict[str, Any],
        stage_f_payload: Dict[str, Any],
        post_body: Optional[Dict]
    ) -> Dict[str, Any]:
        """LLM結果を正規化（表データ検証強化版）"""
        unified_text = llm_result.get('unified_text', '')
        source_inventory = llm_result.get('source_inventory', [])
        table_inventory = llm_result.get('table_inventory', [])
        cross_validation = llm_result.get('cross_validation', {})

        # unified_text が空の場合はフォールバック
        if not unified_text:
            fallback = self._rule_based_merge(stage_e_result, stage_f_payload, post_body)
            unified_text = fallback['unified_text']
            if not source_inventory:
                source_inventory = fallback['source_inventory']
            if not table_inventory:
                table_inventory = fallback['table_inventory']

        # REF_ID の正規化
        for i, item in enumerate(source_inventory):
            if 'ref_id' not in item:
                item['ref_id'] = f'REF_{i+1:03d}'

        # TBL_ID の正規化と表データ検証
        warnings = []
        for i, item in enumerate(table_inventory):
            if 'ref_id' not in item:
                item['ref_id'] = f'TBL_{i+1:03d}'

            # data_summary の検出（禁止パターン）
            if 'data_summary' in item:
                warnings.append(f"G_TABLE_ERROR: {item['ref_id']} uses data_summary (PROHIBITED)")

            # headers と rows の存在確認
            if not item.get('headers') and not item.get('rows'):
                warnings.append(f"G_TABLE_WARN: {item['ref_id']} has no headers and no rows")

            # row_count の正規化
            if 'rows' in item and 'row_count' not in item:
                item['row_count'] = len(item['rows'])
            if 'headers' in item and 'col_count' not in item:
                item['col_count'] = len(item['headers'])

        if not source_inventory:
            warnings.append("G_WARN: source_inventory is empty")

        # 表統計を cross_validation に追加
        if 'table_count' not in cross_validation:
            cross_validation['table_count'] = len(table_inventory)
        if 'total_table_rows' not in cross_validation:
            cross_validation['total_table_rows'] = sum(t.get('row_count', 0) for t in table_inventory)

        return {
            'unified_text': unified_text,
            'source_inventory': source_inventory,
            'table_inventory': table_inventory,
            'cross_validation': cross_validation,
            'ref_count': len(source_inventory) + len(table_inventory),
            'warnings': warnings,
            'processing_mode': 'llm_merged',
            'post_body': post_body or {}
        }

    def _split_paragraphs(self, text: str) -> List[str]:
        """テキストを段落に分割"""
        import re
        # 空行または2連続改行で分割
        paragraphs = re.split(r'\n\s*\n|\r\n\s*\r\n', text)
        return [p.strip() for p in paragraphs if p.strip()]
