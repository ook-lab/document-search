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
        "model": "gemini-3.5-flash-lite",
        "description": "高速対話",
        "temperature": 0.7,
        "max_tokens": 65536,
        "cost_per_1k_tokens": 0.0003,
    }
    EMBEDDING = {
        "provider": AIProvider.GEMINI,
        "model": "gemini-embedding-2",
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
        if task not in task_mapping:
            raise ValueError(f"不明なタスク: {task}")
        return task_mapping[task]


def get_model_config(tier: str) -> Dict[str, Any]:
    return ModelTier.get_model_for_task(tier)


class ResearchFlow:
    FLOWS = {
        "single-35-flash-lite": {
            "steps": ["gemini-3.5-flash-lite"],
            "description": "1段: Gemini 3.5 Flash-Lite単独",
            "rounds": 1,
        },
    }

    @classmethod
    def get_flow(cls, flow_id: str) -> Dict[str, Any]:
        if flow_id not in cls.FLOWS:
            raise ValueError(f"不明なフローID: {flow_id}")
        return cls.FLOWS[flow_id]
