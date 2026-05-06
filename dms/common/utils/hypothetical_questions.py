"""
Hypothetical Questions（仮想質問生成）

文書保存時に「ユーザーが聞きそうな質問」を事前生成し、検索精度を向上させます。
"""
from typing import List, Dict, Any, Optional
from loguru import logger
import json


class HypotheticalQuestionGenerator:
    """
    LLMを使って文書チャンクから仮想質問を生成

    落とし穴対策:
    - AIが「嘘の質問」を作らないように強い制約を設定
    - 文書内に書かれている事実のみに基づいて質問を生成
    - 生成された質問は検索用としてのみ使用（回答ソースにしない）
    """

    def __init__(self, llm_client):
        """
        Args:
            llm_client: LLMClient インスタンス
        """
        self.llm_client = llm_client

    def generate_questions(
        self,
        chunk_text: str,
        num_questions: int = 3,
        document_metadata: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        チャンクテキストから仮想質問を生成

        Args:
            chunk_text: チャンクのテキスト
            num_questions: 生成する質問数（3-5推奨）
            document_metadata: 文書のメタデータ（コンテキスト用）

        Returns:
            質問のリスト:
            [
                {
                    "question_text": str
                },
                ...
            ]
        """
        if not chunk_text or len(chunk_text.strip()) < 50:
            logger.warning("チャンクが短すぎます。質問生成をスキップ。")
            return []

        prompt = self._build_question_generation_prompt(
            chunk_text=chunk_text,
            num_questions=num_questions,
            document_metadata=document_metadata
        )

        try:
            # LLMで質問を生成
            response = self.llm_client.call_model(
                tier="extraction",  # 軽量なモデルで十分
                prompt=prompt
            )

            if not response.get("success"):
                logger.error(f"[HypotheticalQ] LLM呼び出し失敗: {response.get('error')}")
                return []

            content = response.get("content", "")

            # JSONを抽出
            questions = self._extract_questions_from_response(content)

            if questions:
                logger.info(f"[HypotheticalQ] 質問生成成功: {len(questions)}件")
                return questions
            else:
                logger.warning("[HypotheticalQ] 質問抽出失敗")
                return []

        except Exception as e:
            logger.error(f"[HypotheticalQ] エラー: {e}", exc_info=True)
            return []

    def _build_question_generation_prompt(
        self,
        chunk_text: str,
        num_questions: int,
        document_metadata: Optional[Dict[str, Any]]
    ) -> str:
        """
        質問生成用のプロンプトを構築

        Args:
            chunk_text: チャンクのテキスト
            num_questions: 生成する質問数
            document_metadata: 文書のメタデータ

        Returns:
            LLMに送信するプロンプト
        """
        metadata_context = ""
        if document_metadata:
            doc_type = document_metadata.get("doc_type", "不明")
            file_name = document_metadata.get("file_name", "不明")
            metadata_context = f"\n文書タイプ: {doc_type}\nファイル名: {file_name}\n"

        prompt = f"""あなたは質問生成の専門家です。以下の文書の一部を読んで、ユーザーが聞きそうな質問を生成してください。

# 文書の一部
{metadata_context}
---
{chunk_text}
---

# タスク
上記の文書に対して、ユーザーが聞きそうな質問を{num_questions}個生成してください。

# ★★★ 重要な制約（落とし穴対策）★★★

1. **文書内に明確に書かれている事実のみに基づいて質問を作成すること**
   - 文書に書かれていない情報について質問を作らないでください
   - 推測や想像で質問を作らないでください
   - 「〇〇について書いてありますか？」のような質問で、〇〇が実際に書かれていない場合は作成しないでください

2. **具体的で検索しやすい質問を作成すること**
   - 曖昧な質問ではなく、具体的なキーワードを含む質問を作成
   - 例: 「予定は？」❌ → 「12月4日の予定は？」✅

3. **自然な日本語で質問を作成すること**
   - ユーザーが実際に入力しそうな自然な表現を使う
   - 例: 「2024年12月4日に予定されているイベントの詳細情報」❌
   - 例: 「12月4日の予定を教えて」✅

4. **質問の多様性を確保すること**
   - 同じような質問を繰り返さない
   - 異なる観点から質問を作成（日付、人名、場所、内容など）

# 出力形式

以下のJSON形式で出力してください（JSON以外の説明は不要）:

```json
[
  {{
    "question_text": "質問1のテキスト"
  }},
  {{
    "question_text": "質問2のテキスト"
  }},
  {{
    "question_text": "質問3のテキスト"
  }}
]
```

# 例

**文書の一部**:
「2024年12月4日（水）14:00-16:00 社内MTG 議題:Q4振り返り 参加者:営業部全員 場所:会議室A」

**良い質問の例（✅）**:
1. 「12月4日の社内MTGの議題は？」
2. 「Q4振り返りのMTGはいつ？」
3. 「営業部のMTGの場所は？」

**悪い質問の例（❌）**:
1. 「Q4の売上目標は？」← 文書に書かれていない❌
2. 「MTGの結果は？」← まだ開催されていない（文書は予定表）❌
3. 「会議室Aの収容人数は？」← 文書のテーマから逸れている❌

それでは、上記の文書に対する質問を{num_questions}個生成してください:
"""

        return prompt

    def _extract_questions_from_response(self, content: str) -> List[Dict[str, Any]]:
        """
        LLMのレスポンスから質問を抽出

        Args:
            content: LLMのレスポンス

        Returns:
            質問のリスト
        """
        import json_repair

        try:
            # マークダウンコードブロックを除去
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                parts = content.split("```")
                if len(parts) >= 3:
                    content = parts[1]

            # JSON部分のみを抽出
            start_idx = content.find('[')
            end_idx = content.rfind(']')

            if start_idx == -1 or end_idx == -1:
                logger.warning("[HypotheticalQ] JSON配列が見つかりません")
                return []

            json_str = content[start_idx:end_idx+1]

            # json_repair を使用して構文エラーを自動修復
            questions = json_repair.loads(json_str)

            if not isinstance(questions, list):
                logger.warning("[HypotheticalQ] JSONが配列ではありません")
                return []

            # バリデーションとクリーニング
            validated_questions = []
            for q in questions:
                if not isinstance(q, dict):
                    continue

                question_text = q.get("question_text", "").strip()

                # 質問テキストが空でないか、または短すぎないか
                if not question_text or len(question_text) < 5:
                    continue

                validated_questions.append({
                    "question_text": question_text
                })

            return validated_questions

        except Exception as e:
            logger.error(f"[HypotheticalQ] JSON抽出エラー: {e}")
            return []
