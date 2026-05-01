"""
YAML Loader
CLASSIFICATION_MAPPING_v2.0.yaml を読み込んで文字列として返す
"""
from pathlib import Path
import yaml


def get_classification_yaml_string() -> str:
    """
    CLASSIFICATION_MAPPING_v2.0.yaml を読み込んで文字列として返す
    
    Returns:
        YAMLファイルの内容（文字列）
    """
    yaml_path = Path(__file__).parent / "CLASSIFICATION_MAPPING_v2.0.yaml"
    
    if not yaml_path.exists():
        raise FileNotFoundError(f"分類マッピングファイルが見つかりません: {yaml_path}")
    
    with open(yaml_path, "r", encoding="utf-8") as f:
        return f.read()


def load_classification_mapping() -> dict:
    """
    CLASSIFICATION_MAPPING_v2.0.yaml を辞書として読み込む

    Returns:
        YAMLファイルの内容（辞書）
    """
    yaml_path = Path(__file__).parent / "CLASSIFICATION_MAPPING_v2.0.yaml"

    if not yaml_path.exists():
        raise FileNotFoundError(f"分類マッピングファイルが見つかりません: {yaml_path}")

    with open(yaml_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_user_context() -> dict:
    """
    user_context.yaml を辞書として読み込む

    Returns:
        YAMLファイルの内容（辞書）
        ファイルが存在しない場合は空の辞書を返す
    """
    yaml_path = Path(__file__).parent / "user_context.yaml"

    if not yaml_path.exists():
        # ファイルが存在しない場合は空の辞書を返す（エラーにしない）
        return {"children": [], "settings": {}}

    with open(yaml_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_user_context_string() -> str:
    """
    user_context.yaml を読み込んで文字列として返す

    Returns:
        YAMLファイルの内容（文字列）
        ファイルが存在しない場合は空文字列を返す
    """
    yaml_path = Path(__file__).parent / "user_context.yaml"

    if not yaml_path.exists():
        return ""

    with open(yaml_path, "r", encoding="utf-8") as f:
        return f.read()


def get_family_info() -> dict:
    """
    user_context.yaml から家族構成情報を取得

    Returns:
        家族構成情報（辞書）
        例: {'father': {'name': 'yoshinori', 'display_name': '宜紀'}, ...}
    """
    context = load_user_context()
    return context.get("family", {})


def get_organization_info() -> dict:
    """
    user_context.yaml から組織情報（学校名、クラス名など）を取得

    Returns:
        組織情報（辞書）
        例: {'school': {'name': '洗足学園小学校', 'current_class': '2025_5B'}}
    """
    context = load_user_context()
    return context.get("organizations", {})


def get_auth_info() -> dict:
    """
    user_context.yaml から認証情報を取得

    Returns:
        認証情報（辞書）
        例: {'default_email': 'ookubo.y@workspace-o.com'}
    """
    context = load_user_context()
    return context.get("auth", {})