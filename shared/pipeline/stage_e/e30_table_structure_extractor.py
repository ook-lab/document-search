"""
E-30: Table Structure Extractor（表用 - Gemini 2.5 Flash）

表画像から構造を正確に抽出し、Markdown形式に変換する。

目的:
1. 結合セルの正確な把握
2. 行列の整合性維持
3. 空セルの省略防止
"""

from pathlib import Path
from typing import Dict, Any, List, Optional
from loguru import logger
import json

try:
    import google.generativeai as genai
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False
    logger.warning("[E-30] google-generativeai がインストールされていません")


class E30TableStructureExtractor:
    """E-30: Table Structure Extractor（表用）"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: str = "gemini-2.5-flash"  # Gemini 2.5 Flash
    ):
        """
        Table Structure Extractor 初期化

        Args:
            api_key: Google AI API Key
            model_name: 使用するモデル名
        """
        self.model_name = model_name
        self.api_key = api_key

        if not GENAI_AVAILABLE:
            logger.error("[E-30] google-generativeai が必要です")
            return

        if api_key:
            genai.configure(api_key=api_key)
            self.model = genai.GenerativeModel(model_name)
        else:
            logger.warning("[E-30] API key が設定されていません")
            self.model = None

    def extract(
        self,
        image_path: Path,
        cell_map: Optional[List[Dict]] = None,
        custom_prompt: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        表画像から構造を抽出

        Args:
            image_path: 表画像ファイルパス
            cell_map: Stage D の cell_map（座標ヒント）
            custom_prompt: カスタムプロンプト（オプション）

        Returns:
            {
                'success': bool,
                'table_markdown': str,      # Markdown形式の表
                'table_json': dict,         # JSON形式の表
                'raw_response': str,        # Geminiの生レスポンス
                'model_used': str,          # 使用したモデル名
                'tokens_used': int          # 使用トークン数（概算）
            }
        """
        if not GENAI_AVAILABLE or not self.model:
            logger.error("[E-30] Gemini API が利用できません")
            return self._error_result("Gemini API not available")

        logger.info(f"[E-30] 表構造抽出開始: {image_path.name}")

        try:
            # 画像を読み込み
            with open(image_path, 'rb') as f:
                image_data = f.read()

            # プロンプトを構築
            prompt = self._build_prompt(cell_map, custom_prompt)

            logger.info(f"[E-30] モデル: {self.model_name}")
            logger.info(f"[E-30] プロンプト長: {len(prompt)}文字")
            if cell_map:
                logger.info(f"[E-30] セル数: {len(cell_map)}個")

            # Gemini に送信
            response = self.model.generate_content([
                prompt,
                {
                    'mime_type': 'image/png',
                    'data': image_data
                }
            ])

            # レスポンスをパース
            raw_text = response.text
            logger.info(f"[E-30] レスポンス長: {len(raw_text)}文字")

            # Markdown と JSON を抽出
            table_markdown, table_json = self._parse_table_response(raw_text)

            # トークン数を概算
            tokens_used = (len(prompt) + len(raw_text)) // 4

            logger.info(f"[E-30] 抽出完了")
            logger.info(f"  ├─ モデル: {self.model_name}")
            logger.info(f"  └─ トークン: 約{tokens_used}")

            return {
                'success': True,
                'table_markdown': table_markdown,
                'table_json': table_json,
                'raw_response': raw_text,
                'model_used': self.model_name,
                'tokens_used': tokens_used
            }

        except Exception as e:
            logger.error(f"[E-30] 抽出エラー: {e}", exc_info=True)
            return self._error_result(str(e))

    def _build_prompt(
        self,
        cell_map: Optional[List[Dict]],
        custom_prompt: Optional[str]
    ) -> str:
        """
        プロンプトを構築

        Args:
            cell_map: セル座標マップ
            custom_prompt: カスタムプロンプト

        Returns:
            プロンプト文字列
        """
        if custom_prompt:
            return custom_prompt

        prompt_parts = []

        # ベースプロンプト
        prompt_parts.append("""
あなたは表画像を正確にMarkdown形式に変換する専門家です。

添付された表画像を以下の要件に従ってMarkdown形式に変換してください：

**重要な要件:**
1. 結合セルを正確に把握し、適切に表現すること
2. 空セルを省略せず、必ず `(空)` または空白として表現すること
3. 行数・列数を正確に維持すること
4. セル内の改行は `<br>` で表現すること

**出力形式:**
```markdown
| ヘッダー1 | ヘッダー2 | ヘッダー3 |
|----------|----------|----------|
| セル1-1   | セル1-2   | セル1-3   |
| セル2-1   | セル2-2   | (空)     |
```

**注意:**
- ヘッダー行がない場合でも、1行目をヘッダーとして扱うこと
- 数値は右寄せ、テキストは左寄せが望ましい
- 縦書き文字がある場合は、横書きに変換すること
""")

        # セル座標ヒントを追加
        if cell_map:
            grid_info = self._build_grid_info(cell_map)
            prompt_parts.append("\n---\n")
            prompt_parts.append("**座標ヒント（参考情報）:**\n")
            prompt_parts.append(f"この表は約 {grid_info['rows']}行 × {grid_info['cols']}列 で構成されています。\n")
            prompt_parts.append("各セルの座標（正規化座標 0.0-1.0）:\n")

            # 最初の10セルのみ表示（プロンプトが長くなりすぎないように）
            for cell in cell_map[:10]:
                cell_id = cell.get('cell_id', 'N/A')
                bbox = cell.get('bbox_normalized', cell.get('bbox', []))
                prompt_parts.append(
                    f"- {cell_id}: x={bbox[0]:.2f}-{bbox[2]:.2f}, y={bbox[1]:.2f}-{bbox[3]:.2f}\n"
                )

            if len(cell_map) > 10:
                prompt_parts.append(f"... (他 {len(cell_map) - 10}セル)\n")

        return "".join(prompt_parts)

    def _build_grid_info(self, cell_map: List[Dict]) -> Dict[str, int]:
        """
        セルマップからグリッド情報を抽出

        Args:
            cell_map: セル座標マップ

        Returns:
            {'rows': int, 'cols': int}
        """
        max_row = 0
        max_col = 0

        for cell in cell_map:
            row = cell.get('row', 0)
            col = cell.get('col', 0)
            max_row = max(max_row, row)
            max_col = max(max_col, col)

        return {'rows': max_row, 'cols': max_col}

    def _parse_table_response(self, raw_text: str) -> tuple[str, Dict[str, Any]]:
        """
        レスポンスからMarkdownとJSONを抽出

        Args:
            raw_text: Geminiの生レスポンス

        Returns:
            (markdown_text, json_data)
        """
        markdown_text = ""
        json_data = {}

        try:
            # Markdown部分を抽出
            if '```markdown' in raw_text:
                start = raw_text.find('```markdown') + 11
                end = raw_text.find('```', start)
                markdown_text = raw_text[start:end].strip()
            elif '```' in raw_text:
                start = raw_text.find('```') + 3
                end = raw_text.find('```', start)
                markdown_text = raw_text[start:end].strip()
            else:
                # Markdownマーカーがない場合は全体をMarkdownとして扱う
                markdown_text = raw_text.strip()

            # JSON形式への変換（簡易版）
            # TODO: Markdownから構造化JSONへのパーサーを実装
            json_data = {
                'format': 'markdown',
                'content': markdown_text
            }

        except Exception as e:
            logger.warning(f"[E-30] レスポンスパースエラー: {e}")

        return markdown_text, json_data

    def _error_result(self, error_message: str) -> Dict[str, Any]:
        """エラー結果を返す"""
        return {
            'success': False,
            'error': error_message,
            'table_markdown': '',
            'table_json': {},
            'raw_response': '',
            'model_used': self.model_name,
            'tokens_used': 0
        }
