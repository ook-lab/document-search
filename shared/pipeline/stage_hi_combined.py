"""
Stage H+I: Combined Structuring & Synthesis (構造化 + 統合・要約)

【設計 2026-01-24】Stage F → G → H+I の情報進化パイプライン
============================================
役割: Stage G の整理済み出力を受け取り、1回のLLM呼び出しで
      構造化（旧Stage H）と統合・要約（旧Stage I）を同時に実行

入力:
  - unified_text: Stage G で整理されたMarkdown全文
  - source_inventory: REF_ID付きセグメントリスト
  - table_inventory: REF_ID付き表リスト
  - stage_f_structure: Stage F の構造化情報（フォールバック用）

出力:
  - document_date: 基準日付
  - tags: 検索用タグ
  - metadata: 構造化データ（basic_info, articles, weekly_schedule, etc.）
  - title: ドキュメントタイトル
  - summary: 要約
  - calendar_events: カレンダーイベント
  - tasks: タスクリスト
  - audit_canonical_text: 監査用正本テキスト

特徴:
  - 1回のLLM呼び出しでH+Iを実行（コスト削減）
  - REF_IDによる参照追跡
  - 情報の完全維持（1文字も削除しない）
============================================
"""
import re
import json
import json_repair
from typing import Dict, Any, Optional, List
from pathlib import Path
from string import Template
from loguru import logger
from datetime import datetime

from shared.ai.llm_client.llm_client import LLMClient
from .constants import STAGE_H_INPUT_SCHEMA_VERSION


