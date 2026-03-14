"""
複数embeddingの生成（ハイブリッド検索用）

Rawdata_NETSUPER_itemsの各フィールドを個別にベクトル化:
1. general_name_embedding (重め)
2. small_category_embedding (重め)
3. keywords_embedding (軽め)

使用モデル: text-embedding-3-small (1536次元)
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv
import time
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

# ロギング設定
try:
    from loguru import logger
    logger.remove()
    logger.add(sys.stdout, format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>")
except ImportError:
    import logging
    logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)-8s | %(message)s', datefmt='%H:%M:%S')
    logger = logging.getLogger(__name__)


class MultiEmbeddingGenerator:
    """ハイブリッド検索用の複数embedding生成"""

    def __init__(self):
        # Supabase接続（service roleキーを使用）
        self.db = DatabaseClient(use_service_role=True)

        # OpenAI接続
        self.openai_api_key = os.getenv("OPENAI_API_KEY")
        if not self.openai_api_key:
            raise ValueError("環境変数 OPENAI_API_KEY を設定してください")

        self.client = OpenAI(api_key=self.openai_api_key)
        self.model = "text-embedding-3-small"  # 1536次元

    def fetch_products_without_embeddings(self, limit: int = None) -> List[Dict]:
        """
        embeddingがない商品を取得

        Args:
            limit: 取得する最大件数（Noneの場合は全件）

        Returns:
            商品データのリスト
        """
        logger.info("embeddingが未生成の商品を取得中...")

        query = self.db.client.table('Rawdata_NETSUPER_items').select(
            'id, product_name, general_name, small_category, keywords'
        ).is_('general_name_embedding', 'null')

        if limit:
            query = query.limit(limit)

        result = query.execute()

        logger.info(f"取得完了: {len(result.data)}件の商品データ")
        return result.data

    def generate_embedding(self, text: str) -> Optional[List[float]]:
        """
        テキストからembeddingを生成

        Args:
            text: 入力テキスト

        Returns:
            1536次元のベクトル（テキストが空の場合はNone）
        """
        if not text or not text.strip():
            return None

        response = self.client.embeddings.create(
            model=self.model,
            input=text.strip()
        )
        return response.data[0].embedding

    def generate_keywords_embedding(self, keywords) -> Optional[List[float]]:
        """
        keywords配列からembeddingを生成

        Args:
            keywords: キーワード（配列またはJSON文字列）

        Returns:
            1536次元のベクトル（キーワードが空の場合はNone）
        """
        # キーワードの解析
        keyword_list = []

        if isinstance(keywords, str):
            try:
                keyword_list = json.loads(keywords)
            except:
                # JSON解析失敗時はスペース区切りとして扱う
                keyword_list = keywords.split()
        elif isinstance(keywords, list):
            keyword_list = keywords
        else:
            return None

        if not keyword_list:
            return None

        # キーワードを結合してembedding生成
        keywords_text = " ".join(str(k) for k in keyword_list if k)
        return self.generate_embedding(keywords_text)

    def update_product_embeddings(
        self,
        product_id: str,
        general_name_embedding: Optional[List[float]],
        small_category_embedding: Optional[List[float]],
        keywords_embedding: Optional[List[float]]
    ):
        """
        商品の3つのembeddingを更新

        Args:
            product_id: 商品ID
            general_name_embedding: general_name用embedding
            small_category_embedding: small_category用embedding
            keywords_embedding: keywords用embedding
        """
        update_data = {}

        # vector型として保存するために文字列形式に変換
        if general_name_embedding:
            update_data['general_name_embedding'] = '[' + ','.join(map(str, general_name_embedding)) + ']'

        if small_category_embedding:
            update_data['small_category_embedding'] = '[' + ','.join(map(str, small_category_embedding)) + ']'

        if keywords_embedding:
            update_data['keywords_embedding'] = '[' + ','.join(map(str, keywords_embedding)) + ']'

        if update_data:
            self.db.client.table('Rawdata_NETSUPER_items').update(
                update_data
            ).eq('id', product_id).execute()

    def process_products(self, batch_size: int = 100, limit: int = None, delay: float = 0.1):
        """
        商品データをバッチ処理で複数embeddingを生成

        Args:
            batch_size: バッチサイズ
            limit: 処理する最大件数（Noneの場合は全件）
            delay: API呼び出し間の待機時間（秒）
        """
        logger.info("="*80)
        logger.info("複数embedding生成開始（ハイブリッド検索用）")
        logger.info("="*80)

        # embeddingがない商品を取得
        products = self.fetch_products_without_embeddings(limit=limit)

        if not products:
            logger.info("ベクトル化が必要な商品がありません")
            return

        stats = {
            'total': len(products),
            'processed': 0,
            'error': 0,
            'general_name_count': 0,
            'small_category_count': 0,
            'keywords_count': 0
        }

        # バッチ処理
        for i, product in enumerate(products, 1):
            try:
                product_id = product['id']
                product_name = product.get('product_name', '')
                general_name = product.get('general_name', '')
                small_category = product.get('small_category', '')
                keywords = product.get('keywords')

                logger.info(f"[{i}/{stats['total']}] 処理中: {product_name[:40]}")

                # 1. general_name embedding（重め）
                general_name_emb = None
                if general_name:
                    general_name_emb = self.generate_embedding(general_name)
                    if general_name_emb:
                        stats['general_name_count'] += 1
                        logger.info(f"  ✓ general_name: {general_name}")

                # 2. small_category embedding（重め）
                small_category_emb = None
                if small_category:
                    small_category_emb = self.generate_embedding(small_category)
                    if small_category_emb:
                        stats['small_category_count'] += 1
                        logger.info(f"  ✓ small_category: {small_category}")

                # 3. keywords embedding（軽め）
                keywords_emb = None
                if keywords:
                    keywords_emb = self.generate_keywords_embedding(keywords)
                    if keywords_emb:
                        stats['keywords_count'] += 1
                        logger.info(f"  ✓ keywords: {keywords}")

                # データベースに保存
                self.update_product_embeddings(
                    product_id,
                    general_name_emb,
                    small_category_emb,
                    keywords_emb
                )

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
        logger.info("複数embedding生成完了")
        logger.info("="*80)
        logger.info(f"処理件数:              {stats['total']}件")
        logger.info(f"成功:                  {stats['processed']}件")
        logger.info(f"エラー:                {stats['error']}件")
        logger.info(f"general_name生成:      {stats['general_name_count']}件")
        logger.info(f"small_category生成:    {stats['small_category_count']}件")
        logger.info(f"keywords生成:          {stats['keywords_count']}件")
        logger.info("="*80)


def main():
    """メイン処理"""
    import argparse

    parser = argparse.ArgumentParser(description='複数embeddingを生成（ハイブリッド検索用）')
    parser.add_argument('--limit', type=int, help='処理する最大件数', default=None)
    parser.add_argument('--batch-size', type=int, help='バッチサイズ', default=100)
    parser.add_argument('--delay', type=float, help='API呼び出し間の待機時間（秒）', default=0.1)
    args = parser.parse_args()

    generator = MultiEmbeddingGenerator()
    generator.process_products(
        batch_size=args.batch_size,
        limit=args.limit,
        delay=args.delay
    )


if __name__ == "__main__":
    main()
