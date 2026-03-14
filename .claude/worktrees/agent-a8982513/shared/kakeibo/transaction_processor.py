"""
トランザクション処理モジュール

- Gemini OCRの結果を正規化
- エイリアス変換・辞書マッチング
- シチュエーション設定（デフォルト: 日常）
- Supabaseへの登録
"""

import re
from datetime import datetime, date
from typing import Dict, List, Optional
from supabase import Client
from loguru import logger

from shared.common.database.client import DatabaseClient
from shared.ai.llm_client.llm_client import LLMClient


class TransactionProcessor:
    """トランザクション処理クラス"""

    def __init__(self):
        db_client = DatabaseClient()
        self.db: Client = db_client.client
        self.llm_client = LLMClient()  # AI一般名詞抽出用

        # マスタデータをキャッシュ
        self.aliases = self._load_aliases()
        self.product_dict = self._load_product_dictionary()
        self.product_generalize = self._load_product_generalize()
        self.situations = self._load_situations()
        self.categories = self._load_categories()

    def process(
        self,
        ocr_result: Dict,
        file_name: str,
        drive_file_id: str,
        model_name: str = "gemini-2.5-flash",
        source_folder: str = "INBOX"
    ) -> Dict:
        """
        OCR結果を処理してDBに登録（3層構造対応）

        Args:
            ocr_result: Gemini OCRの結果
            file_name: ファイル名
            drive_file_id: Google DriveのファイルID
            model_name: 使用したGeminiモデル名
            source_folder: ソースフォルダ（INBOX_EASY/INBOX_HARD）

        Returns:
            Dict: 処理結果
                - success: {"receipt_id": "...", "transaction_ids": [...]}
                - error: {"error": "...", "message": "..."}
        """
        try:
            # エラーチェック
            if "error" in ocr_result:
                self._log_processing_error(file_name, drive_file_id, ocr_result, model_name, None)
                return ocr_result

            # トランザクション日付
            trans_date = datetime.strptime(ocr_result["transaction_date"], "%Y-%m-%d").date()

            # 1. レシート情報を登録（親テーブル）
            receipt_id = self._insert_receipt(
                ocr_result=ocr_result,
                file_name=file_name,
                drive_file_id=drive_file_id,
                model_name=model_name,
                source_folder=source_folder
            )

            # 2. イベント期間判定 → シチュエーション確定
            situation_id = self._determine_situation(trans_date)

            # 3. 各商品を正規化（税率決定のみ、税額はまだ計算しない）
            # 注意: 小計・合計行は税額按分の対象外なので、ITEM行のみ処理
            normalized_items = []
            for item in ocr_result["items"]:
                # SUBTOTAL/TOTAL行はスキップ（税額按分の対象外）
                line_type = item.get("line_type", "ITEM")
                if line_type in ["SUBTOTAL", "TOTAL"]:
                    # 小計・合計行はそのまま保持（税額按分しない）
                    # 商品名を取得（nullや空文字列の場合は代替値を使用）
                    subtotal_name = item.get("product_name") or item.get("line_text") or item.get("ocr_raw_text") or "小計"
                    if not subtotal_name or not subtotal_name.strip():
                        subtotal_name = "小計" if line_type == "SUBTOTAL" else "合計"

                    normalized_items.append({
                        "raw_item": item,
                        "normalized": {
                            "product_name": subtotal_name,
                            "general_name": None,  # 小計/合計行は一般名詞なし
                            "category_id": None,
                            "tax_rate": item.get("tax_rate", 10),
                            "tax_rate_fixed": False,
                            "tax_amount": item.get("tax_amount", 0)  # レシート記載の税額をそのまま使用
                        }
                    })
                    continue

                normalized = self._normalize_item(item, ocr_result["shop_name"], ocr_result.get("tax_summary"))
                normalized_items.append({
                    "raw_item": item,
                    "normalized": normalized
                })

            # 4. 税額を按分計算
            items_with_tax = self._calculate_and_distribute_tax(
                normalized_items,
                ocr_result.get("tax_summary")
            )

            # 5. 各明細を登録（OCRデータ + 標準化データを統合）
            transaction_ids = []

            for line_num, item_data in enumerate(items_with_tax, start=1):
                item = item_data["raw_item"]
                normalized = item_data["normalized"]

                # Rawdata_RECEIPT_items: OCRデータ + 標準化データを同時に保存
                trans_id = self._insert_transaction(
                    receipt_id=receipt_id,
                    line_number=line_num,
                    ocr_raw_text=item.get("ocr_raw", item["product_name"]),  # OCR原文
                    ocr_confidence=item.get("confidence", None),
                    product_name=normalized["product_name"],
                    item_name=None,  # 将来的にOCRから取得
                    unit_price=item.get("unit_price"),
                    quantity=item.get("quantity", 1),
                    marks_text=item.get("tax_mark"),  # 税率マーク
                    discount_text=item.get("discount_text"),  # 割引情報
                    # 標準化データも同時に保存
                    normalized=normalized,
                    situation_id=situation_id,
                    total_amount=item["total_amount"],
                    tax_amount=normalized["tax_amount"],
                    needs_review=item_data.get("needs_review", False)
                )
                transaction_ids.append(trans_id)

            # 6. 処理ログ記録（receipt_idとocr_resultも保存）
            processing_log_id = self._log_processing_success(
                file_name=file_name,
                drive_file_id=drive_file_id,
                receipt_id=receipt_id,
                transaction_ids=transaction_ids,
                ocr_result=ocr_result,
                model_name=model_name
            )

            # 7. 税額サマリー保存
            if "tax_summary" in ocr_result:
                self._save_tax_summary(
                    receipt_id=receipt_id,
                    processing_log_id=processing_log_id,
                    tax_summary=ocr_result["tax_summary"],
                    items_with_tax=items_with_tax
                )

            logger.info(f"Processed receipt {receipt_id} with {len(transaction_ids)} transactions from {file_name} using {model_name}")

            return {
                "success": True,
                "receipt_id": receipt_id,
                "transaction_ids": transaction_ids
            }

        except Exception as e:
            logger.error(f"Failed to process {file_name}: {e}")
            self._log_processing_error(file_name, drive_file_id, {"error": str(e)}, model_name, None)
            return {
                "error": "processing_failed",
                "message": str(e)
            }

    def _normalize_item(self, item: Dict, shop_name: str, tax_summary: Dict = None) -> Dict:
        """
        商品情報を正規化（税率のみ決定、税額は後で按分計算）

        Priority:
        1. レシート全体の税率情報（最優先）
        2. tax_markから税率を判定
        3. エイリアス変換
        4. 商品辞書マッチング（税率も取得）
        5. そのまま使用（Geminiの推測税率を使用）

        Args:
            item: {"product_name": "...", "tax_rate": 10, "tax_mark": "※", ...}
            shop_name: 店舗名
            tax_summary: レシート全体の税額情報（税率判定に使用）

        Returns:
            Dict: {"product_name": "正規化後", "category_id": "...", "tax_rate": 10, "tax_rate_fixed": True/False}
        """
        # 商品名を取得（nullや空文字列の場合は代替値を使用）
        product_name = item.get("product_name") or item.get("line_text") or item.get("ocr_raw_text") or "不明"
        # 空文字列の場合は「不明」に
        if not product_name or not product_name.strip():
            product_name = "不明"

        gemini_tax_rate = item.get("tax_rate", 10)  # Geminiの推測税率（デフォルト10%）

        # レシート全体の税率情報を確認（最優先）
        receipt_level_tax_rate = None
        if tax_summary:
            tax_8_amount = tax_summary.get("tax_8_amount") or 0
            tax_10_amount = tax_summary.get("tax_10_amount") or 0

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
                "tax_rate_fixed": True  # レシート全体の税率は確定
            }

        # 1. tax_markから税率を判定
        tax_mark = item.get("tax_mark")
        tax_rate_from_mark = None

        # 商品名から税率パターンを検出（「外8」「内8」などのレシート記載パターン）
        product_name_lower = product_name.lower()
        if "外8" in product_name or "内8" in product_name or "外 8" in product_name or "内 8" in product_name:
            tax_rate_from_mark = 8
            # 商品名から税率パターンを削除
            product_name = product_name.replace("外8", "").replace("内8", "").replace("外 8", "").replace("内 8", "").strip()
            # 空文字列になった場合は元の商品名を保持（削除しない）
            if not product_name:
                product_name = item["product_name"]  # 元の商品名に戻す
        elif "外10" in product_name or "内10" in product_name or "外 10" in product_name or "内 10" in product_name:
            tax_rate_from_mark = 10
            # 商品名から税率パターンを削除
            product_name = product_name.replace("外10", "").replace("内10", "").replace("外 10", "").replace("内 10", "").strip()
            # 空文字列になった場合は元の商品名を保持（削除しない）
            if not product_name:
                product_name = item["product_name"]  # 元の商品名に戻す

        # tax_markフィールドからも判定
        if tax_mark:
            # 8%マークの判定（複数パターン対応）
            if (
                tax_mark in ["*", "※", "◆"] or  # よくある軽減税率マーク
                "8%" in str(tax_mark) or
                "8" in str(tax_mark) or
                "(軽)" in str(tax_mark) or
                "外8" in str(tax_mark) or  # 外税8%のパターン
                "内8" in str(tax_mark)  # 内税8%のパターン
            ):
                tax_rate_from_mark = 8
            # 10%マークの判定
            elif (
                tax_mark in ["★", "☆"] or  # よくある標準税率マーク
                "10%" in str(tax_mark) or
                "10" in str(tax_mark) or
                "外10" in str(tax_mark) or  # 外税10%のパターン
                "内10" in str(tax_mark)  # 内税10%のパターン
            ):
                tax_rate_from_mark = 10

        # 2. エイリアス変換
        product_name = self.aliases.get(product_name.lower(), product_name)

        # 3. 商品辞書マッチング
        for entry in self.product_dict:
            if entry["raw_keyword"].lower() in product_name.lower():
                # 辞書に登録されている税率を優先（確定）
                # ただし、tax_markがあればそちらを最優先
                # 一般名詞は後でバッチ処理で抽出（取り込み時はスキップ）

                return {
                    "product_name": entry["official_name"],
                    "general_name": None,  # 後でバッチ処理で抽出
                    "category_id": entry["category_id"],
                    "tax_rate": tax_rate_from_mark if tax_rate_from_mark else entry.get("tax_rate", 10),
                    "tax_rate_fixed": True  # 辞書由来の税率は確定
                }

        # 4. マッチしなければtax_markまたはGeminiの推測を使用（暫定）
        # 一般名詞は後でバッチ処理で抽出（取り込み時はスキップ）

        return {
            "product_name": product_name,
            "general_name": None,  # 後でバッチ処理で抽出
            "category_id": None,
            "tax_rate": tax_rate_from_mark if tax_rate_from_mark else gemini_tax_rate,
            "tax_rate_fixed": bool(tax_rate_from_mark)  # tax_markがあれば確定
        }

    def _determine_situation(self, trans_date: date) -> str:
        """
        取引日からシチュエーションを判定

        Args:
            trans_date: 取引日

        Returns:
            str: situation_id（常に「日常」を返す。必要に応じて手動でpurposeカラムを設定）
        """
        # デフォルトは「日常」を返す
        default_situation = next(
            (s for s in self.situations if s["name"] == "日常"),
            self.situations[0] if self.situations else None
        )

        if default_situation:
            return default_situation["id"]
        else:
            logger.warning("「日常」シチュエーションが見つかりません。最初のシチュエーションを使用します。")
            return self.situations[0]["id"] if self.situations else None

    def _insert_receipt(self, ocr_result: Dict, file_name: str, drive_file_id: str, model_name: str, source_folder: str) -> str:
        """レシート情報をDBに登録（親テーブル）"""
        trans_date = datetime.strptime(ocr_result["transaction_date"], "%Y-%m-%d").date()

        # レシートの合計金額を計算（複数のフィールドから取得を試みる）
        total_amount = 0
        for item in ocr_result.get("items", []):
            # total_amount, amount, displayed_amount のいずれかを使用
            item_amount = item.get("total_amount") or item.get("amount") or item.get("displayed_amount") or 0
            total_amount += item_amount

        # 税額サマリーから税抜小計を計算
        tax_summary = ocr_result.get("tax_summary", {})
        subtotal_amount = None
        if tax_summary:
            # 8%税抜 + 10%税抜
            subtotal_8 = tax_summary.get("tax_8_subtotal", 0)
            subtotal_10 = tax_summary.get("tax_10_subtotal", 0)
            if subtotal_8 or subtotal_10:
                subtotal_amount = subtotal_8 + subtotal_10

        receipt_data = {
            "transaction_date": ocr_result["transaction_date"],
            "shop_name": ocr_result["shop_name"],
            "total_amount_check": ocr_result.get("total") or total_amount or 0,  # nullを許容しない
            "subtotal_amount": subtotal_amount,
            "image_path": f"99_Archive/{trans_date.strftime('%Y-%m')}/{file_name}",
            "drive_file_id": drive_file_id,
            "source_folder": source_folder,
            "ocr_model": model_name,
            "workspace": "household",
            "is_verified": False
        }

        result = self.db.table("Rawdata_RECEIPT_shops").insert(receipt_data).execute()
        return result.data[0]["id"]

    def _insert_transaction(self, receipt_id: str, line_number: int, ocr_raw_text: str,
                           ocr_confidence: float, product_name: str, item_name: str,
                           unit_price: int, quantity: int, marks_text: str = None,
                           discount_text: str = None, normalized: Dict = None,
                           situation_id: str = None, total_amount: int = None,
                           tax_amount: int = None, needs_review: bool = False) -> str:
        """トランザクション（明細行）+ 標準化データをDBに登録（統合テーブル）"""
        trans_data = {
            "receipt_id": receipt_id,
            "line_number": line_number,
            "line_type": "ITEM",  # 将来的にOCRで判定
            "ocr_raw_text": ocr_raw_text,
            "ocr_confidence": ocr_confidence,
            "product_name": product_name,
            "item_name": item_name,
            "unit_price": unit_price,
            "quantity": quantity,
            "marks_text": marks_text,  # 税率マーク（※、★、8%、10%など）
            "discount_text": discount_text  # 割引情報
        }

        # 標準化データがあれば追加
        if normalized:
            # 7要素構造のデータを取得
            base_price = normalized.get("base_price")  # 本体価（税抜）

            # 本体単価を計算（本体価 ÷ 数量）
            std_unit_price = None
            if base_price is not None and quantity and quantity > 0:
                std_unit_price = base_price // quantity  # 整数除算

            trans_data.update({
                "official_name": normalized.get("official_name"),
                "general_name": normalized.get("general_name"),  # 一般名詞
                "category_id": normalized.get("category_id"),
                "situation_id": situation_id,
                "tax_rate": normalized["tax_rate"],
                "std_amount": total_amount,  # 税込価
                "std_unit_price": std_unit_price,  # 本体単価
                "tax_amount": tax_amount,
                "needs_review": needs_review
            })

        result = self.db.table("Rawdata_RECEIPT_items").insert(trans_data).execute()
        return result.data[0]["id"]

    def _log_processing_success(self, file_name: str, drive_file_id: str, receipt_id: str, transaction_ids: List[str], ocr_result: Dict = None, model_name: str = None) -> str:
        """処理成功をログに記録"""
        log_data = {
            "file_name": file_name,
            "drive_file_id": drive_file_id,
            "receipt_id": receipt_id,
            "status": "success",
            "transaction_ids": transaction_ids
        }
        if model_name:
            log_data["ocr_model"] = model_name
        if ocr_result:
            log_data["ocr_result"] = ocr_result

        result = self.db.table("99_lg_image_proc_log").insert(log_data).execute()
        return result.data[0]["id"]

    def _log_processing_error(self, file_name: str, drive_file_id: str, error_info: Dict, model_name: str = None, receipt_id: str = None):
        """処理エラーをログに記録"""
        log_data = {
            "file_name": file_name,
            "drive_file_id": drive_file_id,
            "status": "failed",
            "error_message": error_info.get("message", error_info.get("error"))
        }
        if model_name:
            log_data["ocr_model"] = model_name
        if receipt_id:
            log_data["receipt_id"] = receipt_id

        self.db.table("99_lg_image_proc_log").insert(log_data).execute()

    def _calculate_and_distribute_tax(self, normalized_items: List[Dict], tax_summary: Dict) -> List[Dict]:
        """
        税額を按分計算

        重要な前提：
        - 税込合計額（支払い金額）が絶対に正しい
        - レシート記載の8%/10%対象税込額から税額を逆算
        - 各商品に税額を按分（端数は最初の商品に加算）

        Args:
            normalized_items: 正規化済み商品リスト
            tax_summary: レシート記載の税額サマリー

        Returns:
            List[Dict]: 税額が計算された商品リスト
        """
        # 商品を8%と10%にグループ化
        # 注意: SUBTOTAL/TOTAL行は税額按分の対象外
        items_8 = []
        items_10 = []

        for item_data in normalized_items:
            # SUBTOTAL/TOTAL行はスキップ
            line_type = item_data["raw_item"].get("line_type", "ITEM")
            if line_type in ["SUBTOTAL", "TOTAL"]:
                continue

            if item_data["normalized"]["tax_rate"] == 8:
                items_8.append(item_data)
            else:
                items_10.append(item_data)

        # 各グループの税込合計を計算
        total_8 = sum(item["raw_item"]["total_amount"] for item in items_8)
        total_10 = sum(item["raw_item"]["total_amount"] for item in items_10)

        needs_review = False

        # tax_summaryがある場合は整合性チェック
        if tax_summary:
            receipt_total_8 = tax_summary.get("tax_8_subtotal", 0) + tax_summary.get("tax_8_amount", 0)
            receipt_total_10 = tax_summary.get("tax_10_subtotal", 0) + tax_summary.get("tax_10_amount", 0)

            # 整合性チェック（±数円程度の誤差は許容）
            if abs(total_8 - receipt_total_8) > 5:
                logger.warning(f"8% total mismatch: calculated={total_8}, receipt={receipt_total_8}")
                needs_review = True

            if abs(total_10 - receipt_total_10) > 5:
                logger.warning(f"10% total mismatch: calculated={total_10}, receipt={receipt_total_10}")
                needs_review = True

            # レシート記載値を使用
            if receipt_total_8 > 0:
                total_8 = receipt_total_8
            if receipt_total_10 > 0:
                total_10 = receipt_total_10

        # 税額を計算（グループ全体で）
        # 8%税額 = 8%対象税込額 × (8/108)
        # 10%税額 = 10%対象税込額 × (10/110)
        tax_8_total = round(total_8 * 8 / 108) if total_8 > 0 else 0
        tax_10_total = round(total_10 * 10 / 110) if total_10 > 0 else 0

        # 各商品に税額を按分
        self._distribute_tax_to_items(items_8, tax_8_total)
        self._distribute_tax_to_items(items_10, tax_10_total)

        # needs_reviewフラグを設定
        result = normalized_items
        if needs_review:
            for item in result:
                item["needs_review"] = True

        return result

    def _distribute_tax_to_items(self, items: List[Dict], total_tax: int):
        """
        税額を各商品に按分（端数は最初の商品に加算）

        例：5円の商品×2、税額1円の場合
        - 商品1: 1円
        - 商品2: 0円

        Args:
            items: 商品リスト
            total_tax: 配分する税額合計
        """
        if not items or total_tax == 0:
            for item in items:
                item["normalized"]["tax_amount"] = 0
            return

        # 各商品の税込額の比率で按分
        total_amount = sum(item["raw_item"]["total_amount"] for item in items)

        distributed_tax = []
        for item in items:
            item_amount = item["raw_item"]["total_amount"]
            # 比率で按分（切り捨て）
            tax = int(total_tax * item_amount / total_amount)
            distributed_tax.append(tax)

        # 端数を計算
        remainder = total_tax - sum(distributed_tax)

        # 端数を最初の商品に加算
        if remainder != 0 and len(distributed_tax) > 0:
            distributed_tax[0] += remainder

        # 各商品に税額を設定
        for i, item in enumerate(items):
            item["normalized"]["tax_amount"] = distributed_tax[i]

        logger.debug(f"Distributed {total_tax}円 tax: {distributed_tax} (remainder={remainder})")

    def _save_tax_summary(self, receipt_id: str, processing_log_id: str, tax_summary: Dict, items_with_tax: List[Dict]):
        """
        税額サマリーを保存

        Args:
            receipt_id: レシートID
            processing_log_id: 処理ログID
            tax_summary: レシート記載の税額サマリー
            items_with_tax: 税額計算済み商品リスト
        """
        # 計算した税額を集計
        calculated_tax_8 = sum(
            item["normalized"]["tax_amount"]
            for item in items_with_tax
            if item["normalized"]["tax_rate"] == 8
        )
        calculated_tax_10 = sum(
            item["normalized"]["tax_amount"]
            for item in items_with_tax
            if item["normalized"]["tax_rate"] == 10
        )

        # レシート記載の税額
        actual_tax_8 = tax_summary.get("tax_8_amount", 0)
        actual_tax_10 = tax_summary.get("tax_10_amount", 0)

        # 差分計算（理論的には±1円以内になるはず）
        tax_8_diff = calculated_tax_8 - actual_tax_8 if actual_tax_8 > 0 else 0
        tax_10_diff = calculated_tax_10 - actual_tax_10 if actual_tax_10 > 0 else 0

        # 整合性フラグ（±1円以内なら一致とみなす）
        matches = abs(tax_8_diff) <= 1 and abs(tax_10_diff) <= 1

        # サマリー保存
        summary_data = {
            "receipt_id": receipt_id,
            "tax_8_subtotal": tax_summary.get("tax_8_subtotal"),
            "tax_8_amount": actual_tax_8,
            "tax_10_subtotal": tax_summary.get("tax_10_subtotal"),
            "tax_10_amount": actual_tax_10,
            "total_amount": tax_summary.get("total_amount"),
            "calculated_tax_8_amount": calculated_tax_8,
            "calculated_tax_10_amount": calculated_tax_10,
            "calculated_matches_actual": matches,
            "tax_8_diff": tax_8_diff,
            "tax_10_diff": tax_10_diff
        }

        self.db.table("60_ag_receipt_summary").insert(summary_data).execute()

        if matches:
            logger.info(f"Tax calculation successful: 8%={calculated_tax_8}円, 10%={calculated_tax_10}円")
        else:
            logger.warning(f"Tax diff: 8%={tax_8_diff}円, 10%={tax_10_diff}円")

    # ========================================
    # マスタデータ読み込み
    # ========================================
    def _load_aliases(self) -> Dict[str, str]:
        """エイリアステーブルを読み込み"""
        result = self.db.table("MASTER_Rules_transaction_dict").select("*").execute()
        # product_name → official_name のマッピング
        aliases = {}
        for row in result.data:
            if row.get("product_name") and row.get("official_name"):
                aliases[row["product_name"].lower()] = row["official_name"]
        return aliases

    def _load_product_dictionary(self) -> List[Dict]:
        """商品辞書を読み込み"""
        result = self.db.table("MASTER_Product_classify").select("*").execute()
        return result.data

    def _load_situations(self) -> List[Dict]:
        """シチュエーション一覧を読み込み（名目）"""
        result = self.db.table("MASTER_Categories_purpose").select("*").execute()
        return result.data

    def _load_categories(self) -> List[Dict]:
        """カテゴリ一覧を読み込み（商品カテゴリ）"""
        result = self.db.table("MASTER_Categories_product").select("*").execute()
        return result.data

    def _load_product_generalize(self) -> Dict[str, str]:
        """商品名→一般名詞のマッピングを読み込み"""
        result = self.db.table("MASTER_Product_generalize").select("*").execute()
        # raw_keyword → general_name のマッピング
        generalize_map = {}
        for row in result.data:
            if row.get("raw_keyword") and row.get("general_name"):
                generalize_map[row["raw_keyword"].lower()] = row["general_name"]
        return generalize_map

    def _get_general_name(self, product_name: str) -> Optional[str]:
        """
        商品名から一般名詞を取得

        「買い物を依頼したら、その名前で買ってこれるレベルの名称」を抽出

        ロジック:
        1. MASTER_Product_generalizeでブランド名変換（完全一致・部分一致）
        2. 見つからない場合はGemini 2.5 FlashでAI抽出
        3. AI失敗時は正規表現でメーカー名・容量・産地を除去（フォールバック）

        例:
        - 「明治おいしい牛乳 1000ml」→「牛乳」（ブランド名変換）
        - 「日本ハム 豚ひき肉 200g」→「豚ひき肉」（正規表現除去）
        - 「国産ほうれん草 1束」→「ほうれん草」（正規表現除去）

        Args:
            product_name: 商品名

        Returns:
            str: 一般名詞（例: 「豚ひき肉」「ほうれん草」「ベーコン」）
        """
        if not product_name:
            return None

        # Step 1: MASTER_Product_generalizeで完全一致チェック（ブランド名変換）
        general_name = self.product_generalize.get(product_name.lower())
        if general_name:
            return general_name

        # Step 2: 部分一致でブランド名変換
        for keyword, gen_name in self.product_generalize.items():
            if keyword in product_name.lower():
                return gen_name

        # Step 2.5: Gemini 2.5 FlashでAI抽出
        try:
            ai_result = self._extract_general_name_with_ai(product_name)
            if ai_result:
                logger.debug(f"AI抽出成功: {product_name} → {ai_result}")
                return ai_result
        except Exception as e:
            logger.warning(f"AI抽出失敗（正規表現フォールバックへ）: {e}")

        # Step 3: AI失敗時のフォールバック - 正規表現で自動抽出
        import re
        cleaned = product_name

        # 先頭の特殊な括弧・記号を除去
        cleaned = re.sub(r'^\([^\)]+\)\s*', '', cleaned)  # (10本パック)ヤクルト → ヤクルト
        cleaned = re.sub(r'^\[[^\]]+\]\s*', '', cleaned)  # [A]チーズ → チーズ

        # メーカー名を除去
        manufacturer_patterns = [
            r'明治\s*', r'森永\s*', r'雪印\s*', r'メグミルク\s*', r'雪印メグミルク\s*',
            r'日本ハム\s*', r'伊藤ハム\s*', r'プリマハム\s*', r'丸大食品\s*',
            r'カゴメ\s*', r'キューピー\s*', r'味の素\s*', r'キッコーマン\s*',
            r'ヤマサ\s*', r'ミツカン\s*', r'ハウス食品\s*', r'S&B\s*', r'エスビー\s*',
            r'日清\s*', r'東洋水産\s*', r'サッポロ\s*', r'サントリー\s*',
            r'キリン\s*', r'アサヒ\s*', r'コカ・コーラ\s*', r'ペプシ\s*',
            r'ダノン\s*', r'チチヤス\s*', r'オハヨー\s*', r'六甲バター\s*',
            r'QBB\s*', r'Q・B・B\s*', r'クラフト\s*',
            r'西友オリジナル\s*', r'みなさまのお墨付き\s*',
            r'セブンプレミアム\s*', r'トップバリュ\s*',
        ]
        for pattern in manufacturer_patterns:
            cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE)

        # 産地・品質表示を除去
        # 注意: 「低脂肪」「減塩」などは商品名の一部なので除去しない
        # （例: 「低脂肪牛乳」「減塩醤油」は商品の種類として認識される）
        origin_patterns = [
            r'国産\s*', r'北海道産\s*', r'九州産\s*',
            r'有機\s*', r'オーガニック\s*', r'無添加\s*', r'食塩無添加\s*',
        ]
        for pattern in origin_patterns:
            cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE)

        # 容量・数量を除去
        volume_patterns = [
            r'\d+(?:\.\d+)?(?:ml|ML|ミリリットル)',
            r'\d+(?:\.\d+)?(?:l|L|リットル)',
            r'\d+(?:\.\d+)?(?:g|グラム)',
            r'\d+(?:\.\d+)?(?:kg|キロ)',
            r'\d+枚切り?',  # 6枚切り、6枚切（パン用）
            r'[0-9０-９]+(?:個|本|束|枚|切れ?|缶|袋|パック|玉|入)',  # 全角数字にも対応
            r'[（(][0-9０-９]+(?:個|本|束|枚|切れ?|缶|袋|パック|玉|入)[)）]',
            r'×[0-9０-９]+', r'[0-9０-９]+入り?', r'ケース',
        ]
        for pattern in volume_patterns:
            cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE)

        # 特殊な記号・余分な空白を除去
        cleaned = re.sub(r'[【】\[\]『』「」（）()]', '', cleaned)
        cleaned = re.sub(r'\s+', ' ', cleaned)
        cleaned = cleaned.strip()

        # 空になった場合は元の商品名を返す
        if not cleaned:
            return product_name

        return cleaned

    def _get_general_name_and_keywords(self, product_name: str) -> Optional[Dict]:
        """
        商品名から一般名詞とキーワードを取得

        ロジック:
        1. MASTER_Product_generalizeでブランド名変換（完全一致・部分一致）
        2. 見つからない場合はGemini 2.5 FlashでAI抽出（general_name + keywords）
        3. AI失敗時は正規表現でgeneral_nameのみ生成（フォールバック）

        Args:
            product_name: 商品名

        Returns:
            dict: {"general_name": str, "keywords": list} (失敗時はNone)
                  例: {"general_name": "食パン", "keywords": ["食パン", "パスコ", "超熟", "6枚切り"]}
        """
        if not product_name:
            return None

        # Step 1: MASTER_Product_generalizeで完全一致チェック（ブランド名変換）
        general_name = self.product_generalize.get(product_name.lower())
        if general_name:
            # ブランド名変換の場合、キーワードは商品名を分割して生成
            # 簡易的に空白と記号で分割
            keywords = [general_name] + [
                word.strip() for word in re.split(r'[\s　]+', product_name)
                if word.strip() and word.strip() != general_name
            ]
            return {"general_name": general_name, "keywords": keywords}

        # Step 2: 正規化後の完全一致（空白のゆれ・量のバリエーション許容）
        def normalize_product_name(name):
            """空白除去 + 量情報除去"""
            # 空白を全て除去
            normalized = re.sub(r'[\s　]+', '', name.lower())
            # 量情報を除去（100g, 500ml, 6個入り など）
            normalized = re.sub(r'\d+(?:g|ml|l|個|枚|本|袋|パック|切|入り|ケース)(?:入り)?', '', normalized)
            # ×6 のようなパターンも除去
            normalized = re.sub(r'[×x]\d+', '', normalized)
            # () や【】内も除去
            normalized = re.sub(r'[\(（\[【].*?[\)）\]】]', '', normalized)
            return normalized.strip()

        normalized_product = normalize_product_name(product_name)

        for keyword, gen_name in self.product_generalize.items():
            normalized_keyword = normalize_product_name(keyword)
            if normalized_product == normalized_keyword:
                keywords = [gen_name] + [
                    word.strip() for word in re.split(r'[\s　]+', product_name)
                    if word.strip() and word.strip() != gen_name
                ]
                return {"general_name": gen_name, "keywords": keywords}

        # Step 2.5: Gemini 2.5 FlashでAI抽出
        try:
            ai_result = self._extract_general_name_with_ai(product_name)
            if ai_result:
                logger.debug(f"AI抽出成功: {product_name} → {ai_result}")
                return ai_result
        except Exception as e:
            logger.warning(f"AI抽出失敗（正規表現フォールバックへ）: {e}")

        # Step 3: AI失敗時のフォールバック - 正規表現でgeneral_nameのみ生成
        # _get_general_name()の正規表現ロジックを再利用
        general_name_only = self._get_general_name(product_name)
        if general_name_only:
            # キーワードは商品名を分割して生成
            keywords = [general_name_only] + [
                word.strip() for word in re.split(r'[\s　]+', product_name)
                if word.strip() and word.strip() != general_name_only
            ]
            return {"general_name": general_name_only, "keywords": keywords}

        return None

    def _extract_general_name_with_ai(self, product_name: str) -> Optional[Dict]:
        """
        Gemini 2.5 Flashで商品名から一般名詞とキーワードを抽出

        Args:
            product_name: 商品名

        Returns:
            dict: {"general_name": str, "keywords": list} (失敗時はNone)
                  例: {"general_name": "食パン", "keywords": ["食パン", "パスコ", "超熟", "6枚切り"]}
        """
        prompt = f"""あなたは商品名から一般名詞とキーワードを抽出する専門家です。

商品名: {product_name}

以下の2つを抽出してください:
1. general_name（コア概念）: この商品のカテゴリーを表す最も重要な単語
2. keywords（キーワード配列）: 商品名に含まれる全ての意味のある単語（general_nameを含む）

ルール:
- general_nameは商品のコア概念（例: 食パン、ボトルコーヒー、牛乳、豚ひき肉）
- keywordsは検索に使える全ての単語を個別に抽出
- メーカー名、ブランド名、容量、数量も全てkeywordsに含める
- キーワードは辞書に登録できる単位で分割

例:
商品名: 「パスコ 超熟 6枚切り」
general_name: 食パン
keywords: ["食パン", "パスコ", "超熟", "6枚切り"]

商品名: 「ジョージア 無糖 1000ml」
general_name: ボトルコーヒー
keywords: ["ボトルコーヒー", "ジョージア", "無糖", "1000ml"]

商品名: 「明治おいしい牛乳 1000ml」
general_name: 牛乳
keywords: ["牛乳", "明治", "おいしい牛乳", "1000ml"]

商品名: 「日本ハム 豚ひき肉 200g」
general_name: 豚ひき肉
keywords: ["豚ひき肉", "日本ハム", "200g"]

以下のJSON形式で出力してください:
{{"general_name": "...", "keywords": ["...", "..."]}}"""

        try:
            response = self.llm_client.call_model(
                tier="stageh_extraction",
                prompt=prompt,
                model_name="gemini-2.5-flash",
                max_output_tokens=8192
            )

            if response.get("success"):
                content = response.get("content", "").strip()

                # JSONパース
                import json
                try:
                    # マークダウンのコードブロックを除去（```json ... ```）
                    if content.startswith("```"):
                        content = content.split("```")[1]
                        if content.startswith("json"):
                            content = content[4:]
                        content = content.strip()

                    result = json.loads(content)

                    # 必須フィールドの検証
                    if "general_name" in result and "keywords" in result:
                        # keywordsが配列であることを確認
                        if isinstance(result["keywords"], list):
                            return result
                        else:
                            logger.warning(f"keywords is not a list: {result}")
                    else:
                        logger.warning(f"Missing required fields in AI response: {result}")

                except json.JSONDecodeError as e:
                    logger.error(f"JSON parse error: {e}, content: {content}")
                    return None

            return None

        except Exception as e:
            logger.error(f"Gemini API呼び出しエラー: {e}")
            return None