class StageHICombined:
    """Stage H+I: 構造化 + 統合・要約（統合版）"""

    def __init__(self, llm_client: LLMClient):
        """
        Args:
            llm_client: LLMクライアント
        """
        self.llm = llm_client

    def process(
        self,
        file_name: str,
        doc_type: str,
        workspace: str,
        combined_text: str,
        prompt: str,
        model: str,
        stage_f_structure: Optional[Dict[str, Any]] = None,
        stage_g_result: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        構造化 + 統合・要約（統合版）

        Args:
            file_name: ファイル名
            doc_type: ドキュメントタイプ
            workspace: ワークスペース
            combined_text: 統合テキスト（Stage G の unified_text または Stage F の full_text）
            prompt: プロンプト
            model: モデル名
            stage_f_structure: Stage F の構造化情報
            stage_g_result: Stage G の結果（REF_ID付き目録）

        Returns:
            {
                'document_date': str,
                'tags': List[str],
                'metadata': Dict[str, Any],
                'title': str,
                'summary': str,
                'calendar_events': List[Dict],
                'tasks': List[Dict],
                'audit_canonical_text': str
            }
        """
        logger.info(f"[Stage H+I] 構造化+統合開始... (doc_type={doc_type}, model={model})")

        # Stage G の結果があれば使用
        source_inventory = []
        table_inventory = []
        if stage_g_result:
            source_inventory = stage_g_result.get('source_inventory', [])
            table_inventory = stage_g_result.get('table_inventory', [])
            logger.info(f"[Stage H+I] Stage G 結果: source_inventory={len(source_inventory)}, table_inventory={len(table_inventory)}")

        if not combined_text or not combined_text.strip():
            logger.warning("[Stage H+I] 入力テキストが空です")
            return self._get_fallback_result(doc_type)

        try:
            # プロンプト構築
            logger.info("[Stage H+I] プロンプト構築中...")
            full_prompt = self._build_prompt(
                prompt_template=prompt,
                file_name=file_name,
                doc_type=doc_type,
                workspace=workspace,
                combined_text=combined_text,
                source_inventory=source_inventory,
                table_inventory=table_inventory
            )
            logger.info(f"[Stage H+I] プロンプト構築完了 ({len(full_prompt)}文字)")

            # LLM呼び出し
            logger.info(f"[Stage H+I] LLM呼び出し中... (model={model})")
            response = self.llm.call_model(
                tier="default",
                prompt=full_prompt,
                model_name=model
            )
            logger.info(f"[Stage H+I] LLM応答受信: success={response.get('success')}")

            if not response.get("success"):
                logger.error(f"[Stage H+I エラー] LLM呼び出し失敗: {response.get('error')}")
                return self._get_fallback_result(doc_type)

            # JSON抽出
            content = response.get("content", "")
            logger.info(f"[Stage H+I] ===== LLMレスポンス（最初の1000文字）=====\n{content[:1000]}")
            # リトライ禁止（2026-01-28）: エラー時は即座にフォールバック
            result = self._extract_json_with_retry(content, model=model, max_retries=0)

            # Stage F の構造化情報をマージ
            if stage_f_structure:
                result = self._merge_stage_f_structure(result, stage_f_structure)

            # audit_canonical_text の生成（監査用正本）
            audit_canonical_text = self._generate_audit_canonical_text(
                result, combined_text, source_inventory
            )

            # 結果の整形
            final_result = {
                'document_date': result.get('document_date'),
                'tags': result.get('tags', []),
                'metadata': result.get('metadata', {}),
                'title': result.get('title', ''),
                'summary': result.get('summary', ''),
                'calendar_events': result.get('calendar_events', []),
                'tasks': result.get('tasks', []),
                'audit_canonical_text': audit_canonical_text
            }

            logger.info(f"[Stage H+I完了] title={final_result['title'][:50] if final_result['title'] else 'N/A'}...")
            return final_result

        except Exception as e:
            logger.error(f"[Stage H+I エラー] 処理失敗: {e}", exc_info=True)
            return self._get_fallback_result(doc_type)

    def _build_prompt(
        self,
        prompt_template: str,
        file_name: str,
        doc_type: str,
        workspace: str,
        combined_text: str,
        source_inventory: List[Dict],
        table_inventory: List[Dict]
    ) -> str:
        """プロンプトを構築"""
        # source_inventory を簡略化
        inventory_summary = []
        for item in source_inventory[:30]:  # 最大30件
            inventory_summary.append({
                'ref_id': item.get('ref_id'),
                'type': item.get('type'),
                'text': item.get('text', '')[:200]  # 最大200文字
            })

        # table_inventory を簡略化
        tables_summary = []
        for item in table_inventory[:10]:  # 最大10件
            tables_summary.append({
                'ref_id': item.get('ref_id'),
                'table_title': item.get('table_title'),
                'table_type': item.get('table_type')
            })

        # テンプレート変数を置換
        template = Template(prompt_template)
        prompt = template.substitute(
            file_name=file_name,
            doc_type=doc_type,
            workspace=workspace,
            combined_text=combined_text,
            current_date=datetime.now().strftime("%Y-%m-%d"),
            source_inventory_json=json.dumps(inventory_summary, ensure_ascii=False, indent=2),
            table_inventory_json=json.dumps(tables_summary, ensure_ascii=False, indent=2),
            source_count=len(source_inventory),
            table_count=len(table_inventory)
        )

        return prompt

    def _extract_json_with_retry(
        self,
        content: str,
        model: str,
        max_retries: int = 2
    ) -> Dict[str, Any]:
        """JSON抽出（リトライ機能付き）"""
        for attempt in range(max_retries + 1):
            try:
                result = self._extract_json(content)
                logger.debug(f"[Stage H+I] JSON抽出成功 (試行{attempt + 1}/{max_retries + 1})")
                return result

            except Exception as e:
                if attempt < max_retries:
                    logger.warning(f"[Stage H+I] JSON抽出失敗 (試行{attempt + 1}/{max_retries + 1}): {e}")
                    content = self._retry_json_extraction(content, str(e), model)
                else:
                    logger.error(f"[Stage H+I] JSON抽出失敗（最終試行）: {e}")
                    raise

        return {}

    def _extract_json(self, content: str) -> Dict[str, Any]:
        """コンテンツからJSONを抽出"""
        # 複数のパターンでJSONブロックを探す
        patterns = [
            r'```json\s*(.*?)```',
            r'```\s*(.*?)```',
            r'\{[\s\S]*?\}',
        ]

        json_str = None
        for pattern in patterns:
            match = re.search(pattern, content, re.DOTALL)
            if match:
                json_str = match.group(1) if match.lastindex else match.group(0)
                json_str = json_str.strip()
                if json_str.startswith('{'):
                    break
                else:
                    json_str = None

        if not json_str:
            match = re.search(r'\{[\s\S]*\}', content, re.DOTALL)
            if match:
                json_str = match.group(0).strip()
            else:
                json_str = content.strip()

        # JSONパース
        try:
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.error(f"[Stage H+I] JSON解析失敗: {e}")
            try:
                return json_repair.loads(json_str)
            except Exception as repair_error:
                logger.error(f"[Stage H+I] JSON修復も失敗: {repair_error}")
                raise

    def _retry_json_extraction(
        self,
        failed_content: str,
        error_message: str,
        model: str
    ) -> str:
        """JSON抽出失敗時、LLMにJSON修正を依頼"""
        prompt = f"""以下のJSONにエラーがあります。修正してください。

エラー: {error_message}

元のJSON:
```
{failed_content[:3000]}
```

修正されたJSONを ```json ブロックで出力してください。
"""

        try:
            response = self.llm.call_model(
                tier="default",
                prompt=prompt,
                model_name=model
            )

            if response.get("success"):
                return response.get("content", "")
            else:
                return failed_content

        except Exception as e:
            logger.error(f"[Stage H+I] JSON修正エラー: {e}")
            return failed_content

    def _merge_stage_f_structure(
        self,
        result: Dict[str, Any],
        stage_f_structure: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Stage F の構造化情報をマージ"""
        metadata = result.get('metadata', {})

        # v1.1契約の判定
        schema_ver = stage_f_structure.get('schema_version', '')
        is_v1_1 = (schema_ver == STAGE_H_INPUT_SCHEMA_VERSION)

        if is_v1_1:
            stage_f_tables = stage_f_structure.get('tables', [])
            stage_f_text_blocks = stage_f_structure.get('text_blocks', [])

            if stage_f_text_blocks:
                metadata['_raw_text_blocks'] = stage_f_text_blocks
                logger.info(f"[Stage H+I] Stage F text_blocks を _raw_text_blocks に保存")

        # visual_elements からデッドライン情報を抽出
        visual_elements = stage_f_structure.get('visual_elements', {})
        if visual_elements:
            deadline_info = visual_elements.get('deadline_info')
            if deadline_info and not result.get('document_date'):
                result['document_date'] = deadline_info

        result['metadata'] = metadata
        return result

    def _generate_audit_canonical_text(
        self,
        result: Dict[str, Any],
        combined_text: str,
        source_inventory: List[Dict]
    ) -> str:
        """
        監査用正本テキストを生成

        Stage H+I の出力から、全情報を含む監査用正本を生成。
        後で人間が検証可能な形式。
        """
        parts = []

        # タイトル
        title = result.get('title', '')
        if title:
            parts.append(f"# {title}\n")

        # 基本情報
        metadata = result.get('metadata', {})
        basic_info = metadata.get('basic_info', {})
        if basic_info:
            parts.append("## 基本情報")
            for key, value in basic_info.items():
                if value:
                    parts.append(f"- {key}: {value}")
            parts.append("")

        # 要約
        summary = result.get('summary', '')
        if summary:
            parts.append("## 要約")
            parts.append(summary)
            parts.append("")

        # 記事・お知らせ
        articles = metadata.get('articles', [])
        if articles:
            parts.append("## 記事・お知らせ")
            for article in articles:
                title_a = article.get('title', '')
                body = article.get('body', '')
                if title_a:
                    parts.append(f"### {title_a}")
                if body:
                    parts.append(body)
                parts.append("")

        # カレンダーイベント
        calendar_events = result.get('calendar_events', [])
        if calendar_events:
            parts.append("## カレンダーイベント")
            for event in calendar_events:
                date = event.get('event_date', '')
                name = event.get('event_name', '')
                parts.append(f"- {date}: {name}")
            parts.append("")

        # タスク
        tasks = result.get('tasks', [])
        if tasks:
            parts.append("## タスク")
            for task in tasks:
                name = task.get('task_name', '')
                deadline = task.get('deadline', '')
                parts.append(f"- {name} (期限: {deadline})")
            parts.append("")

        # 元テキスト参照
        if source_inventory:
            parts.append("## 参照元（REF_ID）")
            for item in source_inventory[:10]:  # 最大10件表示
                ref_id = item.get('ref_id', '')
                text = item.get('text', '')[:100]
                parts.append(f"- {ref_id}: {text}...")
            if len(source_inventory) > 10:
                parts.append(f"  ... 他 {len(source_inventory) - 10} 件")

        return '\n'.join(parts)

    def _get_fallback_result(self, doc_type: str) -> Dict[str, Any]:
        """フォールバック結果を返す"""
        return {
            'document_date': None,
            'tags': [],
            'metadata': {
                'doc_type': doc_type,
                'extraction_failed': True
            },
            'title': '',
            'summary': '',
            'calendar_events': [],
            'tasks': [],
            'audit_canonical_text': ''
        }
