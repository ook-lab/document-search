"""user_context.yaml 読み込み（docsearch パッケージ直下）。"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import yaml


def _yaml_path() -> Path:
    # リポジトリ: services/doc-search/docsearch/user_context.yaml
    # Docker: /app/docsearch/user_context.yaml
    here = Path(__file__).resolve().parent
    return here / "user_context.yaml"


def load_user_context() -> Dict[str, Any]:
    path = _yaml_path()
    if not path.exists():
        return {"children": [], "settings": {}}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {"children": [], "settings": {}}
