"""
G-12: Table AI Processor（表のAI処理）

G-11で生成された最高レベルの表データ（headers/rows）から、
Gemini 2.5 Flash-lite で表の内容を解析・要約する。

目的:
- G-11の表データからAI解析
- 表の要約、重要情報の抽出
- 全文ログ出力（AI品質評価用）
"""

from typing import Dict, Any, List, Optional
from loguru import logger
import json

try:
    import google.generativeai as genai
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False
    logger.warning("[G-12] google-generativeai がインストールされていません")


class G12TableAIProcessor:
    """G-12: Table AI Processor（表のAI処理）"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: str = "gemini-2.5-flash-lite"
    ):
        """
        Table AI Processor 初期化

        Args:
            api_key: Google AI API Key
            model_name: 使用するモデル名（gemini-2.5-flash-lite）
        """
        self.model_name = model_name
        self.api_key = api_key

        if not GENAI_AVAILABLE:
            logger.error("[G-12] google-generativeai が必要です")
            self.model = None
            return

        if api_key:
            genai.configure(api_key=api_key)
            self.model = genai.GenerativeModel(model_name)
            logger.info(f"[G-12] モデル初期化: {model_name}")
        else:
            logger.warning("[G-12] API key が設定されていません")
            self.model = None

    def process(
        self,
        structured_tables: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        表データを解析・要約

        Args:
            structured_tables: G-11 で生成された表データ
                [{'headers': list, 'rows': list, 'table_id': str}, ...]

        Returns:
            {
                'success': bool,
                'table_analyses': list,  # 各表の解析結果
                'tokens_used': int
            }
        """
        logger.info("[G-12] AI処理開始")

        if not GENAI_AVAILABLE or not self.model:
            logger.error("[G-12] Gemini API が利用できません")
            return self._error_result("Gemini API not available")

        if not structured_tables:
            logger.info("[G-12] 処理する表がありません")
            return {
                'success': True,
                'table_analyses': [],
                'tokens_used': 0
            }

        try:
            table_analyses = []
            total_tokens = 0

            for i, table in enumerate(structured_tables, 1):
                logger.info(f"[G-12] 表 {i}/{len(structured_tables)} 処理中")

                # 表をテキスト化
                table_text = self._table_to_text(table)

                # プロンプトを構築
                prompt = self._build_prompt(table_text, table.get('table_id', f'Table_{i}'))

                logger.info(f"[G-12] モデル: {self.model_name}")
                logger.info(f"[G-12] 入力テキスト長: {len(table_text)}文字")
                logger.info("")
                logger.info(f"[G-12] ========== 入力テキスト（G-11の出力 - Table {i}） ==========")
                logger.info(table_text)
                logger.info("=" * 60)

                # Gemini に送信
                response = self.model.generate_content(prompt)
                raw_text = response.text

                logger.info("")
                logger.info(f"[G-12] ========== AI応答（生 - Table {i}） ==========")
                logger.info(raw_text)
                logger.info("=" * 60)

                # JSONをパース
                analysis = self._parse_response(raw_text)
                analysis['table_id'] = table.get('table_id', f'Table_{i}')

                table_analyses.append(analysis)

                # トークン数を概算
                tokens = (len(prompt) + len(raw_text)) // 4
                total_tokens += tokens

                logger.info("")
                logger.info(f"[G-12] ========== 解析結果（Table {i}） ==========")
                logger.info(json.dumps(analysis, ensure_ascii=False, indent=2))
                logger.info("=" * 60)

            logger.info(f"[G-12] AI処理完了: 合計トークン約{total_tokens}")

            return {
                'success': True,
                'table_analyses': table_analyses,
                'tokens_used': total_tokens
            }

        except Exception as e:
            logger.error(f"[G-12] AI処理エラー: {e}", exc_info=True)
            return self._error_result(str(e))

    def _table_to_text(self, table: Dict[str, Any]) -> str:
        """
        表データをテキスト形式に変換

        Args:
            table: {'headers': list, 'rows': list}

        Returns:
            テキスト形式の表
        """
        headers = table.get('headers', [])
        rows = table.get('rows', [])

        lines = []

        # ヘッダー行
        if headers:
            lines.append(" | ".join(str(h) for h in headers))
            lines.append("-" * 60)

        # データ行
        for row in rows:
            if isinstance(row, list):
                lines.append(" | ".join(str(cell) for cell in row))
            else:
                lines.append(str(row))

        return "\n".join(lines)

    def _build_prompt(self, table_text: str, table_id: str) -> str:
        """
        プロンプトを構築

        Args:
            table_text: テキスト形式の表
            table_id: 表のID

        Returns:
            プロンプト文字列
        """
        return f"""
あなたは表データを深く理解して構造化する専門家です。

以下の表（{table_id}）の内容を分析し、**表の意味を理解して適切に構造化**してください。

[表データ]
{table_text}

**タスク：**
1. この表が何を表しているか判定（時間割、成績表、予定表、名簿など）
2. 表の構造を理解（どの列・行が何を表すか）
3. 意味のある単位で再構造化

**構造化の例：**

【時間割表の場合】
- クラスごと、曜日ごと、時限ごとに整理
- 複数クラスが横に並んでいる場合は分離

【成績表の場合】
- 生徒ごと、科目ごとに整理

【予定表の場合】
- 日付ごと、時間ごとに整理

**出力形式（柔軟に対応）：**
```json
{{
  "table_type": "timetable",  // 表の種類
  "structure": {{
    // 時間割の場合の例
    "classes": [
      {{
        "class_name": "5A",
        "schedule": {{
          "月曜": ["朝", "国語", "算数", "理科", "社会", "体育"],
          "火曜": ["朝", "算数", "国語", "社会", "理科", "音楽"],
          ...
        }}
      }},
      {{
        "class_name": "5B",
        "schedule": {{
          "月曜": ["朝", "算数", "国語", "社会", "理科", "図工"],
          ...
        }}
      }}
    ]
  }},
  "metadata": {{
    "total_classes": 2,
    "days_of_week": ["月曜", "火曜", "水曜", "木曜", "金曜"],
    "periods_per_day": 6
  }}
}}
```

**重要な指示:**
- 表の構造を深く理解してください
- 左右に複数のセクションがある場合は分離してください（例：5Aと5Bの時間割）
- 表の種類に応じて柔軟に出力形式を変えてください
- 表に記載されていない情報は作らないこと
- わからない場合は素直に "unknown" と記載すること
"""

    def _parse_response(self, raw_text: str) -> Dict[str, Any]:
        """
        レスポンスをパースしてJSONを抽出

        Args:
            raw_text: Geminiの生レスポンス

        Returns:
            抽出されたJSON
        """
        try:
            # ```json ... ``` で囲まれている場合
            if '```json' in raw_text:
                start = raw_text.find('```json') + 7
                end = raw_text.find('```', start)
                json_str = raw_text[start:end].strip()
                return json.loads(json_str)
            # ``` ... ``` で囲まれている場合
            elif '```' in raw_text:
                start = raw_text.find('```') + 3
                end = raw_text.find('```', start)
                json_str = raw_text[start:end].strip()
                return json.loads(json_str)
            # JSON部分のみの場合
            else:
                return json.loads(raw_text)
        except Exception as e:
            logger.warning(f"[G-12] JSONパースエラー: {e}")
            # パースできない場合は空の結果を返す
            return {
                'table_type': 'unknown',
                'summary': '',
                'key_points': [],
                'notes': []
            }

    def _error_result(self, error_message: str) -> Dict[str, Any]:
        """エラー結果を返す"""
        return {
            'success': False,
            'error': error_message,
            'table_analyses': [],
            'tokens_used': 0
        }
