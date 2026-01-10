"""
カテゴリを全上書き：埋め込みベクトルから階層構造を自動生成

処理フロー：
1. MASTER_Categories_productを全削除（バックアップ取得）
2. general_name_embeddingでクラスタリング
3. AIで階層的に命名（小→中→大）
4. 新しい階層をMASTER_Categories_productに登録
5. 各商品にcategory_idを自動設定
"""

import os
import numpy as np
from sklearn.cluster import KMeans
from supabase import create_client
from typing import List, Dict, Tuple
import json
from datetime import datetime
from shared.ai.llm_client.llm_client import LLMClient

# 設定
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not all([SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY]):
    raise Exception("環境変数を設定してください")

db = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
llm_client = LLMClient()

# クラスタ数設定
N_SMALL_CATEGORIES = 150  # 小分類の数
N_MEDIUM_CATEGORIES = 30   # 中分類の数
N_LARGE_CATEGORIES = 8     # 大分類の数


def backup_existing_categories():
    """
    既存カテゴリをバックアップ
    """
    print("\n=== 既存カテゴリをバックアップ中 ===")

    result = db.table('MASTER_Categories_product').select('*').execute()

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_path = f'L_product_classification/category_backup_{timestamp}.json'

    with open(backup_path, 'w', encoding='utf-8') as f:
        json.dump(result.data, f, ensure_ascii=False, indent=2)

    print(f"✅ バックアップ完了: {backup_path}")
    print(f"   {len(result.data)}件のカテゴリをバックアップ")

    return backup_path


def clear_existing_categories():
    """
    MASTER_Categories_productを全削除
    """
    print("\n=== 既存カテゴリを削除中 ===")

    # 全削除
    result = db.table('MASTER_Categories_product').delete().neq('id', '00000000-0000-0000-0000-000000000000').execute()

    print(f"✅ 削除完了")


def fetch_embeddings() -> Tuple[List[Dict], np.ndarray]:
    """
    全商品のgeneral_name_embeddingを取得
    """
    print("\n=== 埋め込みベクトルを取得中 ===")

    all_products = []
    batch_size = 1000
    offset = 0

    while True:
        print(f"取得中: {offset}件目から...")

        result = db.table('Rawdata_NETSUPER_items').select(
            'id, product_name, general_name, general_name_embedding'
        ).not_.is_('general_name_embedding', 'null').range(
            offset, offset + batch_size - 1
        ).execute()

        if not result.data:
            break

        all_products.extend(result.data)
        offset += batch_size

        if offset >= 10000:
            print("警告: 10,000件で停止")
            break

    print(f"取得完了: {len(all_products)}件")

    # 埋め込みベクトルを抽出
    embeddings = []
    valid_products = []

    for product in all_products:
        emb = product.get('general_name_embedding')
        if emb:
            # 文字列の場合はJSONパース
            if isinstance(emb, str):
                try:
                    emb = json.loads(emb)
                except:
                    continue

            if isinstance(emb, list) and len(emb) > 0:
                embeddings.append(emb)
                valid_products.append(product)

    embeddings_array = np.array(embeddings)
    print(f"有効な埋め込み: {len(embeddings_array)}件")

    return valid_products, embeddings_array


