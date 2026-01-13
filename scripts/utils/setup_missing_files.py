"""
セットアップスクリプト: 不足しているディレクトリとファイルを作成
"""
import os
import json
from pathlib import Path

# プロジェクトルート
project_root = Path(__file__).parent

# frontend/schemas ディレクトリを作成
schemas_dir = project_root / "frontend" / "schemas"
schemas_dir.mkdir(parents=True, exist_ok=True)
print(f"✓ ディレクトリ作成: {schemas_dir}")

# generic.json スキーマファイルを作成
generic_schema = {
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

generic_schema_file = schemas_dir / "generic.json"
with open(generic_schema_file, 'w', encoding='utf-8') as f:
    json.dump(generic_schema, f, ensure_ascii=False, indent=2)
print(f"✓ ファイル作成: {generic_schema_file}")

print("\n✅ セットアップ完了!")
