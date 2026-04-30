from __future__ import annotations

import os
from dataclasses import dataclass

from shared.kakeibo.merchant_classifier import MerchantClassifier, MerchantToClassify


@dataclass
class OpenAIClassifier(MerchantClassifier):
    model: str = "gpt-4o-mini"   # コスト効率の良いモデル推奨

    def classify(self, item: MerchantToClassify) -> dict:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            return {
                "category_major": "未分類",
                "evidence": "OPENAI_API_KEY not set",
            }

        # ここではSDKの呼び出しイメージのみ記述します。
        # 実際には `openai` パッケージを install し、client.chat.completions.create を呼びます。

        # prompt = f"""
        # 以下の店名・摘要から、家計簿のカテゴリ（大項目・中項目）を推測しJSONで返してください。
        # 店名: {item.merchant_key}
        # 摘要サンプル: {item.sample_contents}
        # """

        # 一旦ダミー応答（実装時はここをSDK呼び出しに書き換えてください）
        return {
             "category_major": "未分類",
             "confidence": None,
             "model": self.model,
             "evidence": "OpenAI classifier stub",
        }