def cluster_and_name(embeddings: np.ndarray, n_clusters: int, products: List[Dict], level_name: str) -> Dict:
    """
    クラスタリング＆AI命名
    """
    print(f"\n=== {level_name}を生成: {n_clusters}グループ ===")

    kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
    labels = kmeans.fit_predict(embeddings)

    cluster_info = {}

    for cluster_id in range(n_clusters):
        cluster_mask = labels == cluster_id
        cluster_products = [products[i] for i in range(len(products)) if cluster_mask[i]]

        if len(cluster_products) == 0:
            continue

        # 代表商品を10個選ぶ
        sample_size = min(10, len(cluster_products))
        samples = np.random.choice(cluster_products, sample_size, replace=False).tolist()
        product_names = [p['product_name'] for p in samples]

        # AI命名
        prompt = f"""以下の商品リストに共通する、適切な{level_name}名を1つ提案してください。

商品リスト:
{chr(10).join(f"- {name}" for name in product_names)}

要件:
- {level_name}名は2-6文字の日本語
- 具体的で分かりやすい名前
- 例：「乳製品」「調味料」「冷凍食品」

{level_name}名のみを回答してください。"""

        try:
            response = llm_client.call_model(
                tier="stageh_extraction",
                prompt=prompt,
                model_name="gemini-2.5-flash-lite",
                temperature=0.3,
                max_output_tokens=20
            )
            if response.get("success"):
                category_name = response.get("content", "").strip()
            else:
                raise Exception(response.get("error", "Unknown error"))
        except Exception as e:
            print(f"  ❌ AI命名失敗: {e}")
            category_name = f"{level_name}{cluster_id}"

        cluster_info[cluster_id] = {
            'name': category_name,
            'count': len(cluster_products),
            'centroid': kmeans.cluster_centers_[cluster_id],
            'product_indices': [i for i in range(len(products)) if cluster_mask[i]]
        }

        print(f"  {cluster_id}: {category_name} ({len(cluster_products)}件)")

    return cluster_info, labels


def build_hierarchy(products: List[Dict], embeddings: np.ndarray):
    """
    3層の階層構造を構築
    """
    print("\n" + "=" * 60)
    print("階層構造の自動生成")
    print("=" * 60)

    # ステップ1: 小分類
    small_clusters, small_labels = cluster_and_name(
        embeddings, N_SMALL_CATEGORIES, products, "小分類"
    )

    # ステップ2: 中分類（小分類の重心をクラスタリング）
    small_centroids = np.array([info['centroid'] for info in small_clusters.values()])
    small_products_dummy = [{'product_name': info['name']} for info in small_clusters.values()]

    medium_clusters, medium_labels = cluster_and_name(
        small_centroids, N_MEDIUM_CATEGORIES, small_products_dummy, "中分類"
    )

    # ステップ3: 大分類（中分類の重心をクラスタリング）
    medium_centroids = np.array([info['centroid'] for info in medium_clusters.values()])
    medium_products_dummy = [{'product_name': info['name']} for info in medium_clusters.values()]

    large_clusters, large_labels = cluster_and_name(
        medium_centroids, N_LARGE_CATEGORIES, medium_products_dummy, "大分類"
    )

    # 階層マッピングを構築
    # small_id → medium_id のマッピング
    small_to_medium = {}
    for small_id, medium_id in enumerate(medium_labels):
        small_to_medium[small_id] = medium_id

    # medium_id → large_id のマッピング
    medium_to_large = {}
    for medium_id, large_id in enumerate(large_labels):
        medium_to_large[medium_id] = large_id

    return {
        'large': large_clusters,
        'medium': medium_clusters,
        'small': small_clusters,
        'small_to_medium': small_to_medium,
        'medium_to_large': medium_to_large,
        'small_labels': small_labels
    }


