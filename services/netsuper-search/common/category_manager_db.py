"""
ネットスーパーカテゴリー実行スケジュール管理（Supabaseベース）

カテゴリーごとに次回実行日時とインターバルを指定し、
実行すべきかどうかを判定する機能を提供します。
Streamlit Cloud対応のため、設定をSupabaseテーブルに保存します。
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from loguru import logger
from shared.common.database.client import DatabaseClient


class CategoryManagerDB:
    """カテゴリーの実行スケジュールを管理（Supabaseベース）"""

    def __init__(self):
        """初期化"""
        self.db = DatabaseClient(use_service_role=True)
        self.table_name = "99_lg_scraping_schedule"

    def load_config_from_db(self, store_name: str) -> List[Dict[str, Any]]:
        """
        Supabaseから設定を読み込む

        Args:
            store_name: 店舗名

        Returns:
            カテゴリーリスト
        """
        try:
            result = self.db.client.table(self.table_name).select(
                '*'
            ).eq('store_name', store_name).execute()

            return result.data
        except Exception as e:
            logger.error(f"設定読み込みエラー: {e}")
            return []

    def save_category_to_db(
        self,
        store_name: str,
        category_name: str,
        updates: Dict[str, Any]
    ):
        """
        カテゴリー設定をSupabaseに保存（upsert）

        Args:
            store_name: 店舗名
            category_name: カテゴリー名
            updates: 更新内容
        """
        try:
            # 既存レコードを検索
            existing = self.db.client.table(self.table_name).select(
                'id'
            ).eq('store_name', store_name).eq('category_name', category_name).execute()

            data = {
                'store_name': store_name,
                'category_name': category_name,
                **updates
            }

            if existing.data:
                # 更新
                self.db.client.table(self.table_name).update(
                    updates
                ).eq('store_name', store_name).eq('category_name', category_name).execute()
                logger.info(f"カテゴリー {category_name} を更新しました")
            else:
                # 新規作成
                self.db.client.table(self.table_name).insert(data).execute()
                logger.info(f"✅ 新規カテゴリー {category_name} を追加しました（店舗: {store_name}）")

        except Exception as e:
            logger.error(f"カテゴリー保存エラー: {e}")

    def initialize_store_categories(
        self,
        store_name: str,
        categories: List[Dict[str, str]],
        default_interval_days: int = 7,
        default_start_date: Optional[str] = None
    ):
        """
        店舗のカテゴリーを初期化

        Args:
            store_name: 店舗名（例: "rakuten_seiyu"）
            categories: カテゴリーリスト [{"name": "xxx", "url": "xxx"}, ...]
            default_interval_days: デフォルトのインターバル日数
            default_start_date: デフォルトの開始日（YYYY-MM-DD）、指定しない場合は明日
        """
        if default_start_date is None:
            # デフォルト: 明日
            tomorrow = datetime.now() + timedelta(days=1)
            default_start_date = tomorrow.strftime("%Y-%m-%d")

        # 既存カテゴリーを取得
        existing_categories = self.load_config_from_db(store_name)
        existing_names = {cat["category_name"] for cat in existing_categories}

        for category in categories:
            if category["name"] not in existing_names:
                self.save_category_to_db(
                    store_name,
                    category["name"],
                    {
                        "url": category.get("url", ""),
                        "enabled": True,
                        "start_date": default_start_date,
                        "interval_days": default_interval_days,
                        "last_run": None,
                        "notes": ""
                    }
                )

        logger.info(f"{store_name} のカテゴリーを初期化しました（{len(categories)}件）")

    def should_run_category(
        self,
        store_name: str,
        category_name: str,
        now: Optional[datetime] = None
    ) -> bool:
        """
        カテゴリーを今実行すべきかどうかを判定

        ロジック: 現在日付 >= start_date なら True

        Args:
            store_name: 店舗名
            category_name: カテゴリー名
            now: 現在時刻（指定しない場合は現在時刻）

        Returns:
            実行すべきなら True
        """
        if now is None:
            now = datetime.now()

        category = self._find_category_in_db(store_name, category_name)
        if category is None:
            logger.warning(f"カテゴリー {category_name} が見つかりません（店舗: {store_name}）")
            return False

        # 無効化されている場合はスキップ
        if not category.get("enabled", True):
            logger.info(f"カテゴリー {category_name} は無効化されています")
            return False

        # 開始日を取得
        start_date_str = category.get("start_date")
        if not start_date_str:
            logger.warning(f"カテゴリー {category_name} の開始日が設定されていません")
            return False

        # 日付をパース（DATE型なので文字列で返ってくる）
        try:
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        except ValueError as e:
            logger.error(f"開始日のパースエラー: {start_date_str}, {e}")
            return False

        # 判定（日付のみで比較）
        should_run = now.date() >= start_date
        if should_run:
            logger.info(f"カテゴリー {category_name}: 実行可能（開始日: {start_date_str}）")

        return should_run

    def mark_as_run(
        self,
        store_name: str,
        category_name: str,
        run_datetime: Optional[datetime] = None
    ):
        """
        カテゴリーを実行済みとしてマークし、次回開始日を更新

        次回開始日 = 実行日 + interval_days + 1日

        Args:
            store_name: 店舗名
            category_name: カテゴリー名
            run_datetime: 実行日時（指定しない場合は現在時刻）
        """
        if run_datetime is None:
            run_datetime = datetime.now()

        category = self._find_category_in_db(store_name, category_name)
        if category is not None:
            # 最終実行日を記録
            last_run_date = run_datetime.strftime("%Y-%m-%d")

            # 次回開始日を計算: 実行日 + interval_days + 1
            interval_days = category.get("interval_days", 7)
            next_start_date = run_datetime.date() + timedelta(days=interval_days + 1)
            next_start_date_str = next_start_date.strftime("%Y-%m-%d")

            # Supabaseを更新
            self.save_category_to_db(
                store_name,
                category_name,
                {
                    "last_run": last_run_date,
                    "start_date": next_start_date_str
                }
            )

            logger.info(f"カテゴリー {category_name} を実行済みとしてマークしました")
            logger.info(f"  最終実行: {last_run_date}")
            logger.info(f"  次回開始日: {next_start_date_str}")
        else:
            logger.warning(f"カテゴリー {category_name} が見つかりません（店舗: {store_name}）")

    def get_start_date(
        self,
        store_name: str,
        category_name: str
    ) -> Optional[str]:
        """
        カテゴリーの開始日を取得

        Returns:
            開始日（YYYY-MM-DD形式）、または None
        """
        category = self._find_category_in_db(store_name, category_name)
        if category is None:
            return None

        return category.get("start_date")

    def _find_category_in_db(
        self,
        store_name: str,
        category_name: str
    ) -> Optional[Dict[str, Any]]:
        """Supabaseからカテゴリーを検索"""
        try:
            result = self.db.client.table(self.table_name).select(
                '*'
            ).eq('store_name', store_name).eq('category_name', category_name).execute()

            if result.data:
                return result.data[0]
            return None
        except Exception as e:
            logger.error(f"カテゴリー検索エラー: {e}")
            return None

    def get_all_categories(self, store_name: str) -> List[Dict[str, Any]]:
        """店舗のすべてのカテゴリーを取得"""
        return self.load_config_from_db(store_name)

    def update_category(
        self,
        store_name: str,
        category_name: str,
        updates: Dict[str, Any]
    ):
        """
        カテゴリー情報を更新（存在しない場合は新規作成）

        Args:
            store_name: 店舗名
            category_name: カテゴリー名
            updates: 更新する内容（enabled, start_date, interval_days など）
        """
        self.save_category_to_db(store_name, category_name, updates)


if __name__ == "__main__":
    # テスト実行
    manager = CategoryManagerDB()

    # サンプルデータで初期化
    sample_categories = [
        {"name": "野菜", "url": "https://example.com/vegetables"},
        {"name": "果物", "url": "https://example.com/fruits"},
        {"name": "肉類", "url": "https://example.com/meat"},
    ]

    manager.initialize_store_categories(
        "rakuten_seiyu",
        sample_categories,
        default_interval_days=7
    )

    # 実行判定テスト
    print("野菜を実行すべきか:", manager.should_run_category("rakuten_seiyu", "野菜"))
    print("開始日:", manager.get_start_date("rakuten_seiyu", "野菜"))
