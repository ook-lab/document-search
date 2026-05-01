"""
ネットスーパーカテゴリー実行スケジュール管理

カテゴリーごとに次回実行日時とインターバルを指定し、
実行すべきかどうかを判定する機能を提供します。
"""

import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from loguru import logger


class CategoryManager:
    """カテゴリーの実行スケジュールを管理"""

    def __init__(self, config_path: Optional[Path] = None):
        """
        Args:
            config_path: 設定ファイルのパス（指定しない場合はデフォルト）
        """
        if config_path is None:
            # デフォルトパス: B_ingestion/common/category_config.json
            self.config_path = Path(__file__).parent / "category_config.json"
        else:
            self.config_path = Path(config_path)

        self.config: Dict[str, List[Dict[str, Any]]] = {}
        self.load_config()

    def load_config(self):
        """設定ファイルを読み込む"""
        if self.config_path.exists():
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    self.config = json.load(f)
                logger.info(f"設定ファイルを読み込みました: {self.config_path}")
                # 旧フォーマットから新フォーマットへ自動変換
                self._migrate_old_format()
            except Exception as e:
                logger.error(f"設定ファイル読み込みエラー: {e}")
                self.config = {}
        else:
            logger.warning(f"設定ファイルが見つかりません: {self.config_path}")
            self.config = {}

    def _migrate_old_format(self):
        """旧フォーマット（next_run_datetime）から新フォーマット（start_date）へ変換"""
        migrated = False
        for store_name, categories in self.config.items():
            for category in categories:
                # next_run_datetimeがあり、start_dateがない場合
                if "next_run_datetime" in category and "start_date" not in category:
                    # next_run_datetime（日時）を start_date（日付のみ）に変換
                    next_run_datetime = category["next_run_datetime"]
                    # YYYY-MM-DD HH:MM から YYYY-MM-DD を抽出
                    category["start_date"] = next_run_datetime.split()[0] if " " in next_run_datetime else next_run_datetime
                    del category["next_run_datetime"]
                    migrated = True
                    logger.info(f"カテゴリー {category['name']} を新フォーマットに変換しました")

                # last_run_datetimeをlast_runに変換
                if "last_run_datetime" in category and "last_run" not in category:
                    last_run_datetime = category["last_run_datetime"]
                    if last_run_datetime:
                        category["last_run"] = last_run_datetime.split()[0] if " " in last_run_datetime else last_run_datetime
                    else:
                        category["last_run"] = None
                    del category["last_run_datetime"]
                    migrated = True

        if migrated:
            self.save_config()
            logger.info("設定ファイルを新フォーマットに変換しました")

    def save_config(self):
        """設定ファイルに保存"""
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
            logger.info(f"設定ファイルを保存しました: {self.config_path}")
        except Exception as e:
            logger.error(f"設定ファイル保存エラー: {e}")

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

        if store_name not in self.config:
            self.config[store_name] = []

        existing_names = {cat["name"] for cat in self.config[store_name]}

        for category in categories:
            if category["name"] not in existing_names:
                self.config[store_name].append({
                    "name": category["name"],
                    "url": category.get("url", ""),
                    "enabled": True,
                    "start_date": default_start_date,
                    "interval_days": default_interval_days,
                    "last_run": None,
                    "notes": ""
                })

        self.save_config()
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

        if store_name not in self.config:
            logger.warning(f"店舗 {store_name} が設定ファイルに存在しません")
            return False

        category = self._find_category(store_name, category_name)
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

        # 日付をパース
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

        category = self._find_category(store_name, category_name)
        if category is not None:
            # 最終実行日を記録
            category["last_run"] = run_datetime.strftime("%Y-%m-%d")

            # 次回開始日を計算: 実行日 + interval_days + 1
            interval_days = category.get("interval_days", 7)
            next_start_date = run_datetime.date() + timedelta(days=interval_days + 1)
            category["start_date"] = next_start_date.strftime("%Y-%m-%d")

            self.save_config()
            logger.info(f"カテゴリー {category_name} を実行済みとしてマークしました")
            logger.info(f"  最終実行: {category['last_run']}")
            logger.info(f"  次回開始日: {category['start_date']}")
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
        category = self._find_category(store_name, category_name)
        if category is None:
            return None

        return category.get("start_date")

    def _find_category(
        self,
        store_name: str,
        category_name: str
    ) -> Optional[Dict[str, Any]]:
        """カテゴリーを検索"""
        if store_name not in self.config:
            return None

        for category in self.config[store_name]:
            if category["name"] == category_name:
                return category

        return None

    def get_all_categories(self, store_name: str) -> List[Dict[str, Any]]:
        """店舗のすべてのカテゴリーを取得"""
        return self.config.get(store_name, [])

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
            updates: 更新する内容（enabled, next_run_datetime, interval_days など）
        """
        # 店舗が存在しない場合は作成
        if store_name not in self.config:
            self.config[store_name] = []

        category = self._find_category(store_name, category_name)
        if category is not None:
            # 既存カテゴリーを更新
            category.update(updates)
            self.save_config()
            logger.info(f"カテゴリー {category_name} を更新しました")
        else:
            # 新規カテゴリーを作成
            new_category = {
                "name": category_name,
                "url": updates.get("url", ""),
                "enabled": updates.get("enabled", True),
                "start_date": updates.get("start_date", datetime.now().strftime("%Y-%m-%d")),
                "interval_days": updates.get("interval_days", 7),
                "last_run": updates.get("last_run", None),
                "notes": updates.get("notes", "")
            }
            self.config[store_name].append(new_category)
            self.save_config()
            logger.info(f"✅ 新規カテゴリー {category_name} を追加しました（店舗: {store_name}）")


if __name__ == "__main__":
    # テスト実行
    manager = CategoryManager()

    # サンプルデータで初期化
    sample_categories = [
        {"name": "野菜", "url": "https://example.com/vegetables"},
        {"name": "果物", "url": "https://example.com/fruits"},
        {"name": "肉類", "url": "https://example.com/meat"},
    ]

    manager.initialize_store_categories(
        "rakuten_seiyu",
        sample_categories,
        default_interval_days=7,
        default_next_run="2025-12-25 01:00"
    )

    # 実行判定テスト
    print("野菜を実行すべきか:", manager.should_run_category("rakuten_seiyu", "野菜"))
    print("次回実行予定日時:", manager.get_next_run_datetime("rakuten_seiyu", "野菜"))
