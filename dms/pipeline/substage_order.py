"""
サブステージIDの並び（ステージレター → 数値の自然順）。

ルール:
- 人間が読む列挙・argparse の choices・辞書の安定並びは `substage_sort_key` / `sorted_substage_ids` を使う。
- デバッグパイプラインの `--start` / `--end` は **時系列** のため、フラット一覧に
  「G のあとに F60 内チェーン…」のようにステージレターを逆行させない（`DEBUG_PIPELINE_SUBSTAGES_EXECUTION`）。
"""

from __future__ import annotations

import re
from typing import Iterable, List, Sequence, Tuple, Union

_SUBSTAGE_RE = re.compile(r"^([A-Za-z]+)(\d+)$")


def substage_sort_key(stage_id: str) -> Tuple[Union[int, str], ...]:
    """例: D10 > D9、F11 < G11。単一レターはここでは使わない。"""
    m = _SUBSTAGE_RE.match((stage_id or "").strip())
    if not m:
        return (stage_id,)
    return (m.group(1).upper(), int(m.group(2)))


def sorted_substage_ids(ids: Iterable[str]) -> List[str]:
    return sorted(ids, key=substage_sort_key)


def valid_target_sort_key(target: str) -> Tuple[Union[int, str], ...]:
    """VALID_TARGETS 用: 親ステージ1文字（A…G）を各レターのサブステージより先に並べる。"""
    t = (target or "").strip()
    if len(t) == 1 and t.isalpha():
        return (0, t.upper())
    return (1,) + substage_sort_key(t)


def sorted_valid_targets(stages: Sequence[str], substages: Sequence[str]) -> List[str]:
    return sorted(set(stages) | set(substages), key=valid_target_sort_key)


_EXPORT_CLASS_RE = re.compile(r"^E(\d+)")


def e_stage_export_sort_key(export_name: str) -> Tuple[int, str]:
    """stage_e.__all__ 等: E1 < E5（クラス名の先頭 E 番号）。表 UI チェーンは `stage_g`（G15→G22→…→G65）。"""
    m = _EXPORT_CLASS_RE.match(export_name or "")
    if m:
        return (int(m.group(1)), export_name)
    return (10**9, export_name)


# run_debug_pipeline: --start/--end のレンジ計算に使う実行順（時系列のみ。E を G の後に置かない）
# F データ平面 → G UI（数値＝実行順）
DEBUG_PIPELINE_SUBSTAGES_EXECUTION: Tuple[str, ...] = (
    "A3",
    "B1",
    "D3",
    "D5",
    "D8",
    "D9",
    "D10",
    "E1",
    "F11",
    "F13",
    "F17",
    "G11",
)

__all__ = [
    "DEBUG_PIPELINE_SUBSTAGES_EXECUTION",
    "e_stage_export_sort_key",
    "sorted_substage_ids",
    "sorted_valid_targets",
    "substage_sort_key",
    "valid_target_sort_key",
]
