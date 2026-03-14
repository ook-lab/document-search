"""
embeddingの生成元を逆算
実際のembeddingと各カラムから生成したembeddingを比較
"""
import os
import json
from supabase import create_client
from dotenv import load_dotenv
from openai import OpenAI
import numpy as np

# .envファイルを読み込む
env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
load_dotenv(env_path)

# Supabase接続
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

db = create_client(SUPABASE_URL, SUPABASE_KEY)
openai_client = OpenAI(api_key=OPENAI_API_KEY)

def generate_embedding(text):
    """OpenAI text-embedding-3-smallでembeddingを生成"""
    if not text:
        return None
    response = openai_client.embeddings.create(
        model="text-embedding-3-small",
        input=text,
        dimensions=1536
    )
    return response.data[0].embedding

def cosine_similarity(vec1, vec2):
    """コサイン類似度を計算"""
    vec1 = np.array(vec1)
    vec2 = np.array(vec2)
    return np.dot(vec1, vec2) / (np.linalg.norm(vec1) * np.linalg.norm(vec2))

print("=" * 80)
print("embeddingの生成元を逆算")
print("=" * 80)

# サンプルデータを取得
result = db.table('Rawdata_NETSUPER_items').select(
    'product_name, product_name_normalized, general_name, category, manufacturer, embedding'
).limit(3).execute()

for idx, product in enumerate(result.data, 1):
    print(f"\n{'='*80}")
    print(f"商品 {idx}: {product.get('product_name')}")
    print(f"{'='*80}")

    # 実際のembeddingを取得
    actual_embedding_str = product.get('embedding')
    if not actual_embedding_str:
        print("embeddingがありません")
        continue

    actual_embedding = json.loads(actual_embedding_str)

    # 各カラムの組み合わせでembeddingを生成して比較
    test_cases = [
        ("product_name", product.get('product_name')),
        ("product_name_normalized", product.get('product_name_normalized')),
        ("general_name", product.get('general_name')),
        ("category", product.get('category')),
        ("manufacturer", product.get('manufacturer')),
        ("product_name + category", f"{product.get('product_name')} {product.get('category') or ''}".strip()),
        ("product_name + manufacturer", f"{product.get('product_name')} {product.get('manufacturer') or ''}".strip()),
        ("product_name + category + manufacturer",
         f"{product.get('product_name')} {product.get('category') or ''} {product.get('manufacturer') or ''}".strip()),
    ]

    print("\n類似度スコア:")
    print("-" * 80)

    for label, text in test_cases:
        if not text or text.strip() == "":
            continue

        try:
            test_embedding = generate_embedding(text)
            if test_embedding:
                similarity = cosine_similarity(actual_embedding, test_embedding)
                marker = " ★★★" if similarity > 0.99 else ""
                print(f"{label:40s}: {similarity:.6f}{marker}")
        except Exception as e:
            print(f"{label:40s}: エラー - {e}")

print("\n" + "=" * 80)
print("結論")
print("=" * 80)
print("類似度が0.99以上（★★★マーク）のものがembedding生成元です")
