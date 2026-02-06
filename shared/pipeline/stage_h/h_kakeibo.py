"""
Stage H: Kakeibo Structuring (家計簿構造化)

家計簿レシート専用のStage H処理
- 税額按分計算
- 商品分類
- マスタデータとの紐付け
"""

from typing import Dict, Any, List
from loguru import logger
from datetime import date

from shared.common.database.client import DatabaseClient


class StageHKakeibo:
    """家計簿専用のStage H（税額按分・分類）"""

    def __init__(self, db_client: DatabaseClient):
        """
        Args:
            db_client: データベースクライアント
        """
        self.db = db_client

        # マスタデータをロード
        self.aliases = self._load_aliases()
        self.product_dict = self._load_product_dictionary()
        self.situations = self._load_situations()
        self.categories = self._load_categories()

    def process(
        self,
        stage_g_output: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Stage Gの出力から最終データを生成

        Args:
            stage_g_output: Stage Gの構造化データ

        Returns:
            Dict: 最終データ（DB保存可能な形式）
        """
        try:
            # 【重要】レシート記載の金額は改ざんしない
            # 割引行は別行としてそのまま保持（マイナス金額）
            items = stage_g_output.get("items", [])

            # 割引を商品にリンク（税込価計算用）
            items = self._link_discounts_to_items(items)

            # 1. 商品を正規化（マスタとの紐付け）
            normalized_items = []
            for item in items:
                # 値引き行も含めて処理（金額はそのまま）
                if item.get("line_type") == "DISCOUNT":
                    normalized_items.append({
                        "raw_item": item,
                        "normalized": {
                            "product_name": item.get("product_name", "値引"),
                            "category_id": None,
                            "tax_rate": self._get_discount_tax_rate(item, items),
                            "tax_rate_source": "discount",
                            "is_discount": True
                        }
                    })
                    continue

                normalized = self._normalize_item(
                    item,
                    stage_g_output["shop_info"]["name"],
                    stage_g_output.get("amounts", {})
                )
                normalized_items.append({
                    "raw_item": item,
                    "normalized": normalized
                })

            # 2. シチュエーション判定
            trans_date = date.fromisoformat(stage_g_output["transaction_info"]["date"])
            situation_id = self._determine_situation(trans_date)

            # 3. 税額を按分計算
            items_with_tax, tax_subtotals = self._calculate_and_distribute_tax(
                normalized_items,
                stage_g_output.get("amounts", {})
            )

            # 4. 最終データを構築
            result = {
                "receipt": {
                    **stage_g_output["shop_info"],
                    **stage_g_output["transaction_info"],
                    **stage_g_output.get("amounts", {}),
                    **tax_subtotals,  # 税対象額を追加
                    "situation_id": situation_id
                },
                "items": items_with_tax,
                "payment": stage_g_output.get("payment", {}),
                "other_info": stage_g_output.get("other_info", {})
            }

            logger.info(f"Stage H completed: {len(items_with_tax)} items processed")
            return result

        except Exception as e:
            logger.error(f"Stage H failed: {e}")
            raise

    def _get_discount_tax_rate(self, discount_item: Dict, all_items: List[Dict]) -> int:
        """
        割引行の税率を判定（適用先商品から推定）

        Args:
            discount_item: 割引行データ
            all_items: 全明細行リスト

        Returns:
            int: 税率（8 or 10）
        """
        # 行番号でインデックスを作成
        items_by_line = {item.get("line_number"): item for item in all_items}

        # 明示的に値引き適用先が指定されている場合
        applied_to_line = discount_item.get("discount_applied_to")
        if applied_to_line and applied_to_line in items_by_line:
            target = items_by_line[applied_to_line]
            tax_mark = target.get("tax_mark")
            if tax_mark and (
                tax_mark in ["*", "※", "◆"] or
                "8%" in str(tax_mark) or
                "8" in str(tax_mark)
            ):
                return 8
            return 10

        # 直前の商品から推定
        discount_line_num = discount_item.get("line_number")
        if discount_line_num:
            for i in range(discount_line_num - 1, 0, -1):
                if i in items_by_line and items_by_line[i].get("line_type") != "DISCOUNT":
                    target = items_by_line[i]
                    tax_mark = target.get("tax_mark")
                    if tax_mark and (
                        tax_mark in ["*", "※", "◆"] or
                        "8%" in str(tax_mark) or
                        "8" in str(tax_mark)
                    ):
                        return 8
                    return 10

        # デフォルト10%
        return 10

    def _link_discounts_to_items(self, items: List[Dict]) -> List[Dict]:
        """
        割引行を商品にリンク（税込価計算用）

        各商品に linked_discount フィールドを追加
        割引の適用先が明示されていない場合は直前の商品に適用

        Args:
            items: Stage Gで抽出された全明細行

        Returns:
            List[Dict]: リンク情報が追加された明細行リスト
        """
        # 行番号でインデックスを作成
        items_by_line = {item.get("line_number"): item for item in items}

        # 各商品のlinked_discountを初期化
        for item in items:
            if item.get("line_type") != "DISCOUNT":
                item["linked_discount"] = 0

        # 割引を適用先にリンク
        for item in items:
            if item.get("line_type") != "DISCOUNT":
                continue

            discount_amount = item.get("amount", 0)  # 負の値
            applied_to_line = item.get("discount_applied_to")

            target = None
            if applied_to_line and applied_to_line in items_by_line:
                # 明示的に適用先が指定されている場合
                target = items_by_line[applied_to_line]
            else:
                # 直前の商品を探す
                discount_line_num = item.get("line_number")
                if discount_line_num:
                    for i in range(discount_line_num - 1, 0, -1):
                        if i in items_by_line and items_by_line[i].get("line_type") != "DISCOUNT":
                            target = items_by_line[i]
                            break

            if target and target.get("line_type") != "DISCOUNT":
                target["linked_discount"] = target.get("linked_discount", 0) + discount_amount
                logger.info(f"Linked discount {discount_amount}円 to {target.get('product_name')}")

        return items

    def _normalize_item(self, item: Dict, shop_name: str, amounts: Dict = None) -> Dict:
        """
        商品名を正規化し、カテゴリ・税率を判定

        Args:
            item: 商品データ（Stage Gの出力）
            shop_name: 店舗名
            amounts: レシート全体の金額情報（税率判定に使用）

        Returns:
            Dict: {"product_name": "正規化後", "category_id": "...", "tax_rate": 10}
        """
        # 商品名を取得（空文字列もNoneとして扱う）
        product_name = item.get("product_name") or item.get("line_text") or item.get("ocr_raw_text") or "不明"
        # 空文字列の場合は「不明」に
        if not product_name or not product_name.strip():
            product_name = "不明"

        receipt_tax_mark = item.get("tax_mark")  # レシートの税率マーク

        # レシート全体の税率情報を確認（最優先）
        receipt_level_tax_rate = None
        if amounts:
            tax_8_amount = amounts.get("tax_8_amount") or 0
            tax_10_amount = amounts.get("tax_10_amount") or 0

            # 8%のみの場合
            if tax_8_amount > 0 and tax_10_amount == 0:
                receipt_level_tax_rate = 8
                logger.debug(f"Receipt has only 8% tax, setting all items to 8%")
            # 10%のみの場合
            elif tax_10_amount > 0 and tax_8_amount == 0:
                receipt_level_tax_rate = 10
                logger.debug(f"Receipt has only 10% tax, setting all items to 10%")
            # 混在の場合は個別判定に進む

        # レシート全体の税率が判定できた場合はそれを使用（最優先）
        if receipt_level_tax_rate is not None:
            return {
                "product_name": product_name,
                "category_id": None,
                "tax_rate": receipt_level_tax_rate,
                "tax_rate_source": "receipt_level",
                "tax_amount": None
            }

        # 1. エイリアス変換
        product_name = self.aliases.get(product_name.lower(), product_name)

        # 2. 商品辞書マッチング
        for entry in self.product_dict:
            if entry["raw_keyword"].lower() in product_name.lower():
                return {
                    "product_name": entry["official_name"],
                    "category_id": entry["category_id"],
                    "tax_rate": entry["tax_rate"],
                    "tax_rate_source": "master",  # マスタから取得
                    "tax_amount": None  # 後で計算
                }

        # 3. 商品名から税率パターンを検出（「外8」「内8」などのレシート記載パターン）
        if "外8" in product_name or "内8" in product_name or "外 8" in product_name or "内 8" in product_name:
            tax_rate = 8
            tax_rate_source = "product_name_pattern"
            # 商品名から税率パターンを削除
            product_name = product_name.replace("外8", "").replace("内8", "").replace("外 8", "").replace("内 8", "").strip()
            # 空文字列になった場合は「不明」に
            if not product_name:
                product_name = "不明"
        elif "外10" in product_name or "内10" in product_name or "外 10" in product_name or "内 10" in product_name:
            tax_rate = 10
            tax_rate_source = "product_name_pattern"
            # 商品名から税率パターンを削除
            product_name = product_name.replace("外10", "").replace("内10", "").replace("外 10", "").replace("内 10", "").strip()
            # 空文字列になった場合は「不明」に
            if not product_name:
                product_name = "不明"
        # 4. レシートのマークから税率を判定
        # 8%マークの判定（複数パターン対応）
        elif receipt_tax_mark and (
            receipt_tax_mark in ["*", "※", "◆"] or  # よくある軽減税率マーク
            "8%" in str(receipt_tax_mark) or
            "8" in str(receipt_tax_mark) or
            "(軽)" in str(receipt_tax_mark) or
            "外8" in str(receipt_tax_mark) or  # 外税8%のパターン
            "内8" in str(receipt_tax_mark)  # 内税8%のパターン
        ):
            tax_rate = 8
            tax_rate_source = "receipt_mark"
        # 10%マークの判定
        elif receipt_tax_mark and (
            receipt_tax_mark in ["★", "☆"] or  # よくある標準税率マーク
            "10%" in str(receipt_tax_mark) or
            "10" in str(receipt_tax_mark) or
            "外10" in str(receipt_tax_mark) or  # 外税10%のパターン
            "内10" in str(receipt_tax_mark)  # 内税10%のパターン
        ):
            tax_rate = 10
            tax_rate_source = "receipt_mark"
        else:
            # デフォルト10%（あとで要レビュー）
            tax_rate = 10
            tax_rate_source = "default"

        return {
            "product_name": product_name,
            "category_id": None,
            "tax_rate": tax_rate,
            "tax_rate_source": tax_rate_source,
            "tax_amount": None
        }

    def _determine_situation(self, trans_date: date) -> str:
        """
        取引日からシチュエーションを判定

        Args:
            trans_date: 取引日

        Returns:
            str: シチュエーションID
        """
        weekday = trans_date.weekday()  # 0=月曜, 6=日曜

        # 土日
        if weekday >= 5:
            for s in self.situations:
                if s["name"] == "週末":
                    return s["id"]

        # 平日
        for s in self.situations:
            if s["name"] == "平日":
                return s["id"]

        # デフォルト（最初のシチュエーション）
        return self.situations[0]["id"] if self.situations else None

    def _calculate_and_distribute_tax(
        self,
        normalized_items: List[Dict],
        amounts: Dict
    ) -> List[Dict]:
        """
        税額を按分計算（内税・外税対応）

        Args:
            normalized_items: 正規化済み商品リスト
            amounts: Stage Gで抽出した金額情報

        Returns:
            List[Dict]: 税額が計算された商品リスト
        """
        # 【重要】内税・外税の判定
        # Stage Gで判定済みの場合はそれを優先
        tax_type = amounts.get("tax_display_type")

        if tax_type:
            logger.info(f"Stage Gで判定済み: tax_display_type={tax_type}")
        else:
            # フォールバック: 小計と合計の比較で判定
            subtotal = amounts.get("subtotal")
            total = amounts.get("total")

            if subtotal is not None and total is not None and subtotal < total:
                tax_type = "excluded"  # 外税
                logger.info(f"外税レシート検出: 小計={subtotal}円 < 合計={total}円")
            else:
                tax_type = "included"  # 内税
                logger.info(f"内税レシート検出: 小計={subtotal}円 = 合計={total}円")

        # 商品を8%と10%にグループ化
        items_8 = []
        items_10 = []

        for item_data in normalized_items:
            if item_data["normalized"]["tax_rate"] == 8:
                items_8.append(item_data)
            else:
                items_10.append(item_data)

        # レシート記載の税額を使用（優先）
        tax_8_amount = amounts.get("tax_8_amount") or 0
        tax_10_amount = amounts.get("tax_10_amount") or 0

        # レシート記載がない場合のみ逆算（通常は記載されている）
        if tax_8_amount == 0:
            total_8 = sum(item["raw_item"]["amount"] or 0 for item in items_8)
            if total_8 > 0:
                if tax_type == "excluded":
                    tax_8_amount = round(total_8 * 8 / 100)  # 外税：本体価格×税率
                else:
                    tax_8_amount = round(total_8 * 8 / 108)  # 内税：税込額から逆算
                logger.warning(f"8% tax not in receipt, calculated: {tax_8_amount}円 (type={tax_type})")

        if tax_10_amount == 0:
            total_10 = sum(item["raw_item"]["amount"] or 0 for item in items_10)
            if total_10 > 0:
                if tax_type == "excluded":
                    tax_10_amount = round(total_10 * 10 / 100)  # 外税：本体価格×税率
                else:
                    tax_10_amount = round(total_10 * 10 / 110)  # 内税：税込額から逆算
                logger.warning(f"10% tax not in receipt, calculated: {tax_10_amount}円 (type={tax_type})")

        # 各商品に税額を按分（内外タイプを渡す）
        self._distribute_tax_to_items(items_8, tax_8_amount, tax_type)
        self._distribute_tax_to_items(items_10, tax_10_amount, tax_type)

        # 税対象額を計算（税抜額）
        total_8 = sum(item["raw_item"]["amount"] or 0 for item in items_8)
        total_10 = sum(item["raw_item"]["amount"] or 0 for item in items_10)

        # 税対象額を返す（内税の場合は税抜額、外税の場合も税抜額）
        if tax_type == "included":
            # 内税：税込額から税額を引いて税抜額を計算
            tax_8_subtotal = total_8 - tax_8_amount if total_8 > 0 else 0
            tax_10_subtotal = total_10 - tax_10_amount if total_10 > 0 else 0
        else:
            # 外税：表示額がそのまま税抜額
            tax_8_subtotal = total_8
            tax_10_subtotal = total_10

        tax_subtotals = {
            "tax_8_subtotal": tax_8_subtotal,
            "tax_10_subtotal": tax_10_subtotal
        }

        return normalized_items, tax_subtotals

    def _distribute_tax_to_items(self, items: List[Dict], total_tax: int, tax_type: str):
        """
        商品データの7要素を設定
        1. 数量
        2. 表示額
        3. 外or内
        4. 税率
        5. 本体価
        6. 税額
        7. 税込価

        Args:
            items: 商品リスト
            total_tax: グループ全体の税額
            tax_type: "included"（内税）or "excluded"（外税）
        """
        if not items:
            return

        # 金額0円の商品（セット内訳行など）を除外
        items_with_amount = [item for item in items if (item["raw_item"].get("amount") or 0) != 0]
        items_zero_amount = [item for item in items if (item["raw_item"].get("amount") or 0) == 0]

        # 金額0円の商品には税額0を設定
        for item in items_zero_amount:
            quantity = item["raw_item"].get("quantity", 1)
            displayed_amount = 0
            item["normalized"]["quantity"] = quantity
            item["normalized"]["displayed_amount"] = displayed_amount
            item["normalized"]["tax_display_type"] = tax_type
            item["normalized"]["base_price"] = 0
            item["normalized"]["tax_amount"] = 0
            item["normalized"]["tax_included_amount"] = 0
            logger.debug(f"Zero-amount item excluded from tax distribution: {item['raw_item'].get('product_name')}")

        # 金額がある商品のみで税額按分を行う
        if not items_with_amount:
            return

        if total_tax == 0:
            # 税額が0の場合も7要素を設定（割引は考慮）
            for item in items_with_amount:
                quantity = item["raw_item"].get("quantity", 1)
                displayed_amount = item["raw_item"].get("amount") or 0
                linked_discount = item["raw_item"].get("linked_discount", 0)

                # 税込価を計算（表示額 + 割引）
                tax_included_amount = displayed_amount + linked_discount

                # 7要素を設定
                item["normalized"]["quantity"] = quantity  # 1. 数量
                item["normalized"]["displayed_amount"] = displayed_amount  # 2. 表示額
                item["normalized"]["tax_display_type"] = tax_type  # 3. 外or内
                # 4. 税率 は _normalize_item で既に設定済み
                item["normalized"]["tax_amount"] = 0  # 6. 税額

                if tax_type == "excluded":
                    # 外税：表示額 = 本体価
                    item["normalized"]["base_price"] = displayed_amount + linked_discount  # 5. 本体価
                    item["normalized"]["tax_included_amount"] = displayed_amount + linked_discount  # 7. 税込価
                else:
                    # 内税：表示額 = 税込額
                    item["normalized"]["tax_included_amount"] = tax_included_amount  # 7. 税込価
                    item["normalized"]["base_price"] = tax_included_amount  # 5. 本体価
            return

        # Step 1: 各商品の税込価を計算（金額がある商品のみ）
        tax_included_amounts = []
        for item in items_with_amount:
            displayed_amount = item["raw_item"].get("amount") or 0
            linked_discount = item["raw_item"].get("linked_discount", 0)
            tax_included_amount = displayed_amount + linked_discount
            tax_included_amounts.append(tax_included_amount)

        # Step 2: 各商品の理論税額を計算（小数のまま保持）
        theoretical_taxes_float = []
        for i, item in enumerate(items_with_amount):
            tax_included_amount = tax_included_amounts[i]
            tax_rate = item["normalized"].get("tax_rate", 10)
            line_type = item["raw_item"].get("line_type", "ITEM")

            # 割引行は税額0（商品行にすでに割引後の税額が含まれているため）
            if line_type == "DISCOUNT":
                theoretical_tax = 0.0
            elif tax_type == "excluded":
                # 外税：理論税額 = 税抜額 × 税率 / 100
                theoretical_tax = tax_included_amount * tax_rate / 100
            else:
                # 内税：理論税額 = 税込価 - (税込価 / (1 + 税率/100))
                theoretical_tax = tax_included_amount - (tax_included_amount / (1 + tax_rate / 100))

            theoretical_taxes_float.append(theoretical_tax)

        # Step 3: 理論税額の合計（小数）とレシート記載税額の差分
        total_theoretical_tax = sum(theoretical_taxes_float)
        remainder = total_tax - total_theoretical_tax

        # Step 4: 各商品の理論税額を四捨五入し、端数を按分
        theoretical_taxes_rounded = [round(tax) for tax in theoretical_taxes_float]
        total_rounded = sum(theoretical_taxes_rounded)
        final_remainder = total_tax - total_rounded

        # Step 5: 最終端数を税込価の大きい順に1円ずつ配分
        distributed_tax = theoretical_taxes_rounded.copy()

        if final_remainder != 0:
            # 税込価の絶対値でソート（インデックスを保持）
            indexed_amounts = [(i, abs(tax_included_amounts[i])) for i in range(len(items_with_amount))]
            indexed_amounts.sort(key=lambda x: x[1], reverse=True)

            # 端数を1円ずつ配分
            for j in range(abs(final_remainder)):
                idx = indexed_amounts[j % len(items_with_amount)][0]
                if final_remainder > 0:
                    distributed_tax[idx] += 1
                else:
                    distributed_tax[idx] -= 1

        # 各商品に7要素を設定（金額がある商品のみ）
        for i, item in enumerate(items_with_amount):
            quantity = item["raw_item"].get("quantity", 1)
            displayed_amount = item["raw_item"].get("amount") or 0
            linked_discount = item["raw_item"].get("linked_discount", 0)  # リンクされた割引（負の値）

            # 税込価を計算（表示額 + 割引）
            # 割引は負の値なので加算すると減算になる
            tax_included_amount = displayed_amount + linked_discount

            # 按分された税額を使用
            tax_amount = distributed_tax[i]

            # 税率から本体価を計算
            tax_rate = item["normalized"].get("tax_rate", 10)
            if tax_type == "excluded":
                # 外税：表示額 = 本体価、税込価 = 本体価 + 税額
                base_price = displayed_amount + linked_discount
                tax_included_amount = base_price + tax_amount
            else:
                # 内税：税込価 - 按分税額 = 本体価
                base_price = tax_included_amount - tax_amount

            # 7要素を設定
            item["normalized"]["quantity"] = quantity  # 1. 数量
            item["normalized"]["displayed_amount"] = displayed_amount  # 2. 表示額
            item["normalized"]["tax_display_type"] = tax_type  # 3. 外or内
            # 4. 税率 は _normalize_item で既に設定済み
            item["normalized"]["base_price"] = base_price  # 5. 本体価
            item["normalized"]["tax_amount"] = tax_amount  # 6. 税額
            item["normalized"]["tax_included_amount"] = tax_included_amount  # 7. 税込価

            if linked_discount != 0:
                logger.info(f"{item['raw_item'].get('product_name')}: 表示額={displayed_amount}, 割引={linked_discount}, 税込価={tax_included_amount}, 本体価={base_price}, 税額={tax_amount}")

        logger.debug(f"Distributed tax ({tax_type})")

    # ========================================
    # マスタデータ読み込み
    # ========================================

    def _load_aliases(self) -> Dict[str, str]:
        """エイリアステーブルを読み込み"""
        result = self.db.client.table("MASTER_Rules_transaction_dict").select("*").execute()
        # product_name → official_name のマッピング
        aliases = {}
        for row in result.data:
            if row.get("product_name") and row.get("official_name"):
                aliases[row["product_name"].lower()] = row["official_name"]
        return aliases

    def _load_product_dictionary(self) -> List[Dict]:
        """商品辞書を読み込み"""
        result = self.db.client.table("MASTER_Product_classify").select("*").execute()
        return result.data

    def _load_situations(self) -> List[Dict]:
        """シチュエーションマスタを読み込み（名目）"""
        result = self.db.client.table("MASTER_Categories_purpose").select("*").execute()
        return result.data

    def _load_categories(self) -> List[Dict]:
        """カテゴリマスタを読み込み（商品カテゴリ）"""
        result = self.db.client.table("MASTER_Categories_product").select("*").execute()
        return result.data
