"""
共通商品取り込みパイプライン基盤クラス
全ネットスーパースクリプトで共有する処理を定義
"""

from abc import ABC, abstractmethod
from datetime import date, datetime
from typing import Dict, List, Optional, Set
from uuid import UUID
import uuid
import os

from shared.common.database.client import DatabaseClient
from shared.ai.llm_client.llm_client import LLMClient
from loguru import logger
from openai import OpenAI


class BaseProductIngestionPipeline(ABC):
    """商品取り込みの共通基盤クラス"""

    def __init__(self, organization_name: str, headless: bool = True):
        """
        初期化

        Args:
            organization_name: 組織名（東急ストア、楽天西友、ダイエー）
            headless: ブラウザのヘッドレスモード
        """
        self.organization_name = organization_name
        self.headless = headless
        self.db = DatabaseClient(use_service_role=True)
        self.llm_client = LLMClient()
        self.scraper = None

        # OpenAI clientの初期化（embedding生成用）
        openai_api_key = os.getenv("OPENAI_API_KEY")
        if openai_api_key:
            self.openai_client = OpenAI(api_key=openai_api_key)
            self.embedding_enabled = True
            logger.info("OpenAI Embedding機能が有効化されました")
        else:
            self.openai_client = None
            self.embedding_enabled = False
            logger.warning("OPENAI_API_KEYが設定されていません。Embedding生成は無効です")

    @abstractmethod
    async def start(self) -> bool:
        """
        スクレイパーの起動とログイン
        各ストア固有の実装が必要
        """
        pass

    @abstractmethod
    async def close(self):
        """スクレイパーのクリーンアップ"""
        pass

    async def check_existing_products(self, jan_codes: List[str]) -> Set[str]:
        """
        既存商品のチェック（JANコードで重複排除）

        Args:
            jan_codes: チェックするJANコードリスト

        Returns:
            既存のJANコードのセット
        """
        if not jan_codes:
            return set()

        # 空文字・Noneを除外
        valid_jan_codes = [code for code in jan_codes if code]

        if not valid_jan_codes:
            return set()

        result = self.db.client.table('Rawdata_NETSUPER_items').select(
            'jan_code'
        ).in_('jan_code', valid_jan_codes).execute()

        return {row['jan_code'] for row in result.data if row.get('jan_code')}

    async def check_existing_products_by_name(self, products: List[Dict]) -> Dict[tuple, str]:
        """
        既存商品のチェック（商品名+組織で重複排除）
        JANコードがない商品用

        Args:
            products: 商品データのリスト（product_nameとjan_codeを含む）

        Returns:
            (product_name, organization) -> product_id のマッピング
        """
        # JANコードがない商品のみを抽出
        no_jan_products = [p for p in products if not p.get('jan_code')]

        if not no_jan_products:
            return {}

        # 商品名のリストを作成
        product_names = list(set([p.get('product_name') for p in no_jan_products if p.get('product_name')]))

        if not product_names:
            return {}

        # 該当する商品名と組織の組み合わせで既存レコードを検索
        result = self.db.client.table('Rawdata_NETSUPER_items').select(
            'id, product_name, organization'
        ).in_('product_name', product_names).eq(
            'organization', self.organization_name
        ).is_('jan_code', 'null').execute()

        # (product_name, organization) -> id のマッピングを作成
        existing_map = {}
        for row in result.data:
            key = (row['product_name'], row['organization'])
            existing_map[key] = row['id']

        return existing_map

    def _generate_embedding(self, text: str) -> Optional[List[float]]:
        """
        商品名からembeddingを生成

        Args:
            text: 商品名

        Returns:
            1536次元のベクトル（失敗時はNone）
        """
        if not self.embedding_enabled or not self.openai_client:
            return None

        if not text:
            return None

        try:
            response = self.openai_client.embeddings.create(
                model="text-embedding-3-small",
                input=text
            )
            return response.data[0].embedding
        except Exception as e:
            logger.error(f"Embedding生成エラー: {e}")
            return None

    def _prepare_product_data(
        self,
        product: Dict,
        category_name: Optional[str] = None,
        general_name: Optional[str] = None,
        category_id: Optional[UUID] = None,
        confidence: Optional[float] = None
    ) -> Dict:
        """
        商品データの正規化と準備

        Args:
            product: スクレイパーから取得した生データ
            category_name: カテゴリ名（スクレイパー取得値）
            general_name: 一般名詞（分類済みの場合）
            category_id: カテゴリID（分類済みの場合）
            confidence: 分類信頼度

        Returns:
            データベース挿入用の正規化済みデータ
        """
        today = date.today()

        # 商品名を取得（サイト表記のまま保存）
        product_name = product.get("product_name", "")

        # 価格のパース（本体価格と税込価格の両方）
        # 本体価格（税抜）
        price = product.get("price")
        current_price = None
        if price is not None:
            try:
                if isinstance(price, (int, float)):
                    current_price = float(price)
                else:
                    current_price = float(str(price).replace(",", "").replace("円", "").replace("¥", "").strip())
            except (ValueError, AttributeError):
                current_price = None

        # 税込価格
        price_tax_included = product.get("price_tax_included")
        current_price_tax_included = None
        if price_tax_included is not None:
            try:
                if isinstance(price_tax_included, (int, float)):
                    current_price_tax_included = float(price_tax_included)
                else:
                    current_price_tax_included = float(str(price_tax_included).replace(",", "").replace("円", "").replace("¥", "").strip())
            except (ValueError, AttributeError):
                current_price_tax_included = None

        # price_text: 元のテキスト形式を保持（本体価格 / 税込価格）
        price_text_parts = []
        if price is not None:
            price_text_parts.append(f"本体¥{price}")
        if price_tax_included is not None:
            price_text_parts.append(f"税込¥{price_tax_included}")
        price_text = " / ".join(price_text_parts) if price_text_parts else ""

        # メタデータ
        metadata = {
            "raw_data": product,
            "scraping_timestamp": datetime.now().isoformat()
        }

        # データ構築
        data = {
            # 基本情報
            "source_type": "online_supermarket",
            "workspace": "shopping",
            "doc_type": "online_grocery_item",
            "organization": self.organization_name,

            # 商品情報
            "product_name": product_name,
            "jan_code": product.get("jan_code"),

            # 価格情報
            "current_price": current_price,
            "current_price_tax_included": current_price_tax_included,
            "price_text": price_text,

            # 分類情報
            "category": category_name,
            "general_name": general_name,
            "category_id": str(category_id) if category_id else None,
            "classification_confidence": confidence,
            "needs_approval": general_name is None,  # 未分類の場合は承認待ち

            # その他
            "manufacturer": product.get("manufacturer"),
            "image_url": product.get("image_url"),
            "in_stock": product.get("in_stock", True),
            "is_available": product.get("is_available", True),

            # メタデータ
            "metadata": metadata,
            "document_date": today.isoformat(),
            "last_scraped_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat()
        }

        # 複数Embeddingを生成（ハイブリッド検索用）
        # 1. general_name_embedding
        if general_name:
            general_name_emb = self._generate_embedding(general_name)
            if general_name_emb:
                data["general_name_embedding"] = '[' + ','.join(map(str, general_name_emb)) + ']'
                logger.debug(f"general_name_embedding生成成功: {general_name[:30]}...")

        # 2. small_category_embedding
        if category_name:
            category_emb = self._generate_embedding(category_name)
            if category_emb:
                data["small_category_embedding"] = '[' + ','.join(map(str, category_emb)) + ']'
                logger.debug(f"small_category_embedding生成成功: {category_name[:30]}...")

        # 3. keywords_embedding
        keywords = product.get("keywords")
        if keywords:
            # キーワードを文字列に変換
            if isinstance(keywords, list):
                keywords_text = " ".join(str(k) for k in keywords if k)
            else:
                keywords_text = str(keywords)

            if keywords_text.strip():
                keywords_emb = self._generate_embedding(keywords_text)
                if keywords_emb:
                    data["keywords_embedding"] = '[' + ','.join(map(str, keywords_emb)) + ']'
                    logger.debug(f"keywords_embedding生成成功: {keywords_text[:30]}...")

        return data

    async def process_category_page(
        self,
        category_url: str,
        page: int = 1,
        category_name: Optional[str] = None
    ) -> Dict:
        """
        カテゴリページの処理（共通ロジック）

        Args:
            category_url: カテゴリURL
            page: ページ番号
            category_name: カテゴリ名

        Returns:
            処理結果
        """
        if not self.scraper:
            raise RuntimeError("Scraper not initialized. Call start() first.")

        logger.info(f"ページ {page} を処理中...")

        # スクレイパー固有の実装を呼び出し（タプル返却）
        products, pagination_info = await self.scraper.fetch_products_page(category_url, page)

        # ページネーション情報をログ出力
        if page == 1 and pagination_info:
            logger.info(f"ページネーション情報: {pagination_info}")

        if not products:
            logger.warning("商品が見つかりませんでした")
            return {
                "success": True,
                "total_products": 0,
                "new_products": 0,
                "updated_products": 0,
                "pagination_info": pagination_info
            }

        # 商品データの正規化と保存
        jan_codes = [p.get("jan_code") for p in products if p.get("jan_code")]
        existing_jan_codes = await self.check_existing_products(jan_codes)

        # JANコードがない商品の既存チェック（商品名+組織）
        existing_by_name = await self.check_existing_products_by_name(products)

        insert_count = 0
        update_count = 0

        for product in products:
            # 商品データ準備（分類は後で実施）
            product_data = self._prepare_product_data(product, category_name)
            jan_code = product.get("jan_code")
            product_name = product.get("product_name")

            try:
                if jan_code and jan_code in existing_jan_codes:
                    # JANコードで既存商品を更新
                    # general_name と keywords は除外（AI生成済みデータを保持）
                    update_data = {k: v for k, v in product_data.items() if k not in ['general_name', 'keywords']}
                    self.db.client.table('Rawdata_NETSUPER_items').update(
                        update_data
                    ).eq('jan_code', jan_code).execute()
                    update_count += 1
                elif not jan_code and (product_name, self.organization_name) in existing_by_name:
                    # JANコードなし商品を商品名+組織で更新
                    # general_name と keywords は除外（AI生成済みデータを保持）
                    existing_id = existing_by_name[(product_name, self.organization_name)]
                    update_data = {k: v for k, v in product_data.items() if k not in ['general_name', 'keywords']}
                    self.db.client.table('Rawdata_NETSUPER_items').update(
                        update_data
                    ).eq('id', existing_id).execute()
                    update_count += 1
                else:
                    # 新規商品を挿入
                    product_data["created_at"] = datetime.now().isoformat()
                    self.db.client.table('Rawdata_NETSUPER_items').insert(
                        product_data
                    ).execute()
                    insert_count += 1

            except Exception as e:
                logger.error(f"Failed to save product {product_name}: {e}")

        logger.info(f"✅ 処理完了: 合計{len(products)}件（新規{insert_count}件、更新{update_count}件）")

        return {
            "success": True,
            "total_products": len(products),
            "new_products": insert_count,
            "updated_products": update_count,
            "pagination_info": pagination_info
        }

    async def process_category_all_pages(
        self,
        category_url: str,
        category_name: Optional[str] = None,
        max_pages: int = 100
    ) -> Dict:
        """
        カテゴリの全ページを処理

        Args:
            category_url: カテゴリURL
            category_name: カテゴリ名
            max_pages: 最大ページ数

        Returns:
            処理結果
        """
        logger.info(f"カテゴリー '{category_name}' の全ページ処理開始")

        page = 1
        total_products = 0
        total_new = 0
        total_updated = 0
        total_pages_from_pagination = None

        while page <= max_pages:
            result = await self.process_category_page(category_url, page, category_name)

            # 初回のページネーション情報を取得
            if page == 1 and result.get("pagination_info"):
                pagination = result["pagination_info"]
                total_pages_from_pagination = pagination.get("totalPages")
                if total_pages_from_pagination:
                    logger.info(f"📄 検出された総ページ数: {total_pages_from_pagination}")
                    # 総ページ数がmax_pagesより少ない場合、max_pagesを更新
                    if total_pages_from_pagination < max_pages:
                        max_pages = total_pages_from_pagination
                        logger.info(f"✅ 処理ページ数を {total_pages_from_pagination} に制限")

            if not result.get("success") or result.get("total_products", 0) == 0:
                logger.info(f"ページ {page} で商品なし、カテゴリー処理終了")
                break

            total_products += result.get("total_products", 0)
            total_new += result.get("new_products", 0)
            total_updated += result.get("updated_products", 0)

            # ページネーション情報に基づく終了判定
            if total_pages_from_pagination and page >= total_pages_from_pagination:
                logger.info(f"✅ 全{total_pages_from_pagination}ページの処理完了")
                break

            page += 1

        logger.info(f"✅ カテゴリー '{category_name}' 完了")
        logger.info(f"   合計: {total_products}件（新規{total_new}件、更新{total_updated}件）")

        return {
            "success": True,
            "category_url": category_url,
            "category_name": category_name,
            "total_products": total_products,
            "new_products": total_new,
            "updated_products": total_updated,
            "pages_processed": page - 1,
            "total_pages": total_pages_from_pagination
        }
