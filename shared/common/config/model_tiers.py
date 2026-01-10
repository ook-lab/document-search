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
        "model": "gemini-2.5-flash",
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

    # 各構成フローの定義: {"steps": [model_list], "description": "説明", "cost": コスト, "ability": 能力}
    FLOWS = {
        "flash-x1": {
            "steps": ["gemini-2.5-flash"],
            "description": "Flash×1 (標準・高速)",
            "cost": 0.64,
            "ability": 8.0
        },
        "lite-lite-pro": {
            "steps": ["gemini-2.5-flash-lite", "gemini-2.5-flash-lite", "gemini-2.5-pro"],
            "description": "Lite→Lite→Pro (コスパ最強)",
            "cost": 2.86,
            "ability": 8.8
        },
        "lite-flash-pro": {
            "steps": ["gemini-2.5-flash-lite", "gemini-2.5-flash", "gemini-2.5-pro"],
            "description": "Lite→Flash→Pro (黄金比・標準)",
            "cost": 2.94,
            "ability": 8.95
        },
        "flash-flash-pro": {
            "steps": ["gemini-2.5-flash", "gemini-2.5-flash", "gemini-2.5-pro"],
            "description": "Flash→Flash→Pro (3回ループの王)",
            "cost": 3.02,
            "ability": 9.3
        },
        "lite-flash-flash-pro": {
            "steps": ["gemini-2.5-flash-lite", "gemini-2.5-flash", "gemini-2.5-flash", "gemini-2.5-pro"],
            "description": "Lite→Flash→Flash→Pro (賢い節約術)",
            "cost": 3.1,
            "ability": 9.45
        },
        "flash-flash-flash-pro": {
            "steps": ["gemini-2.5-flash", "gemini-2.5-flash", "gemini-2.5-flash", "gemini-2.5-pro"],
            "description": "Flash→Flash→Flash→Pro (Deep Research推奨)",
            "cost": 3.18,
            "ability": 9.5
        },
        "lite-lite-flash-flash": {
            "steps": ["gemini-2.5-flash-lite", "gemini-2.5-flash-lite", "gemini-2.5-flash", "gemini-2.5-flash"],
            "description": "Lite→Lite→Flash→Flash (粘りの凡人)",
            "cost": 0.48,
            "ability": 7.35
        },
        "flash-flash-flash": {
            "steps": ["gemini-2.5-flash", "gemini-2.5-flash", "gemini-2.5-flash"],
            "description": "Flash→Flash→Flash (ザ・標準)",
            "cost": 0.48,
            "ability": 7.4
        },
        "lite-lite-lite-flash": {
            "steps": ["gemini-2.5-flash-lite", "gemini-2.5-flash-lite", "gemini-2.5-flash-lite", "gemini-2.5-flash"],
            "description": "Lite→Lite→Lite→Flash (人海戦術)",
            "cost": 0.4,
            "ability": 7.5
        },
        "flash-flash-flash-flash": {
            "steps": ["gemini-2.5-flash", "gemini-2.5-flash", "gemini-2.5-flash", "gemini-2.5-flash"],
            "description": "Flash→Flash→Flash→Flash (優等生の限界)",
            "cost": 0.64,
            "ability": 7.85
        },
        "lite-lite-flash-pro": {
            "steps": ["gemini-2.5-flash-lite", "gemini-2.5-flash-lite", "gemini-2.5-flash", "gemini-2.5-pro"],
            "description": "Lite→Lite→Flash→Pro (泥臭い名探偵)",
            "cost": 3.02,
            "ability": 9.28
        },
        "lite-lite-lite-pro": {
            "steps": ["gemini-2.5-flash-lite", "gemini-2.5-flash-lite", "gemini-2.5-flash-lite", "gemini-2.5-pro"],
            "description": "Lite→Lite→Lite→Pro (質より量の極み)",
            "cost": 2.94,
            "ability": 9.15
        },
        "flash-pro-pro": {
            "steps": ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.5-pro"],
            "description": "Flash→Pro→Pro (精鋭部隊)",
            "cost": 5.56,
            "ability": 9.6
        },
        "lite-pro-pro": {
            "steps": ["gemini-2.5-flash-lite", "gemini-2.5-pro", "gemini-2.5-pro"],
            "description": "Lite→Pro→Pro (一点突破・改)",
            "cost": 5.48,
            "ability": 9.55
        },
        "lite-lite-pro-pro": {
            "steps": ["gemini-2.5-flash-lite", "gemini-2.5-flash-lite", "gemini-2.5-pro", "gemini-2.5-pro"],
            "description": "Lite→Lite→Pro→Pro (素材重視のプロ)",
            "cost": 5.56,
            "ability": 9.65
        },
        "lite-flash-pro-pro": {
            "steps": ["gemini-2.5-flash-lite", "gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.5-pro"],
            "description": "Lite→Flash→Pro→Pro (超・黄金比)",
            "cost": 5.64,
            "ability": 9.7
        },
        "flash-flash-pro-pro": {
            "steps": ["gemini-2.5-flash", "gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.5-pro"],
            "description": "Flash→Flash→Pro→Pro (編集長決済)",
            "cost": 5.72,
            "ability": 9.75
        },
        "pro-pro-pro": {
            "steps": ["gemini-2.5-pro", "gemini-2.5-pro", "gemini-2.5-pro"],
            "description": "Pro→Pro→Pro (富豪の鉄板)",
            "cost": 8.1,
            "ability": 9.85
        },
        "lite-pro-pro-pro": {
            "steps": ["gemini-2.5-flash-lite", "gemini-2.5-pro", "gemini-2.5-pro", "gemini-2.5-pro"],
            "description": "Lite→Pro→Pro→Pro (成り上がり)",
            "cost": 8.18,
            "ability": 9.9
        },
        "flash-pro-pro-pro": {
            "steps": ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.5-pro", "gemini-2.5-pro"],
            "description": "Flash→Pro→Pro→Pro (天才集団)",
            "cost": 8.26,
            "ability": 9.95
        },
        "pro-pro-pro-pro": {
            "steps": ["gemini-2.5-pro", "gemini-2.5-pro", "gemini-2.5-pro", "gemini-2.5-pro"],
            "description": "Pro→Pro→Pro→Pro (全知全能)",
            "cost": 10.8,
            "ability": 9.99
        }
    }

    @classmethod
    def get_flow(cls, flow_id: str) -> Dict[str, Any]:
        """指定されたフローIDの構成を取得"""
        return cls.FLOWS.get(flow_id, cls.FLOWS["flash-x1"])

    @classmethod
    def get_all_flows(cls) -> Dict[str, Dict[str, Any]]:
        """全構成フローを取得"""
        return cls.FLOWS
