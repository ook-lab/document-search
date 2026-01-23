"""
Stage I: Synthesis (統合・要約)

抽出されたデータと元のテキストを統合し、人間と検索エンジンに最適な形に整形
- 役割: 全情報を統合し、要約・タグ生成・基準日付抽出
- モデル: 設定ファイルで指定（デフォルト: Gemini 2.5 Flash）

D_stage_a_classifier から完全移行
"""
import json
from typing import Dict, Any, Optional
from pathlib import Path
from string import Template
from loguru import logger

from shared.ai.llm_client.llm_client import LLMClient


class StageISynthesis:
    """Stage I: 統合・要約（設定ベース版）"""

    def __init__(self, llm_client: LLMClient):
        """
        Args:
            llm_client: LLMクライアント
        """
        self.llm = llm_client

    def process(
        self,
        combined_text: str,
        stageH_result: Dict[str, Any],
        prompt: str,
        model: str
    ) -> Dict[str, Any]:
        """
        統合・要約（設定ベース版）

        Args:
            combined_text: 統合テキスト
            stageH_result: Stage H の結果
            prompt: プロンプト（config/prompts/stage_i/*.md から読み込み）
            model: モデル名

        Returns:
            {
                'summary': str,
                'relevant_date': str,
                'tags': List[str]
            }
        """
        logger.info(f"[Stage I] 統合・要約開始... (model={model})")

        if not combined_text or not combined_text.strip():
            logger.warning("[Stage I] 入力テキストが空です")
            return self._get_fallback_result(stageH_result)

        try:
            # プロンプト構築
            full_prompt = self._build_prompt(
                prompt_template=prompt,
                combined_text=combined_text,
                stageH_result=stageH_result
            )

            # LLM呼び出し
            response = self.llm.call_model(
                tier="default",
                prompt=full_prompt,
                model_name=model
            )

            if not response.get("success"):
                logger.error(f"[Stage I エラー] LLM呼び出し失敗: {response.get('error')}")
                return self._get_fallback_result(stageH_result)

            # 結果をJSON形式で取得
            content = response.get("content", "")
            logger.info(f"[Stage I] ===== LLMレスポンス全文 ===== {content[:500]}")
            result = self._parse_result(content)

            # Stage H のタグとマージ
            stageH_tags = stageH_result.get('tags', [])
            stageI_tags = result.get('tags', [])
            merged_tags = list(set(stageH_tags + stageI_tags))  # 重複削除

            return {
                'title': result.get('title', ''),
                'summary': result.get('summary', ''),
                'relevant_date': result.get('relevant_date') or stageH_result.get('document_date'),
                'tags': merged_tags,
                'calendar_events': result.get('calendar_events', []),
                'tasks': result.get('tasks', [])
            }

        except Exception as e:
            logger.error(f"[Stage I エラー] 統合・要約失敗: {e}", exc_info=True)
            return self._get_fallback_result(stageH_result)

    def _build_prompt(
        self,
        prompt_template: str,
        combined_text: str,
        stageH_result: Dict[str, Any]
    ) -> str:
        """
        プロンプトを構築

        Args:
            prompt_template: プロンプトテンプレート
            combined_text: 統合テキスト
            stageH_result: Stage H の結果

        Returns:
            構築されたプロンプト
        """
        # Stage H の結果をJSON文字列化
        stageH_json = json.dumps(stageH_result, ensure_ascii=False, indent=2)

        # string.Templateを使用してテンプレート変数を置換（JSONの{}と競合しない）
        template = Template(prompt_template)
        prompt = template.substitute(
            combined_text=combined_text,
            stageH_result=stageH_json
        )

        return prompt

    def _sanitize_llm_json(self, text: str) -> str:
        """
        P1-2: LLM出力をJSONパース前にサニタイズ

        処理順序:
        1. None → "" / strip()
        2. コードフェンス除去 (```json ... ```)
        3. 先頭の json / JSON: ラベル除去
        4. 先頭の {{ → { に縮退
        5. 最初の { から最後の } までを切り出し
        """
        import re

        # 1. None対策とstrip
        if text is None:
            return ""
        text = text.strip()
        if not text:
            return ""

        # 2. コードフェンス除去
        # ```json や ```JSON など
        text = re.sub(r'^```(?:json|JSON)?\s*\n?', '', text)
        text = re.sub(r'\n?```\s*$', '', text)

        # 3. 先頭の json / JSON: ラベル除去
        text = re.sub(r'^(?:json|JSON)\s*[:：]?\s*', '', text.strip())

        # 4. 先頭の {{ → { に縮退（先頭のみ）
        if text.startswith('{{'):
            text = text[1:]

        # 5. 最初の { から最後の } までを切り出し
        first_brace = text.find('{')
        last_brace = text.rfind('}')
        if first_brace >= 0 and last_brace > first_brace:
            text = text[first_brace:last_brace + 1]

        return text

    def _parse_result(self, content: str, doc_id: str = None) -> Dict[str, Any]:
        """
        P1-2/P1-3: LLM出力から結果を抽出（サニタイズ + ログ強化）

        Args:
            content: LLMの出力
            doc_id: ドキュメントID（ログ用）

        Returns:
            抽出された結果
        """
        raw = content or ""
        raw_head = raw[:200] if raw else "(empty)"

        # P1-2: サニタイズ適用
        clean = self._sanitize_llm_json(raw)
        clean_head = clean[:200] if clean else "(empty)"

        # JSON形式で出力されている場合
        try:
            if clean:
                result = json.loads(clean)
                logger.info(f"[Stage I] JSON解析成功 (doc_id={doc_id})")
                return result
        except json.JSONDecodeError as e:
            logger.warning(f"[P1-2] JSON parse failed attempt=1 (doc_id={doc_id}): {e}")
            logger.warning(f"  raw_head: {raw_head}")
            logger.warning(f"  clean_head: {clean_head}")

        # P1-3: フォールバック（テキストから抽出）
        logger.info(f"[Stage I] JSONパース失敗 → テキスト抽出フォールバック (doc_id={doc_id})")
        return self._extract_from_text(raw)

    def _extract_from_text(self, content: str) -> Dict[str, Any]:
        """
        JSON形式でない場合、テキストから情報を抽出（フォールバック）
        """
        import re

        result = {
            'title': '',
            'summary': '',
            'tags': [],
            'relevant_date': None,
            'calendar_events': [],
            'tasks': []
        }

        if not content:
            return result

        # 要約を抽出（最初の段落または全体）
        lines = content.split('\n')
        summary_lines = []
        for line in lines:
            line = line.strip()
            if line and not line.startswith('#') and not line.startswith('-'):
                summary_lines.append(line)
                if len(summary_lines) >= 3:  # 最大3行
                    break

        result['summary'] = ' '.join(summary_lines) if summary_lines else content[:200]

        # タグを抽出（例: タグ: tag1, tag2, tag3）
        tags_match = re.search(r'タグ[:：]\s*(.+)', content, re.IGNORECASE)
        if tags_match:
            tags_str = tags_match.group(1)
            result['tags'] = [t.strip() for t in tags_str.split(',')]

        # 日付を抽出（YYYY-MM-DD形式）
        date_match = re.search(r'(\d{4}-\d{2}-\d{2})', content)
        if date_match:
            result['relevant_date'] = date_match.group(1)

        return result

    def _get_fallback_result(self, stageH_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        フォールバック結果を返す

        Args:
            stageH_result: Stage H の結果

        Returns:
            最小限の結果
        """
        return {
            'summary': '処理に失敗しました',
            'relevant_date': stageH_result.get('document_date'),
            'tags': stageH_result.get('tags', []),
            'calendar_events': [],
            'tasks': []
        }
