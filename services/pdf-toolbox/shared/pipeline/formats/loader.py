"""
formats/loader.py - フォーマット定義ローダー

目的:
  shared/pipeline/formats/ 配下の YAML 定義を読み込み、
  format_id をキーとした辞書を返す。

用途:
  F2/F3 の判定器がこのローダーを呼び出し、
  フォーマット判定ルール（detect）と意味抽出スキーマ（schema）を取得する。

例外方針:
  - _index.yaml が無い/壊れてる: warning + 空dict返却
  - 個別 yaml が無い/壊れてる: warning + そのformatだけスキップ
  （パイプラインを止めない設計）
"""

from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional
from loguru import logger

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore


# 必須トップキー
REQUIRED_KEYS = ["format_id", "version", "detect", "fingerprint", "schema"]


def load_format_registry(base_dir: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    """
    フォーマット定義を読み込み、format_id をキーとした辞書を返す。

    Args:
        base_dir: formats/ ディレクトリのパス。
                  未指定なら本ファイルと同じディレクトリを使用。

    Returns:
        {format_id: {detect, fingerprint, schema, meta, ...}, ...}
        priority 降順で挿入（Python 3.7+ の dict は挿入順を保持）
    """
    if yaml is None:
        logger.warning("[loader] PyYAML がインストールされていません。空の registry を返します。")
        return {}

    # base_dir の解決
    if base_dir is None:
        base_dir = Path(__file__).parent
    else:
        base_dir = Path(base_dir)

    if not base_dir.exists():
        logger.warning(f"[loader] formats ディレクトリが存在しません: {base_dir}")
        return {}

    # _index.yaml の読み込み
    index_path = base_dir / "_index.yaml"
    if not index_path.exists():
        logger.warning(f"[loader] _index.yaml が存在しません: {index_path}")
        return {}

    try:
        with open(index_path, "r", encoding="utf-8") as f:
            index_data = yaml.safe_load(f)
    except Exception as e:
        logger.warning(f"[loader] _index.yaml の読み込みに失敗: {e}")
        return {}

    if not index_data:
        logger.warning("[loader] _index.yaml が空です")
        return {}

    # enabled なフォーマット一覧を取得
    enabled_formats = get_enabled_formats(index_data)
    if not enabled_formats:
        logger.info("[loader] enabled なフォーマットがありません")
        return {}

    # priority 降順でソート
    enabled_formats.sort(key=lambda x: x.get("priority", 0), reverse=True)

    logger.info(f"[loader] enabled フォーマット数: {len(enabled_formats)}")

    # 個別 yaml の読み込み
    registry: Dict[str, Dict[str, Any]] = {}

    for entry in enabled_formats:
        format_id = entry.get("format_id")
        if not format_id:
            logger.warning(f"[loader] format_id が空のエントリをスキップ: {entry}")
            continue

        # ファイル名の決定（file キーがあればそれ、なければ format_id.yaml）
        file_name = entry.get("file") or f"{format_id}.yaml"
        file_path = base_dir / file_name

        if not file_path.exists():
            logger.warning(f"[loader] フォーマット定義ファイルが存在しません: {file_path}")
            continue

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                format_def = yaml.safe_load(f)
        except Exception as e:
            logger.warning(f"[loader] {file_name} の読み込みに失敗: {e}")
            continue

        if not format_def:
            logger.warning(f"[loader] {file_name} が空です")
            continue

        # バリデーション
        is_valid, messages = validate_format_def(format_def)
        if not is_valid:
            logger.warning(f"[loader] {format_id} のバリデーション失敗: {messages}")
            continue

        # format_id の一致確認
        file_format_id = format_def.get("format_id")
        if file_format_id != format_id:
            logger.warning(
                f"[loader] format_id 不一致: index={format_id}, file={file_format_id}"
            )
            # 警告だけ出してファイル側の format_id を採用
            format_id = file_format_id

        # index からのメタ情報をマージ
        format_def["_index_entry"] = entry
        format_def["_priority"] = entry.get("priority", 0)
        format_def["_enabled"] = True

        registry[format_id] = format_def
        logger.info(
            f"[loader] ロード成功: {format_id} (priority={entry.get('priority', 0)})"
        )

    logger.info(f"[loader] 合計 {len(registry)} フォーマットをロード")
    return registry


def validate_format_def(d: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """
    フォーマット定義の必須キーをチェック。

    Args:
        d: フォーマット定義の辞書

    Returns:
        (is_valid, messages)
        - is_valid: 全必須キーがあれば True
        - messages: 欠落キーのリスト
    """
    if not d or not isinstance(d, dict):
        return False, ["定義が空または辞書ではありません"]

    missing = []
    for key in REQUIRED_KEYS:
        if key not in d:
            missing.append(f"必須キー欠落: {key}")
        elif d[key] is None:
            missing.append(f"必須キーが空: {key}")

    # detect の詳細チェック（任意：より厳格にしたい場合）
    detect = d.get("detect")
    if detect and isinstance(detect, dict):
        if not detect.get("scope"):
            missing.append("detect.scope が未指定")
        if not detect.get("must_all") and not detect.get("must_any"):
            missing.append("detect に must_all または must_any が必要")

    # schema の詳細チェック（任意）
    schema = d.get("schema")
    if schema and isinstance(schema, dict):
        if "header" not in schema:
            missing.append("schema.header が未指定")

    is_valid = len(missing) == 0
    return is_valid, missing


def get_enabled_formats(index_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    _index.yaml のデータから enabled=true のフォーマット一覧を取得。

    Args:
        index_data: _index.yaml を読み込んだ辞書

    Returns:
        enabled なフォーマットエントリのリスト
    """
    if not index_data or not isinstance(index_data, dict):
        return []

    # formats キーから取得
    formats = index_data.get("formats")
    if not formats:
        return []

    # リストでない場合の吸収
    if not isinstance(formats, list):
        logger.warning("[loader] formats がリストではありません")
        return []

    # enabled=true のみ抽出
    enabled = []
    for entry in formats:
        if not isinstance(entry, dict):
            continue
        # enabled が明示的に false でなければ有効とみなす（デフォルト true）
        if entry.get("enabled", True):
            enabled.append(entry)

    return enabled


# -----------------------------------------------------------------------------
# テスト用：直接実行時の動作確認
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    import json

    logger.info("=== loader.py 動作確認 ===")
    registry = load_format_registry()

    print(f"\n=== ロードされたフォーマット ({len(registry)}件) ===")
    for fmt_id, fmt_def in registry.items():
        print(f"\n--- {fmt_id} ---")
        print(f"  version: {fmt_def.get('version')}")
        print(f"  priority: {fmt_def.get('_priority')}")
        print(f"  detect.scope: {fmt_def.get('detect', {}).get('scope')}")
        print(f"  detect.must_all: {fmt_def.get('detect', {}).get('must_all')}")
        print(f"  schema.header: {fmt_def.get('schema', {}).get('header')}")
