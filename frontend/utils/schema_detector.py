"""
Schema Detector
メタデータの構造から適切なスキーマを判定
"""
import json
from pathlib import Path
from typing import Dict, Any, Optional, List


class SchemaDetector:
    """スキーマ検出クラス"""

    def __init__(self):
        """スキーマファイルをロード"""
        self.schemas = {}
        # frontend/utils/schema_detector.py → frontend/schemas/
        self.schema_dir = Path(__file__).parent.parent / "schemas"
        self._load_schemas()

        # デフォルトの汎用スキーマを定義（ファイルがない場合のフォールバック）
        if "generic" not in self.schemas:
            self.schemas["generic"] = {
                "$schema": "http://json-schema.org/draft-07/schema#",
                "title": "Generic Document Schema",
                "type": "object",
                "properties": {
                    "title": {
                        "type": ["string", "null"],
                        "title": "タイトル",
                        "description": "ドキュメントのタイトル"
                    },
                    "summary": {
                        "type": ["string", "null"],
                        "title": "要約",
                        "description": "ドキュメントの要約"
                    },
                    "document_date": {
                        "type": ["string", "null"],
                        "format": "date",
                        "title": "文書日付",
                        "description": "文書の日付（YYYY-MM-DD）"
                    },
                    "tags": {
                        "type": "array",
                        "title": "タグ",
                        "description": "ドキュメントに関連するタグ",
                        "items": {
                            "type": "string"
                        }
                    },
                    "category": {
                        "type": ["string", "null"],
                        "title": "カテゴリー",
                        "description": "ドキュメントのカテゴリー"
                    },
                    "sender": {
                        "type": ["string", "null"],
                        "title": "送信者",
                        "description": "送信者名またはメールアドレス"
                    },
                    "subject": {
                        "type": ["string", "null"],
                        "title": "件名",
                        "description": "メールまたは文書の件名"
                    },
                    "content_type": {
                        "type": ["string", "null"],
                        "title": "内容タイプ",
                        "description": "文書の内容タイプ"
                    }
                },
                "required": []
            }

    def _load_schemas(self):
        """schemas/ディレクトリから全てのスキーマをロード"""
        if not self.schema_dir.exists():
            print(f"スキーマディレクトリが存在しません: {self.schema_dir}")
            return

        for schema_file in self.schema_dir.glob("*.json"):
            try:
                with open(schema_file, 'r', encoding='utf-8') as f:
                    schema_name = schema_file.stem
                    self.schemas[schema_name] = json.load(f)
            except Exception as e:
                print(f"スキーマ読み込みエラー ({schema_file.name}): {e}")

    def detect_schema(self, doc_type: str, metadata: Dict[str, Any]) -> Optional[str]:
        """
        doc_typeとmetadataからスキーマを判定

        現在は全てのドキュメントに汎用スキーマを適用

        Args:
            doc_type: ドキュメントタイプ（サンプル用、現在は使用しない）
            metadata: メタデータ辞書（サンプル用、現在は使用しない）

        Returns:
            スキーマ名（常に "generic"）
        """
        # 全てのドキュメントに汎用スキーマを適用
        if "generic" in self.schemas:
            return "generic"

        return None

    def _is_ikuya_school_structure(self, metadata: Dict[str, Any]) -> bool:
        """ikuya_school の構造かどうか判定"""
        # Noneの場合に空の辞書に置き換える
        metadata = metadata or {}

        # basic_info フィールドの存在をチェック
        if "basic_info" in metadata and isinstance(metadata["basic_info"], dict):
            return True

        # 新しい構造化フィールドの存在をチェック（優先）
        if "monthly_schedule_list" in metadata or "learning_content_list" in metadata:
            return True

        # weekly_schedule with class_schedules の存在をチェック（後方互換性）
        if "weekly_schedule" in metadata:
            weekly_schedule = metadata["weekly_schedule"]
            if isinstance(weekly_schedule, list) and len(weekly_schedule) > 0:
                first_day = weekly_schedule[0]
                # class_schedules フィールドがあれば ikuya_school
                if "class_schedules" in first_day:
                    return True

        # text_blocks と structured_tables の両方があれば ikuya_school の可能性が高い
        if "text_blocks" in metadata and "structured_tables" in metadata:
            return True

        return False

    def _is_timetable_structure(self, metadata: Dict[str, Any]) -> bool:
        """時間割の構造かどうか判定"""
        # Noneの場合に空の辞書に置き換える
        metadata = metadata or {}

        # daily_scheduleフィールドの存在をチェック
        if "daily_schedule" in metadata:
            daily_schedule = metadata["daily_schedule"]
            if isinstance(daily_schedule, list) and len(daily_schedule) > 0:
                # 最初の要素がdate, day_of_week, periodsを持つかチェック
                first_day = daily_schedule[0]
                return all(key in first_day for key in ["date", "day_of_week", "periods"])
        return False

    def _is_school_notice_structure(self, metadata: Dict[str, Any]) -> bool:
        """学校通知の構造かどうか判定"""
        # Noneの場合に空の辞書に置き換える
        metadata = metadata or {}

        # notice_typeまたはweekly_scheduleフィールドの存在をチェック
        if "notice_type" in metadata:
            return True
        if "weekly_schedule" in metadata:
            return True
        # school_nameとgradeの両方が存在する場合も学校通知として扱う
        if "school_name" in metadata and "grade" in metadata:
            return True
        return False

    def get_schema(self, schema_name: str) -> Optional[Dict[str, Any]]:
        """
        スキーマ名からスキーマ定義を取得

        Args:
            schema_name: スキーマ名

        Returns:
            スキーマ定義辞書、存在しない場合はNone
        """
        return self.schemas.get(schema_name)

    def get_editable_fields(self, schema_name: str) -> List[Dict[str, Any]]:
        """
        スキーマから編集可能なフィールドリストを取得

        Args:
            schema_name: スキーマ名

        Returns:
            フィールド定義のリスト
        """
        schema = self.get_schema(schema_name)
        if not schema or "properties" not in schema:
            return []

        fields = []
        for field_name, field_def in schema["properties"].items():
            fields.append({
                "name": field_name,
                "type": field_def.get("type", "string"),
                "title": field_def.get("title", field_name),
                "description": field_def.get("description", ""),
                "required": field_name in schema.get("required", []),
                "enum": field_def.get("enum"),
                "format": field_def.get("format"),
                "items": field_def.get("items")
            })

        return fields

    def validate_metadata(self, schema_name: str, metadata: Dict[str, Any]) -> tuple[bool, List[str]]:
        """
        メタデータがスキーマに適合しているか検証

        Args:
            schema_name: スキーマ名
            metadata: 検証するメタデータ

        Returns:
            (検証結果, エラーメッセージリスト)
        """
        # メタデータがNoneの場合、安全のために空の辞書に変換
        if metadata is None:
            metadata = {}

        schema = self.get_schema(schema_name)
        if not schema:
            return False, [f"スキーマ '{schema_name}' が見つかりません"]

        errors = []

        # 必須フィールドのチェック
        required_fields = schema.get("required", [])
        for field in required_fields:
            if field not in metadata:
                errors.append(f"必須フィールド '{field}' が欠けています")

        # 型のチェック（簡易版）
        properties = schema.get("properties", {})
        for field_name, value in metadata.items():
            if field_name in properties:
                expected_type = properties[field_name].get("type")
                if expected_type and not self._check_type(value, expected_type):
                    errors.append(f"フィールド '{field_name}' の型が不正です（期待: {expected_type}）")

        return len(errors) == 0, errors

    def _check_type(self, value: Any, expected_type: Any) -> bool:
        """値の型をチェック（配列形式の型定義にも対応）"""
        type_map = {
            "string": str,
            "integer": int,
            "number": (int, float),
            "boolean": bool,
            "array": list,
            "object": dict,
            "null": type(None)
        }

        # expected_type が配列の場合（例: ["string", "null"]）
        if isinstance(expected_type, list):
            # いずれかの型に一致すればOK
            return any(self._check_type(value, t) for t in expected_type)

        # expected_type が文字列の場合
        if not isinstance(expected_type, str):
            return True  # 不明な型は検証をスキップ

        expected_python_type = type_map.get(expected_type)
        if expected_python_type is None:
            return True  # 不明な型は検証をスキップ

        return isinstance(value, expected_python_type)