def register_hierarchy_to_db(hierarchy: Dict):
    """
    階層構造をMASTER_Categories_productに登録
    """
    print("\n=== データベースに階層を登録中 ===")

    # UUIDマッピング
    large_uuids = {}
    medium_uuids = {}
    small_uuids = {}

    # 名前重複チェック用
    used_names = set()

    def make_unique_name(name: str) -> str:
        """重複する名前に番号を付けてユニークにする"""
        if name not in used_names:
            used_names.add(name)
            return name

        counter = 2
        while f"{name}{counter}" in used_names:
            counter += 1
        unique_name = f"{name}{counter}"
        used_names.add(unique_name)
        return unique_name

    # 大分類を登録
    print("\n大分類を登録...")
    for large_id, info in hierarchy['large'].items():
        unique_name = make_unique_name(info['name'])
        result = db.table('MASTER_Categories_product').insert({
            'name': unique_name,
            'parent_id': None
        }).execute()
        large_uuids[large_id] = result.data[0]['id']
        print(f"  {unique_name}")

    # 中分類を登録
    print("\n中分類を登録...")
    for medium_id, info in hierarchy['medium'].items():
        large_id = hierarchy['medium_to_large'][medium_id]
        parent_uuid = large_uuids[large_id]

        unique_name = make_unique_name(info['name'])
        result = db.table('MASTER_Categories_product').insert({
            'name': unique_name,
            'parent_id': parent_uuid
        }).execute()
        medium_uuids[medium_id] = result.data[0]['id']
        print(f"  {unique_name} → {hierarchy['large'][large_id]['name']}")

    # 小分類を登録
    print("\n小分類を登録...")
    for small_id, info in hierarchy['small'].items():
        medium_id = hierarchy['small_to_medium'][small_id]
        parent_uuid = medium_uuids[medium_id]

        unique_name = make_unique_name(info['name'])
        result = db.table('MASTER_Categories_product').insert({
            'name': unique_name,
            'parent_id': parent_uuid
        }).execute()
        small_uuids[small_id] = result.data[0]['id']

        if small_id % 20 == 0:
            print(f"  進捗: {small_id}/{len(hierarchy['small'])}件")

    print(f"✅ 登録完了")
    print(f"  大分類: {len(large_uuids)}件")
    print(f"  中分類: {len(medium_uuids)}件")
    print(f"  小分類: {len(small_uuids)}件")

    return small_uuids


def assign_products_to_categories(products: List[Dict], hierarchy: Dict, small_uuids: Dict):
    """
    全商品にcategory_idを設定
    """
    print("\n=== 商品にカテゴリを割り当て中 ===")

    small_labels = hierarchy['small_labels']
    updated_count = 0

    for i, product in enumerate(products):
        small_id = small_labels[i]
        category_uuid = small_uuids[small_id]

        db.table('Rawdata_NETSUPER_items').update({
            'category_id': category_uuid
        }).eq('id', product['id']).execute()

        updated_count += 1

        if updated_count % 500 == 0:
            print(f"  進捗: {updated_count}/{len(products)}件")

    print(f"✅ 完了: {updated_count}件の商品を更新")


def main():
    """
    メイン処理
    """
    print("=" * 60)
    print("カテゴリ全上書き：自動階層分類")
    print("=" * 60)

    # 確認
    print("\n⚠️  警告:")
    print("- MASTER_Categories_productの全データを削除します")
    print("- 新しい階層構造を自動生成します")
    print("- 全商品のcategory_idを上書きします")
    print("- バックアップは自動的に作成されます")
    print("\n自動実行モード: 処理を開始します")

    # 1. バックアップ
    backup_path = backup_existing_categories()

    # 2. 既存カテゴリを削除
    clear_existing_categories()

    # 3. 埋め込みベクトルを取得
    products, embeddings = fetch_embeddings()

    if len(products) == 0:
        print("❌ 埋め込みベクトルが見つかりません")
        return

    # 4. 階層構造を構築
    hierarchy = build_hierarchy(products, embeddings)

    # 5. データベースに登録
    small_uuids = register_hierarchy_to_db(hierarchy)

    # 6. 商品にカテゴリを割り当て
    assign_products_to_categories(products, hierarchy, small_uuids)

    print("\n" + "=" * 60)
    print("✅ 全処理完了！")
    print("=" * 60)
    print(f"バックアップ: {backup_path}")
    print(f"大分類: {N_LARGE_CATEGORIES}件")
    print(f"中分類: {N_MEDIUM_CATEGORIES}件")
    print(f"小分類: {N_SMALL_CATEGORIES}件")
    print(f"商品: {len(products)}件に自動割り当て")


if __name__ == "__main__":
    main()
