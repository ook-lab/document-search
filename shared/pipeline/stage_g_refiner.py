"""
Stage G: Logical Refinement (論理的精錬)

【設計 2026-01-24】Stage F → G → H+I の情報進化パイプライン
============================================
役割: Stage F の冗長な出力を整理し、後続が引用しやすい「ID付き目録」を作成

入力 (Stage F payload):
  - full_text: OCR全文
  - text_blocks: 段落単位のブロック (block_id付き)
  - tables: 表構造データ
  - post_body: 投稿本文

出力:
  - unified_text: 重複のない1本のMarkdown全文（正本）
  - source_inventory: REF_ID付きの整理済みセグメントリスト
  - table_inventory: REF_ID付きの表リスト

特徴:
  - Flash-Lite を使用（低コスト）
  - 重複排除（full_text, blocks, tables間の重複を解消）
  - REF_ID付与（後続のH+Iが参照可能）
  - 情報の完全維持（1文字も削除しない）
============================================
"""
import json
from typing import Dict, Any, List, Optional
from loguru import logger

from shared.ai.llm_client.llm_client import LLMClient


class StageGRefiner:
    """Stage G: 論理的精錬（Lite モデル使用）"""

    def __init__(self, llm_client: LLMClient):
        """
        Args:
            llm_client: LLMクライアント
        """
        self.llm = llm_client

    def process(
        self,
        stage_f_payload: Dict[str, Any],
        model: str = "gemini-2.0-flash-lite",
        workspace: str = "default"
    ) -> Dict[str, Any]:
        """
        Stage F の出力を論理的に整理してID付き目録を作成

        Args:
            stage_f_payload: Stage F の出力JSON
            model: 使用するモデル（デフォルト: Lite）
            workspace: ワークスペース

        Returns:
            {
                'unified_text': str,  # 整理済みMarkdown全文
                'source_inventory': List[Dict],  # ID付きセグメント
                'table_inventory': List[Dict],  # ID付き表
                'ref_count': int,  # 振られたID数
                'warnings': List[str]
            }
        """
        logger.info(f"[Stage G] 論理的精錬開始... (model={model})")

        # Stage F の出力を取得
        full_text = stage_f_payload.get('full_text', '')
        text_blocks = stage_f_payload.get('text_blocks', [])
        tables = stage_f_payload.get('tables', [])
        post_body = stage_f_payload.get('post_body', {})

        logger.info(f"[Stage G] 入力: full_text={len(full_text)}文字, blocks={len(text_blocks)}個, tables={len(tables)}個")

        # 入力が少ない場合はLLMを使わずルールベースで処理
        if len(full_text) < 100 and len(text_blocks) < 3:
            logger.info("[Stage G] 入力が少ないためルールベース処理")
            return self._rule_based_refine(stage_f_payload)

        # LLMによる整理
        try:
            result = self._llm_refine(stage_f_payload, model)
            logger.info(f"[Stage G] LLM整理完了: ref_count={result.get('ref_count', 0)}")
            return result
        except Exception as e:
            logger.warning(f"[Stage G] LLM整理失敗、フォールバック: {e}")
            return self._rule_based_refine(stage_f_payload)

    def _rule_based_refine(self, stage_f_payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        ルールベースの整理（LLMを使わない）
        - text_blocks にそのままREF_IDを振る
        - tables にそのままREF_IDを振る
        - full_text をそのまま unified_text に
        """
        full_text = stage_f_payload.get('full_text', '')
        text_blocks = stage_f_payload.get('text_blocks', [])
        tables = stage_f_payload.get('tables', [])
        post_body = stage_f_payload.get('post_body', {})

        # post_body を先頭に追加
        source_inventory = []
        ref_index = 1

        if post_body.get('text'):
            source_inventory.append({
                'ref_id': f'REF_{ref_index:03d}',
                'text': post_body['text'],
                'type': 'post_body',
                'source': 'stage_f.post_body'
            })
            ref_index += 1

        # text_blocks をそのまま追加
        for block in text_blocks:
            text = block.get('text') or block.get('content', '')
            if text and text.strip():
                source_inventory.append({
                    'ref_id': f'REF_{ref_index:03d}',
                    'text': text,
                    'type': block.get('block_type', 'paragraph'),
                    'source': f"stage_f.text_blocks[{block.get('block_id', 'unknown')}]"
                })
                ref_index += 1

        # tables をID付きで追加
        table_inventory = []
        for i, table in enumerate(tables):
            table_inventory.append({
                'ref_id': f'TBL_{i+1:03d}',
                'table_title': table.get('table_title', f'表{i+1}'),
                'table_type': table.get('table_type', 'unknown'),
                'rows': table.get('rows', []),
                'source': f'stage_f.tables[{i}]'
            })

        # unified_text は full_text をそのまま使用
        unified_text = full_text

        return {
            'unified_text': unified_text,
            'source_inventory': source_inventory,
            'table_inventory': table_inventory,
            'ref_count': len(source_inventory) + len(table_inventory),
            'warnings': [],
            'processing_mode': 'rule_based'
        }

    def _llm_refine(self, stage_f_payload: Dict[str, Any], model: str) -> Dict[str, Any]:
        """
        LLMによる整理（重複排除とID付与）
        """
        full_text = stage_f_payload.get('full_text', '')
        text_blocks = stage_f_payload.get('text_blocks', [])
        tables = stage_f_payload.get('tables', [])
        post_body = stage_f_payload.get('post_body', {})

        # プロンプト構築
        prompt = self._build_refine_prompt(full_text, text_blocks, tables, post_body)

        # LLM呼び出し
        response = self.llm.generate(
            prompt=prompt,
            model=model,
            temperature=0.1,  # 低温で確実に
            response_format='json'
        )

        # JSON パース
        try:
            result = json.loads(response)
        except json.JSONDecodeError:
            # JSON修復を試みる
            import json_repair
            result = json_repair.repair_json(response, return_objects=True)

        # 結果を正規化
        return self._normalize_result(result, stage_f_payload)

    def _build_refine_prompt(
        self,
        full_text: str,
        text_blocks: List[Dict],
        tables: List[Dict],
        post_body: Dict
    ) -> str:
        """整理用プロンプトを構築"""

        # text_blocks を簡略化（長すぎる場合）
        blocks_summary = []
        for i, block in enumerate(text_blocks[:50]):  # 最大50ブロック
            text = block.get('text') or block.get('content', '')
            if len(text) > 500:
                text = text[:500] + '...'
            blocks_summary.append({
                'index': i,
                'block_id': block.get('block_id'),
                'type': block.get('block_type'),
                'text': text
            })

        # tables を簡略化
        tables_summary = []
        for i, table in enumerate(tables[:10]):  # 最大10テーブル
            tables_summary.append({
                'index': i,
                'title': table.get('table_title', f'表{i+1}'),
                'type': table.get('table_type'),
                'row_count': len(table.get('rows', []))
            })

        prompt = f"""あなたは公文書の管理・整理の専門家です。
Stage Fで物理的に抽出された冗長なデータを整理し、「重複が一切なく、かつ1文字も情報の欠落がない」正本JSONを作成してください。

【入力データ】
投稿本文（post_body）: {json.dumps(post_body.get('text', '')[:1000] if post_body else '', ensure_ascii=False)}

テキストブロック（{len(text_blocks)}個）:
{json.dumps(blocks_summary, ensure_ascii=False, indent=2)}

表データ（{len(tables)}個）:
{json.dumps(tables_summary, ensure_ascii=False, indent=2)}

【整理ルール（能力の死守）】
1. **情報の完全維持**: 数値、日付、小さな注釈、手書き文字の読み取り結果など、入力にある全ての事実を1文字も漏らさず残してください。
2. **重複の知能的排除**: 同じ内容が「本文」と「表」にある場合、より構造的に正しい方（例：表データ）を優先し、二重書きを解消してください。
3. **参照IDの付与**: 各要素に `REF_001` から始まる一意の参照IDを付与してください。
4. **統合Markdownの生成**: 読み順に従い、人間が通読可能な「お掃除済み」の1本のMarkdownテキスト（unified_text）を作成してください。

【出力JSON形式】
{{
  "unified_text": "整理されたMarkdown全文（ここが後の正本になる）",
  "source_inventory": [
    {{"ref_id": "REF_001", "text": "内容...", "type": "paragraph", "original_index": 0}},
    {{"ref_id": "REF_002", "text": "内容...", "type": "heading", "original_index": 1}}
  ],
  "table_inventory": [
    {{"ref_id": "TBL_001", "table_title": "表タイトル", "type": "schedule", "original_index": 0}}
  ],
  "dedup_report": {{
    "removed_duplicates": 0,
    "merged_blocks": 0
  }}
}}

重要: source_inventory には、入力のtext_blocksを整理した結果を全て含めてください。削除や要約は厳禁です。"""

        return prompt

    def _normalize_result(
        self,
        llm_result: Dict[str, Any],
        stage_f_payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """LLM結果を正規化"""

        unified_text = llm_result.get('unified_text', '')
        source_inventory = llm_result.get('source_inventory', [])
        table_inventory = llm_result.get('table_inventory', [])

        # unified_text が空の場合は full_text を使用
        if not unified_text:
            unified_text = stage_f_payload.get('full_text', '')

        # source_inventory が空の場合はルールベースで生成
        if not source_inventory:
            rule_result = self._rule_based_refine(stage_f_payload)
            source_inventory = rule_result['source_inventory']
            table_inventory = rule_result['table_inventory']

        # REF_ID の正規化（連番保証）
        for i, item in enumerate(source_inventory):
            if 'ref_id' not in item:
                item['ref_id'] = f'REF_{i+1:03d}'

        for i, item in enumerate(table_inventory):
            if 'ref_id' not in item:
                item['ref_id'] = f'TBL_{i+1:03d}'

        warnings = []
        dedup_report = llm_result.get('dedup_report', {})
        if dedup_report.get('removed_duplicates', 0) > 0:
            warnings.append(f"重複排除: {dedup_report['removed_duplicates']}件")

        return {
            'unified_text': unified_text,
            'source_inventory': source_inventory,
            'table_inventory': table_inventory,
            'ref_count': len(source_inventory) + len(table_inventory),
            'warnings': warnings,
            'processing_mode': 'llm_refined',
            'dedup_report': dedup_report
        }
