"""
E-7L: LLM差分抽出（Merge Detection）

【ダイエット方式】全トークンの再出力を禁止し、差分のみを返させる
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
入力:
  - vision_tokens: E6出力（座標付きトークン）
  - image_path: ページ画像パス（Vision用）

出力:
  - merge_instructions: [{"ids": ["t0","t1"], "text": "接着後テキスト"}, ...]

【設計思想】
- AIの仕事は「どこを接着するか」の判断のみ
- 実際の結合処理はE-7P（Python）が行う
- 入力はid+text+座標概略に圧縮（bboxの詳細は不要、画像を見るため）
- 出力は差分JSON限定（不変トークンの出力は厳禁）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import json
import base64
from pathlib import Path
from typing import Dict, Any, List, Optional
from loguru import logger


class E7LMergeDetector:
    """E-7L: LLM差分抽出 - 接着候補の検出のみ"""

    MODEL = "gemini-2.5-flash"

    def __init__(self, llm_client):
        self.llm_client = llm_client

    def detect(
        self,
        vision_tokens: List[Dict[str, Any]],
        image_path: Optional[str] = None
    ) -> List[Dict]:
        """
        トークン列から接着候補をLLMに検出させる（差分のみ）

        Args:
            vision_tokens: E6出力（座標付きトークン）
            image_path: ページ画像パス

        Returns:
            merge_instructions: [{"ids": ["t0","t1"], "text": "..."}, ...]
        """
        if not vision_tokens:
            return []

        logger.info(f"[E-7L] LLM差分抽出開始: {len(vision_tokens)}トークン")

        # 入力データを圧縮（id + text + 座標概略のみ、bbox詳細は不要）
        compact_input = self._build_compact_input(vision_tokens)

        # Vision有無で分岐
        if image_path and Path(image_path).exists():
            merges = self._call_vision_merge(compact_input, image_path, len(vision_tokens))
        else:
            logger.warning("[E-7L] 画像なし → テキストのみで検出")
            merges = self._call_text_merge(compact_input, len(vision_tokens))

        logger.info(f"[E-7L] LLM検出結果: {len(merges)}グループ")
        for i, m in enumerate(merges[:20]):
            ids = m.get('ids', [])
            text = m.get('text', '')
            logger.info(f"[E-7L]   [{i}] ids={ids} -> '{text}'")
        if len(merges) > 20:
            logger.info(f"[E-7L]   ... 他{len(merges)-20}件")

        return merges

    def _build_compact_input(self, vision_tokens: List[Dict]) -> str:
        """
        LLMへの入力を圧縮する

        bbox詳細は削除し、左上座標の概略だけ残す。
        AIは画像を直接見るため、正確なbboxは不要。
        """
        lines = []
        for i, t in enumerate(vision_tokens):
            tid = f"t{i}"
            text = t.get("text", "")
            bbox = t.get("bbox", [0, 0, 0, 0])
            # 左上座標を整数で（位置の手がかり）
            x = int(bbox[0])
            y = int(bbox[1])
            lines.append(f"{tid}\t{text}\t@{x},{y}")

        return "\n".join(lines)

    def _call_vision_merge(
        self,
        compact_input: str,
        image_path: str,
        token_count: int
    ) -> List[Dict]:
        """Vision付きで差分を検出"""
        try:
            with open(image_path, "rb") as f:
                image_base64 = base64.b64encode(f.read()).decode("utf-8")
        except Exception as e:
            logger.error(f"[E-7L] 画像読み込み失敗: {e}")
            return []

        prompt = self._build_prompt(token_count)
        full_prompt = prompt + "\n\nTokens:\n" + compact_input

        try:
            response = self.llm_client.generate_with_vision(
                prompt=full_prompt,
                image_path=image_path,
                model=self.MODEL,
                max_tokens=8192,
                temperature=0.0,
                response_format="json"
            )

            return self._parse_response(response)

        except Exception as e:
            logger.warning(f"[E-7L] Vision検出エラー: {e}")
            return []

    def _call_text_merge(
        self,
        compact_input: str,
        token_count: int
    ) -> List[Dict]:
        """テキストのみで差分を検出（フォールバック）"""
        prompt = self._build_prompt(token_count)
        full_prompt = prompt + "\n\nTokens:\n" + compact_input

        try:
            response = self.llm_client.generate(
                prompt=full_prompt,
                model=self.MODEL,
                max_tokens=8192,
                temperature=0.0
            )

            return self._parse_response(response)

        except Exception as e:
            logger.warning(f"[E-7L] テキスト検出エラー: {e}")
            return []

    def _build_prompt(self, token_count: int) -> str:
        """
        ダイエット版プロンプトを構築

        重要: 「差分のみ出力」を繰り返し強調する
        """
        return f"""You are a minimal-output OCR merge detector for Japanese documents.

INPUT: {token_count} OCR tokens (id, text, approximate position).
IMAGE: The original document page.

TASK: Find tokens that should be MERGED into one word/phrase.

OUTPUT RULES (CRITICAL):
- Output ONLY groups that need merging.
- NEVER list tokens that are already correct and standalone.
- NEVER repeat or echo the input token list.
- Keep your response SHORT. Typical output: 20-80 merge groups.
- If no merges needed, return {{"merges": []}}

MERGE RULES:
1. Merge fragments of one word/name/phrase into ONE group with ALL fragments.
   Example: "都","立","大","学" → one group with all 4 IDs.
2. If OCR misread characters, provide CORRECTED text.
3. Never merge across different table columns or rows.
4. Each token ID may appear in only ONE group.
5. Prefer LONGER groups (capture full words, not partial).

OUTPUT FORMAT (strict JSON, nothing else):
{{"merges": [
  {{"ids": ["t10","t11","t12","t13"], "text": "都立大学"}},
  {{"ids": ["t50","t51"], "text": "テスト"}}
]}}"""

    def _parse_response(self, response: str) -> List[Dict]:
        """LLM応答からmerge指示を抽出"""
        if not response:
            return []

        try:
            import json_repair
            parsed = json_repair.loads(response)
            merges = parsed.get("merges", [])

            # バリデーション: 各エントリに ids が存在するか
            valid_merges = []
            for m in merges:
                if isinstance(m, dict) and m.get("ids") and len(m["ids"]) >= 2:
                    valid_merges.append({
                        "ids": m["ids"],
                        "text": m.get("text", "")
                    })

            return valid_merges

        except Exception as e:
            logger.warning(f"[E-7L] JSON解析エラー: {e}")
            return []
