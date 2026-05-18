"""
AIモデル構成定義（最小限版）
G_unified_pipeline 移行後、実際に使用されている部分のみを保持
"""

from enum import Enum
from typing import Dict, Any


class AIProvider(Enum):
    """AIプロバイダの定義"""
    GEMINI = "gemini"
    CLAUDE = "claude"
    OPENAI = "openai"


class ModelTier:
    """モデル階層の定義（最小限）"""

    # UI回答生成（デフォルト）
    # G_cloud_run で tier="ui_response" として使用
    UI_RESPONSE_GENERATOR = {
        "provider": AIProvider.GEMINI,
        "model": "gemini-2.5-flash-lite",
        "description": "100万トークンコンテキスト、高速で安定した対話応答",
        "temperature": 0.7,
        "max_tokens": 65536,  # Gemini 2.5 Flashの最大出力トークン数
        "cost_per_1k_tokens": 0.0003
    }

    # Embedding生成
    # LLMClient で tier="embeddings" として使用
    EMBEDDING = {
        "provider": AIProvider.OPENAI,
        "model": "text-embedding-3-small",
        "description": "ベクトル検索用Embedding",
        "dimensions": 1536
    }

    @classmethod
    def get_model_for_task(cls, task: str) -> Dict[str, Any]:
        """タスクに応じた最適なモデルを返す"""
        task_mapping = {
            "ui_response": cls.UI_RESPONSE_GENERATOR,
            "embeddings": cls.EMBEDDING
        }
        return task_mapping.get(task, cls.UI_RESPONSE_GENERATOR)


def get_model_config(tier: str) -> Dict[str, Any]:
    """指定されたタスクの最適モデル設定を取得"""
    return ModelTier.get_model_for_task(tier)


# ============================================
# Deep Research 構成フロー定義
# ============================================
class ResearchFlow:
    """Deep Research構成フローの定義"""

    # 各構成フローの定義
    # steps = [Step1モデル, Step2モデル, Step3モデル]
    # Step1: Evidenceノート生成（抽象要約禁止）
    # Step2: 論点別証拠束への再編
    # Step3: 最終回答生成（ここで初めて抽象化OK）
    # ※ クエリ改善は常に Flash-lite 固定（steps に含まない）
    FLOWS = {
        # 1段: 回答生成+Evidence抽出を同時実行（最小コスト）
        "compress-1step": {
            "steps": ["gemini-2.5-flash-lite"],
            "description": "1段: 回答生成+Evidence同時（Lite×1）",
            "rounds": 1,
        },
        # 2段: Evidence整理→回答生成（安定モード）
        "compress-2step": {
            "steps": ["gemini-2.5-flash-lite", "gemini-2.5-flash-lite"],
            "description": "2段: Evidence整理(Lite) → 回答生成(Lite)",
            "rounds": 2,
        },
        # 3段全Lite: Evidence抽出→論点整理→回答生成（Lite統一）
        "compress-3step-lite": {
            "steps": ["gemini-2.5-flash-lite", "gemini-2.5-flash-lite", "gemini-2.5-flash-lite"],
            "description": "3段: Evidence抽出(Lite) → 論点整理(Lite) → 最終回答(Lite)",
            "rounds": 3,
        },
        # 3段Flash締め: Evidence抽出→論点整理→回答生成（最終のみFlash）
        "compress-3step": {
            "steps": ["gemini-2.5-flash-lite", "gemini-2.5-flash-lite", "gemini-2.5-flash-lite"],
            "description": "3段: Evidence抽出(Lite) → 論点整理(Lite) → 最終回答(Flash)",
            "rounds": 3,
        },
    }

    @classmethod
    def get_flow(cls, flow_id: str) -> Dict[str, Any]:
        """指定されたフローIDの構成を取得"""
        return cls.FLOWS.get(flow_id, cls.FLOWS["compress-1step"])

    @classmethod
    def get_all_flows(cls) -> Dict[str, Dict[str, Any]]:
        """全構成フローを取得"""
        return cls.FLOWS
