"""
既存商品データをOpenAIでベクトル化

Rawdata_NETSUPER_itemsテーブルの商品名をOpenAI APIでベクトル化し、
general_name_embeddingカラムに保存します。

使用モデル: text-embedding-3-small (1536次元)

注意: ハイブリッド検索（複数フィールドのembedding）が必要な場合は
generate_multi_embeddings.py を使用してください。
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv
import time
from typing import List, Dict
from openai import OpenAI

# Windows環境でのUnicode出力設定
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# プロジェクトルートをパスに追加
root_dir = Path(__file__).parent.parent
sys.path.insert(0, str(root_dir))

load_dotenv(root_dir / ".env")

from supabase import create_client
from shared.common.database.client import DatabaseClient

# ロギング設定
try:
    from loguru import logger
    logger.remove()
    logger.add(sys.stdout, format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>")
except ImportError:
    import logging
    logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)-8s | %(message)s', datefmt='%H:%M:%S')
    logger = logging.getLogger(__name__)


class ProductEmbeddingGenerator:
    """商品データのベクトル化"""

    def __init__(self):
        # Supabase接続（service roleキーを使用）
        self.db = DatabaseClient(use_service_role=True)

        # OpenAI接続
        self.openai_api_key = os.getenv("OPENAI_API_KEY")
        if not self.openai_api_key:
            raise ValueError("環境変数 OPENAI_API_KEY を設定してください")

        self.client = OpenAI(api_key=self.openai_api_key)
        self.model = "text-embedding-3-small"  # 1536次元

    def fetch_products_without_embedding(self, limit: int = None) -> List[Dict]:
        """
        embeddingがない商品を取得

        Args:
            limit: 取得する最大件数（Noneの場合は全件）

        Returns:
            商品データのリスト
        """
        logger.info("general_name_embeddingがない商品を取得中...")

        query = self.db.client.table('Rawdata_NETSUPER_items').select('id, product_name, general_name, small_category, keywords').is_('general_name_embedding', 'null')

        if limit:
            query = query.limit(limit)

        result = query.execute()

        logger.info(f"取得完了: {len(result.data)}件の商品データ")
        return result.data

    def generate_embedding(self, text: str) -> List[float]:
        """
        テキストからembeddingを生成

        Args:
            text: 商品名

        Returns:
            1536次元のベクトル
        """
        response = self.client.embeddings.create(
            model=self.model,
            input=text
        )
        return response.data[0].embedding

    def update_product_embedding(self, product_id: str, embedding: List[float]):
        """
        商品のgeneral_name_embeddingを更新

        Args:
            product_id: 商品ID
            embedding: 埋め込みベクトル
        """
        # vector型として保存するために文字列形式に変換
        embedding_str = '[' + ','.join(map(str, embedding)) + ']'
        self.db.client.table('Rawdata_NETSUPER_items').update({
            'general_name_embedding': embedding_str
        }).eq('id', product_id).execute()

    def process_products(self, batch_size: int = 100, limit: int = None, delay: float = 0.1):
        """
        商品データをバッチ処理でベクトル化

        Args:
            batch_size: バッチサイズ
            limit: 処理する最大件数（Noneの場合は全件）
            delay: API呼び出し間の待機時間（秒）
        """
        logger.info("="*80)
        logger.info("商品データのベクトル化開始")
        logger.info("="*80)

        # embeddingがない商品を取得
        products = self.fetch_products_without_embedding(limit=limit)

        if not products:
            logger.info("ベクトル化が必要な商品がありません")
            return

        stats = {
            'total': len(products),
            'processed': 0,
            'error': 0
        }

        # バッチ処理
        for i, product in enumerate(products, 1):
            try:
                product_id = product['id']
                product_name = product['product_name']

                if not product_name:
                    logger.warning(f"[{i}/{stats['total']}] 商品名が空のためスキップ: ID={product_id}")
                    stats['error'] += 1
                    continue

                # embeddingを生成
                embedding = self.generate_embedding(product_name)

                # データベースに保存
                self.update_product_embedding(product_id, embedding)

                logger.info(f"[{i}/{stats['total']}] 完了: {product_name}")
                stats['processed'] += 1

                # 進捗表示
                if i % 50 == 0:
                    logger.info(f"進捗: {i}/{stats['total']} ({i/stats['total']*100:.1f}%)")

                # レート制限対策
                time.sleep(delay)

            except Exception as e:
                logger.error(f"[{i}/{stats['total']}] エラー: {product.get('product_name', '不明')} - {e}")
                stats['error'] += 1
                time.sleep(1)  # エラー時は少し長めに待機

        # 結果サマリー
        logger.info("="*80)
        logger.info("ベクトル化完了")
        logger.info("="*80)
        logger.info(f"処理件数: {stats['total']}件")
        logger.info(f"成功:     {stats['processed']}件")
        logger.info(f"エラー:   {stats['error']}件")
        logger.info("="*80)


def main():
    """メイン処理"""
    import argparse

    parser = argparse.ArgumentParser(description='商品データをOpenAIでベクトル化')
    parser.add_argument('--limit', type=int, help='処理する最大件数', default=None)
    parser.add_argument('--batch-size', type=int, help='バッチサイズ', default=100)
    parser.add_argument('--delay', type=float, help='API呼び出し間の待機時間（秒）', default=0.1)
    args = parser.parse_args()

    generator = ProductEmbeddingGenerator()
    generator.process_products(
        batch_size=args.batch_size,
        limit=args.limit,
        delay=args.delay
    )


if __name__ == "__main__":
    main()
