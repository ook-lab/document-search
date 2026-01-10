"""
embeddingの内容を詳しく確認
"""
import os
import json
from supabase import create_client
from dotenv import load_dotenv

# .envファイルを読み込む
env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
load_dotenv(env_path)

# Supabase接続
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

db = create_client(SUPABASE_URL, SUPABASE_KEY)

print("=" * 60)
print("embeddingの内容を確認")
print("=" * 60)

# サンプルデータを1件取得
result = db.table('Rawdata_NETSUPER_items').select(
    'id, product_name, product_name_normalized, general_name, category, manufacturer, embedding'
).limit(3).execute()

for idx, product in enumerate(result.data, 1):
    print(f"\n--- 商品 {idx} ---")
    print(f"商品名: {product.get('product_name')}")
    print(f"正規化商品名: {product.get('product_name_normalized')}")
    print(f"一般名: {product.get('general_name')}")
    print(f"カテゴリ: {product.get('category')}")
    print(f"メーカー: {product.get('manufacturer')}")

    embedding = product.get('embedding')
    if embedding:
        print(f"embedding型: {type(embedding)}")
        if isinstance(embedding, str):
            print(f"embedding長さ（文字列）: {len(embedding)}文字")
            print(f"embedding先頭: {embedding[:100]}...")
            # JSON形式かどうか確認
            try:
                parsed = json.loads(embedding)
                print(f"JSON解析成功: {type(parsed)}, 長さ: {len(parsed)}")
            except:
                print("JSON解析失敗")
        elif isinstance(embedding, list):
            print(f"embedding次元数: {len(embedding)}")
            print(f"embedding先頭5要素: {embedding[:5]}")
    else:
        print("embedding: None")

print("\n" + "=" * 60)
print("embeddingが何から生成されているか推測")
print("=" * 60)
print("""
可能性:
1. product_name のみ
2. product_name + category
3. product_name_normalized + general_name
4. product_name + manufacturer
5. その他の組み合わせ

データベーストリガーまたはアプリケーション側で生成されている可能性があります。
""")
