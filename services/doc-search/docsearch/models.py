"""doc-search 用モデル・フロー定義（検索サービス専用）。"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict


class AIProvider(Enum):
    GEMINI = "gemini"
    CLAUDE = "claude"
    OPENAI = "openai"


class ModelTier:
    UI_RESPONSE_GENERATOR = {
        "provider": AIProvider.GEMINI,
        "model": "gemini-2.5-flash-lite",
        "description": "高速対話",
        "temperature": 0.7,
        "max_tokens": 65536,
        "cost_per_1k_tokens": 0.0003,
    }
    EMBEDDING = {
        "provider": AIProvider.OPENAI,
        "model": "text-embedding-3-small",
        "description": "ベクトル検索用",
        "dimensions": 1536,
    }

    @classmethod
    def get_model_for_task(cls, task: str) -> Dict[str, Any]:
        task_mapping = {
            "ui_response": cls.UI_RESPONSE_GENERATOR,
            "embeddings": cls.EMBEDDING,
            "utility": cls.UI_RESPONSE_GENERATOR,
        }
        return task_mapping.get(task, cls.UI_RESPONSE_GENERATOR)


def get_model_config(tier: str) -> Dict[str, Any]:
    return ModelTier.get_model_for_task(tier)


class ResearchFlow:
    FLOWS = {
        "single-25-lite": {
            "steps": ["gemini-2.5-flash-lite"],
            "description": "1段: Gemini 2.5 Flash-Lite単独",
            "rounds": 1,
        },
        "cascade-25lite-31lite-preview": {
            "steps": ["gemini-2.5-flash-lite", "gemini-3.1-flash-lite-preview"],
            "description": "2段: 2.5 Flash-Lite → 3.1 Flash-Lite Preview",
            "rounds": 2,
        },
        "single-31-lite-preview": {
            "steps": ["gemini-3.1-flash-lite-preview"],
            "description": "1段: Gemini 3.1 Flash-Lite Preview単独",
            "rounds": 1,
        },
    }

    @classmethod
    def get_flow(cls, flow_id: str) -> Dict[str, Any]:
        return cls.FLOWS.get(flow_id, cls.FLOWS["single-25-lite"])
