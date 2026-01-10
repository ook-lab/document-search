"""
Stage H: Structuring (構造化)

非構造化テキストから、意味のあるデータ(JSON)を抽出
- 役割: 指定スキーマに基づくJSON抽出、表データの正規化
- モデル: 設定ファイルで指定（Gemini, OpenAI等のLLM）
- 重要: doc_typeを判定するのではなく、渡されたdoc_typeのスキーマを使ってデータを抽出

F_stage_c_extractor から完全移行
"""
import re
import json
import json_repair
from typing import Dict, Any, Optional
from pathlib import Path
from string import Template
from loguru import logger
from datetime import datetime

from shared.ai.llm_client.llm_client import LLMClient


class StageHStructuring:
    """Stage H: 構造化（設定ベース版）"""

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
        stage_f_structure: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        構造化（設定ベース版）

        Args:
            file_name: ファイル名
            doc_type: ドキュメントタイプ
            workspace: ワークスペース
            combined_text: 統合テキスト（Stage E + G の結果）
            prompt: プロンプト（config/prompts/stage_h/*.md から読み込み）
            model: モデル名
            stage_f_structure: Stage F の構造化情報（tables, sections, visual_elements）

        Returns:
            {
                'document_date': str,
                'tags': List[str],
                'metadata': Dict[str, Any]
            }
        """
        logger.info(f"[Stage H] 構造化開始... (doc_type={doc_type}, model={model})")

        # Stage F の構造化情報がある場合はログ出力
        if stage_f_structure:
            logger.info(f"[Stage H] Stage F の構造化情報を受信: tables={len(stage_f_structure.get('tables', []))}, sections={len(stage_f_structure.get('sections', []))}")

        if not combined_text or not combined_text.strip():
            logger.warning("[Stage H] 入力テキストが空です")
            return self._get_fallback_result(doc_type)

        try:
            # プロンプト構築
            logger.info("[Stage H] プロンプト構築中...")
            full_prompt = self._build_prompt(
                prompt_template=prompt,
                file_name=file_name,
                doc_type=doc_type,
                workspace=workspace,
                combined_text=combined_text
            )
            logger.info(f"[Stage H] プロンプト構築完了 ({len(full_prompt)}文字)")

            # LLM呼び出し
            logger.info(f"[Stage H] LLM呼び出し中... (model={model})")
            response = self.llm.call_model(
                tier="default",
                prompt=full_prompt,
                model_name=model
            )
            logger.info(f"[Stage H] LLM応答受信: success={response.get('success')}")

            if not response.get("success"):
                logger.error(f"[Stage H エラー] LLM呼び出し失敗: {response.get('error')}")
                return self._get_fallback_result(doc_type)

            # JSON抽出（リトライ機能付き）
            content = response.get("content", "")
            logger.info(f"[Stage H] ===== LLMレスポンス全文 =====\n{content}\n[Stage H] ===== レスポンス終了 =====")
            result = self._extract_json_with_retry(content, model=model, max_retries=2)

            # Stage F の構造化情報をマージ
            if stage_f_structure:
                result = self._merge_stage_f_structure(result, stage_f_structure)

            # 結果の整形
            return {
                'document_date': result.get('document_date'),
                'tags': result.get('tags', []),
                'metadata': result.get('metadata', {})
            }

        except Exception as e:
            logger.error(f"[Stage H エラー] 構造化失敗: {e}", exc_info=True)
            return self._get_fallback_result(doc_type)

    def _build_prompt(
        self,
        prompt_template: str,
        file_name: str,
        doc_type: str,
        workspace: str,
        combined_text: str
    ) -> str:
        """
        プロンプトを構築

        Args:
            prompt_template: プロンプトテンプレート
            file_name: ファイル名
            doc_type: ドキュメントタイプ
            workspace: ワークスペース
            combined_text: 統合テキスト

        Returns:
            構築されたプロンプト
        """
        # string.Templateを使用してテンプレート変数を置換（JSONの{}と競合しない）
        template = Template(prompt_template)
        prompt = template.substitute(
            file_name=file_name,
            doc_type=doc_type,
            workspace=workspace,
            combined_text=combined_text,
            current_date=datetime.now().strftime("%Y-%m-%d")
        )

        return prompt

    def _extract_json_with_retry(
        self,
        content: str,
        model: str,
        max_retries: int = 2
    ) -> Dict[str, Any]:
        """
        JSON抽出（リトライ機能付き）

        Args:
            content: LLMの出力
            model: モデル名
            max_retries: 最大リトライ回数

        Returns:
            抽出されたJSON
        """
        for attempt in range(max_retries + 1):
            try:
                result = self._extract_json(content)
                logger.debug(f"[Stage H] JSON抽出成功 (試行{attempt + 1}/{max_retries + 1})")
                return result

            except Exception as e:
                if attempt < max_retries:
                    logger.warning(f"[Stage H] JSON抽出失敗 (試行{attempt + 1}/{max_retries + 1}): {e}")
                    # リトライ: LLMにJSON修正を依頼
                    content = self._retry_json_extraction(content, str(e), model)
                else:
                    logger.error(f"[Stage H] JSON抽出失敗（最終試行）: {e}")
                    raise

        # ここには到達しないはずだが、念のため
        return {}

    def _extract_json(self, content: str) -> Dict[str, Any]:
        """
        コンテンツからJSONを抽出

        Args:
            content: LLMの出力

        Returns:
            抽出されたJSON

        Raises:
            Exception: JSON抽出に失敗した場合
        """
        # 複数のパターンでJSONブロックを探す
        patterns = [
            r'```json\s*(.*?)```',  # ```json ... ``` (改行を柔軟に)
            r'```\s*(.*?)```',      # ``` ... ```
            r'\{[\s\S]*?\}',        # { ... } (非貪欲)
        ]

        json_str = None
        for pattern in patterns:
            match = re.search(pattern, content, re.DOTALL)
            if match:
                json_str = match.group(1) if match.lastindex else match.group(0)
                json_str = json_str.strip()
                # { で始まるか確認
                if json_str.startswith('{'):
                    break
                else:
                    json_str = None

        if not json_str:
            # パターンマッチしない場合、{ ... } を直接探す
            match = re.search(r'\{[\s\S]*\}', content, re.DOTALL)
            if match:
                json_str = match.group(0).strip()
            else:
                json_str = content.strip()

        # JSONパース
        try:
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            # json_repair で修復を試みる
            logger.error(f"[Stage H] JSON解析失敗: {e}")
            logger.error(f"[Stage H] 抽出されたJSON（最初の500文字）:\n{json_str[:500]}")
            try:
                return json_repair.loads(json_str)
            except Exception as repair_error:
                logger.error(f"[Stage H] JSON修復も失敗: {repair_error}")
                logger.error(f"[Stage H] 修復失敗したJSON全文:\n{json_str}")
                raise

    def _retry_json_extraction(
        self,
        failed_content: str,
        error_message: str,
        model: str
    ) -> str:
        """
        JSON抽出失敗時、LLMにJSON修正を依頼

        Args:
            failed_content: 失敗したコンテンツ
            error_message: エラーメッセージ
            model: モデル名

        Returns:
            修正されたJSON文字列
        """
        prompt = f"""以下のJSONにエラーがあります。修正してください。

エラー: {error_message}

元のJSON:
```
{failed_content}
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
                logger.error(f"[Stage H] JSON修正失敗: {response.get('error')}")
                return failed_content

        except Exception as e:
            logger.error(f"[Stage H] JSON修正エラー: {e}")
            return failed_content

    def _merge_stage_f_structure(self, result: Dict[str, Any], stage_f_structure: Dict[str, Any]) -> Dict[str, Any]:
        """
        Stage F の構造化情報を Stage H の結果にマージ

        Args:
            result: Stage H の抽出結果
            stage_f_structure: Stage F の構造化情報

        Returns:
            マージ済みの結果
        """
        metadata = result.get('metadata', {})

        # Stage F の tables を優先的に使用（Stage H で tables が抽出されていない場合、または少ない場合）
        stage_f_tables = stage_f_structure.get('tables', [])
        stage_h_tables = metadata.get('structured_tables', [])

        if stage_f_tables and len(stage_f_tables) > len(stage_h_tables):
            logger.info(f"[Stage H] Stage F の tables を優先使用: {len(stage_f_tables)}個（Stage H: {len(stage_h_tables)}個）")

            # Stage F の tables を Stage H の形式に変換
            converted_tables = []
            for i, table in enumerate(stage_f_tables):
                rows = table.get('rows', [])
                if rows:
                    # 最初の行をヘッダーとして扱う
                    headers = rows[0] if rows else []
                    data_rows = rows[1:] if len(rows) > 1 else []

                    # 辞書形式に変換
                    converted_rows = []
                    for row in data_rows:
                        row_dict = {}
                        for j, cell in enumerate(row):
                            header = headers[j] if j < len(headers) else f"列{j+1}"
                            row_dict[header] = cell
                        converted_rows.append(row_dict)

                    converted_tables.append({
                        'table_title': table.get('caption', f'表{i+1}'),
                        'table_type': 'ocr_extracted',
                        'headers': headers,
                        'rows': converted_rows
                    })

            metadata['structured_tables'] = converted_tables

        # Stage F の sections を text_blocks に統合（text_blocks が少ない場合）
        stage_f_sections = stage_f_structure.get('sections', [])
        stage_h_text_blocks = metadata.get('text_blocks', [])

        if stage_f_sections and len(stage_h_text_blocks) < len(stage_f_sections):
            logger.info(f"[Stage H] Stage F の sections を text_blocks に変換: {len(stage_f_sections)}個")

            converted_blocks = []
            current_heading = None
            current_content_parts = []

            for section in stage_f_sections:
                section_type = section.get('type', '')
                content = section.get('content', '')

                if section_type == 'heading':
                    # 前のブロックを保存
                    if current_heading and current_content_parts:
                        converted_blocks.append({
                            'title': current_heading,
                            'content': '\n'.join(current_content_parts)
                        })
                    # 新しいヘッディング開始
                    current_heading = content
                    current_content_parts = []
                else:
                    # paragraph, list など
                    if content:
                        # contentがlistの場合は改行で結合して文字列化
                        if isinstance(content, list):
                            current_content_parts.append('\n'.join(str(item) for item in content))
                        else:
                            current_content_parts.append(str(content))

            # 最後のブロックを保存
            if current_heading and current_content_parts:
                converted_blocks.append({
                    'title': current_heading,
                    'content': '\n'.join(current_content_parts)
                })

            # Stage H の text_blocks より多ければ置き換え
            if len(converted_blocks) > len(stage_h_text_blocks):
                metadata['text_blocks'] = converted_blocks

        # Stage F の visual_elements からデッドライン情報を抽出
        visual_elements = stage_f_structure.get('visual_elements', {})
        if visual_elements:
            deadline_info = visual_elements.get('deadline_info')
            if deadline_info and not result.get('document_date'):
                logger.info(f"[Stage H] Stage F の deadline_info を document_date として使用: {deadline_info}")
                result['document_date'] = deadline_info

            # 強調されたテキストをタグに追加
            emphasized_text = visual_elements.get('emphasized_text', [])
            if emphasized_text:
                existing_tags = result.get('tags', [])
                for text in emphasized_text:
                    if text and text not in existing_tags:
                        existing_tags.append(text)
                result['tags'] = existing_tags
                logger.info(f"[Stage H] Stage F の emphasized_text をタグに追加: {emphasized_text}")

        result['metadata'] = metadata
        return result

    def _get_fallback_result(self, doc_type: str) -> Dict[str, Any]:
        """
        フォールバック結果を返す

        Args:
            doc_type: ドキュメントタイプ

        Returns:
            最小限の結果
        """
        return {
            'document_date': None,
            'tags': [],
            'metadata': {
                'doc_type': doc_type,
                'extraction_failed': True
            }
        }
