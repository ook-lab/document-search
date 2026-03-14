"""
ハイブリッド検索（複数embedding + SQL検索）

検索方式:
1. general_name_embedding による類似度検索（重め: weight=0.4）
2. small_category_embedding による類似度検索（重め: weight=0.3）
3. keywords_embedding による類似度検索（軽め: weight=0.2）
4. SQL テキスト検索（LIKE/trigram）（weight=0.1）

最終スコア = 各検索結果の重み付き合計
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from typing import List, Dict, Optional
from openai import OpenAI
import json

# Windows環境でのUnicode出力設定
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# プロジェクトルートをパスに追加
root_dir = Path(__file__).parent.parent
sys.path.insert(0, str(root_dir))

load_dotenv(root_dir / ".env")

from shared.common.database.client import DatabaseClient


class HybridSearch:
    """ハイブリッド検索エンジン"""

    def __init__(self):
        # Supabase接続
        self.db = DatabaseClient(use_service_role=True)

        # OpenAI接続（クエリのembedding生成用）
        self.openai_api_key = os.getenv("OPENAI_API_KEY")
        if not self.openai_api_key:
            raise ValueError("環境変数 OPENAI_API_KEY を設定してください")

        self.client = OpenAI(api_key=self.openai_api_key)
        self.model = "text-embedding-3-small"

        # 重み設定（合計1.0）
        self.weights = {
            'general_name': 0.4,      # 重め
            'small_category': 0.3,     # 重め
            'keywords': 0.2,           # 軽め
            'text_search': 0.1         # SQL検索
        }

    def generate_query_embedding(self, query: str) -> List[float]:
        """
        検索クエリからembeddingを生成

        Args:
            query: 検索クエリ

        Returns:
            1536次元のベクトル
        """
        response = self.client.embeddings.create(
            model=self.model,
            input=query
        )
        return response.data[0].embedding

    def vector_search(
        self,
        query_embedding: List[float],
        embedding_column: str,
        limit: int = 100
    ) -> Dict[str, float]:
        """
        ベクトル類似度検索

        Args:
            query_embedding: クエリのembedding
            embedding_column: 検索対象のカラム名
            limit: 取得件数

        Returns:
            {product_id: similarity_score} の辞書
        """
        # embedding を文字列形式に変換
        embedding_str = '[' + ','.join(map(str, query_embedding)) + ']'

        # ベクトル類似度検索（コサイン類似度）
        # Supabaseのベクトル検索クエリ
        # 1 - (embedding <=> query) でコサイン類似度を計算（値が大きいほど類似）
        query = f"""
        SELECT
            id,
            1 - ({embedding_column} <=> '{embedding_str}'::halfvec) as similarity
        FROM "Rawdata_NETSUPER_items"
        WHERE {embedding_column} IS NOT NULL
        ORDER BY {embedding_column} <=> '{embedding_str}'::halfvec
        LIMIT {limit}
        """

        result = self.db.client.rpc('exec_sql', {'query': query}).execute()

        # 結果を辞書に変換
        scores = {}
        if result.data:
            for row in result.data:
                scores[row['id']] = float(row['similarity'])

        return scores

    def text_search(self, query: str, limit: int = 100) -> Dict[str, float]:
        """
        SQL テキスト検索（LIKE + trigram）

        Args:
            query: 検索クエリ
            limit: 取得件数

        Returns:
            {product_id: match_score} の辞書
        """
        # トライグラム類似度 + LIKE検索
        # similarity() は pg_trgm の関数（0〜1の類似度）
        sql_query = f"""
        SELECT
            id,
            GREATEST(
                similarity(product_name, '{query}'),
                similarity(general_name, '{query}'),
                CASE WHEN product_name ILIKE '%{query}%' THEN 0.5 ELSE 0 END,
                CASE WHEN general_name ILIKE '%{query}%' THEN 0.5 ELSE 0 END
            ) as match_score
        FROM "Rawdata_NETSUPER_items"
        WHERE
            product_name % '{query}'
            OR general_name % '{query}'
            OR product_name ILIKE '%{query}%'
            OR general_name ILIKE '%{query}%'
        ORDER BY match_score DESC
        LIMIT {limit}
        """

        try:
            result = self.db.client.rpc('exec_sql', {'query': sql_query}).execute()

            scores = {}
            if result.data:
                for row in result.data:
                    scores[row['id']] = float(row['match_score'])

            return scores
        except Exception as e:
            print(f"テキスト検索エラー: {e}")
            return {}

    def combine_scores(self, score_dicts: List[Dict[str, float]]) -> Dict[str, float]:
        """
        複数の検索結果を重み付けして統合

        Args:
            score_dicts: [
                {'weight': 0.4, 'scores': {id: score, ...}},
                {'weight': 0.3, 'scores': {id: score, ...}},
                ...
            ]

        Returns:
            {product_id: final_score} の辞書
        """
        combined = {}

        for item in score_dicts:
            weight = item['weight']
            scores = item['scores']

            for product_id, score in scores.items():
                if product_id not in combined:
                    combined[product_id] = 0.0
                combined[product_id] += weight * score

        return combined

    def search(
        self,
        query: str,
        top_k: int = 20,
        general_weight: float = None,
        category_weight: float = None,
        keywords_weight: float = None,
        text_weight: float = None
    ) -> List[Dict]:
        """
        ハイブリッド検索を実行（SQL関数を使用）

        Args:
            query: 検索クエリ
            top_k: 返す結果の件数
            general_weight: general_name重み（Noneの場合はデフォルト0.4）
            category_weight: small_category重み（Noneの場合はデフォルト0.3）
            keywords_weight: keywords重み（Noneの場合はデフォルト0.2）
            text_weight: text検索重み（Noneの場合はデフォルト0.1）

        Returns:
            検索結果のリスト（スコア順）
        """
        print(f"検索クエリ: {query}")
        print("="*80)

        # デフォルト重みを使用
        g_weight = general_weight if general_weight is not None else self.weights['general_name']
        c_weight = category_weight if category_weight is not None else self.weights['small_category']
        k_weight = keywords_weight if keywords_weight is not None else self.weights['keywords']
        t_weight = text_weight if text_weight is not None else self.weights['text_search']

        # クエリのembeddingを生成
        print("クエリのembedding生成中...")
        query_embedding = self.generate_query_embedding(query)

        # embedding を PostgreSQL vector 形式に変換
        embedding_str = '[' + ','.join(map(str, query_embedding)) + ']'

        # ハイブリッド検索SQL関数を呼び出し
        print("ハイブリッド検索実行中...")
        print(f"  重み: general={g_weight}, category={c_weight}, keywords={k_weight}, text={t_weight}")

        try:
            result = self.db.client.rpc('hybrid_search', {
                'query_embedding': embedding_str,
                'query_text': query,
                'match_count': top_k,
                'general_weight': g_weight,
                'category_weight': c_weight,
                'keywords_weight': k_weight,
                'text_weight': t_weight
            }).execute()

            if not result.data:
                print("検索結果: 0件")
                return []

            # 結果を整形
            products = []
            for row in result.data:
                product = {
                    'id': row['id'],
                    'product_name': row['product_name'],
                    'general_name': row['general_name'],
                    'small_category': row['small_category'],
                    'keywords': row['keywords'],
                    'organization': row['organization'],
                    'current_price': row['current_price'],
                    'search_score': row['final_score'],
                    'score_breakdown': {
                        'general': row['general_score'],
                        'category': row['category_score'],
                        'keywords': row['keywords_score'],
                        'text': row['text_score']
                    }
                }
                products.append(product)

            print(f"検索結果: {len(products)}件")
            return products

        except Exception as e:
            print(f"エラー: {e}")
            print("SQL関数 hybrid_search() が存在するか確認してください")
            return []


def main():
    """テスト実行"""
    import argparse

    parser = argparse.ArgumentParser(description='ハイブリッド検索テスト')
    parser.add_argument('query', type=str, help='検索クエリ')
    parser.add_argument('--top-k', type=int, default=10, help='表示件数')
    args = parser.parse_args()

    # 検索実行
    searcher = HybridSearch()
    results = searcher.search(args.query, top_k=args.top_k)

    # 結果表示
    print("\n" + "="*80)
    print(f"検索結果: {len(results)}件")
    print("="*80)

    for i, product in enumerate(results, 1):
        print(f"\n{i}. {product['product_name']}")
        print(f"   一般名: {product.get('general_name', 'N/A')}")
        print(f"   小分類: {product.get('small_category', 'N/A')}")
        print(f"   キーワード: {product.get('keywords', 'N/A')}")
        print(f"   価格: {product.get('current_price', 'N/A')}")
        print(f"   スコア: {product['search_score']:.4f}")

    print("\n" + "="*80)


if __name__ == "__main__":
    main()
