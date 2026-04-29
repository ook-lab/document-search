"""
家計簿データのDB保存ハンドラー

Stage Hで処理された家計簿データを2層構造のDBに保存する
- Rawdata_RECEIPT_shops（親）
- Rawdata_RECEIPT_items（子）← OCR生データ + 標準化データ統合
- 99_lg_image_proc_log（処理ログ）
"""

from typing import Dict, List
from loguru import logger
from datetime import datetime

from shared.common.database.client import DatabaseClient


class KakeiboDBHandler:
    """家計簿データのDB保存専用ハンドラー"""

    def __init__(self, db_client: DatabaseClient):
        """
        Args:
            db_client: データベースクライアント
        """
        self.db = db_client

    def save_receipt(
        self,
        stage_h_output: Dict,
        file_name: str,
        drive_file_id: str,
        model_name: str,
        source_folder: str,
        owner_id: str = None
    ) -> Dict:
        """
        レシートデータを3層構造でDBに保存

        Args:
            stage_h_output: Stage Hの出力
            file_name: ファイル名
            drive_file_id: Google DriveのファイルID
            model_name: 使用したモデル名
            source_folder: ソースフォルダ
            owner_id: オーナーID（Phase 3 必須）

        Returns:
            Dict: {"receipt_id": "...", "transaction_ids": [...], ...}

        Raises:
            ValueError: owner_id が指定されていない場合
        """
        # Phase 3: owner_id 必須チェック
        if owner_id is None:
            raise ValueError("owner_id is required for save_receipt (Phase 3)")

        try:
            # 1. レシート（親）を登録
            receipt_id = self._insert_receipt(
                stage_h_output["receipt"],
                file_name,
                drive_file_id,
                model_name,
                source_folder,
                owner_id
            )

            # 2. トランザクション（OCRデータ + 標準化データを統合して登録）
            transaction_ids = []

            for line_num, item_data in enumerate(stage_h_output["items"], start=1):
                item = item_data["raw_item"]
                normalized = item_data["normalized"]

                # displayed_amount は normalized から取得（レシート記載の金額）
                displayed_amount = normalized.get("displayed_amount") or item.get("amount") or 0

                # 値引き行でない場合は標準化データも含める
                is_discount = item.get("line_type") == "DISCOUNT"

                # 商品名を取得（nullや空文字列の場合は代替値を使用）
                product_name = normalized.get("product_name") or item.get("product_name") or item.get("line_text") or "不明"
                if not product_name or not product_name.strip():
                    product_name = "不明"

                transaction_id = self._insert_transaction(
                    receipt_id=receipt_id,
                    line_number=line_num,
                    line_type=item.get("line_type", "ITEM"),
                    ocr_raw_text=item.get("line_text", ""),
                    product_name=product_name,
                    quantity=normalized.get("quantity", item.get("quantity", 1)),
                    unit_price=item.get("unit_price"),
                    displayed_amount=displayed_amount,
                    discount_text=item.get("discount_text"),
                    base_price=normalized.get("base_price"),
                    tax_amount=normalized.get("tax_amount"),
                    tax_included_amount=normalized.get("tax_included_amount"),
                    tax_display_type=normalized.get("tax_display_type"),
                    tax_rate=normalized.get("tax_rate"),
                    # 標準化データ（値引き行以外）
                    normalized=normalized if not is_discount else None,
                    situation_id=stage_h_output["receipt"]["situation_id"] if not is_discount else None
                )
                transaction_ids.append(transaction_id)

            # 3. 処理ログを記録
            log_id = self._log_processing_success(
                file_name=file_name,
                drive_file_id=drive_file_id,
                receipt_id=receipt_id,
                transaction_ids=transaction_ids,
                model_name=model_name
            )

            logger.info(f"Saved receipt {receipt_id} with {len(transaction_ids)} items")

            return {
                "success": True,
                "receipt_id": receipt_id,
                "transaction_ids": transaction_ids,
                "log_id": log_id
            }

        except Exception as e:
            logger.error(f"Failed to save receipt: {e}")
            # エラーログを記録（エラー時は失敗しても続行）
            try:
                self._log_processing_error(
                    file_name=file_name,
                    drive_file_id=drive_file_id,
                    error_info={"error": str(e)},
                    model_name=model_name
                )
            except Exception as log_error:
                logger.warning(f"Failed to log error (ignoring): {log_error}")
            raise

    def _insert_receipt(
        self,
        receipt_data: Dict,
        file_name: str,
        drive_file_id: str,
        model_name: str,
        source_folder: str,
        owner_id: str
    ) -> str:
        """レシート情報をDBに登録（親テーブル）"""
        trans_date = datetime.strptime(receipt_data["date"], "%Y-%m-%d").date()

        data = {
            "transaction_date": receipt_data["date"],
            "shop_name": receipt_data["name"],
            "total_amount_check": receipt_data.get("total") or 0,
            "subtotal_amount": receipt_data.get("subtotal") or 0,
            "tax_8_amount": receipt_data.get("tax_8_amount"),  # 8%消費税額
            "tax_10_amount": receipt_data.get("tax_10_amount"),  # 10%消費税額
            "tax_8_subtotal": receipt_data.get("tax_8_subtotal"),  # 8%対象額（税抜）
            "tax_10_subtotal": receipt_data.get("tax_10_subtotal"),  # 10%対象額（税抜）
            "image_path": f"99_Archive/{trans_date.strftime('%Y-%m')}/{file_name}",
            "drive_file_id": drive_file_id,
            "source_folder": source_folder,
            "ocr_model": model_name,
            "workspace": "household",
            "is_verified": False,
            "owner_id": owner_id  # Phase 3: 必須
        }

        result = self.db.client.table("Rawdata_RECEIPT_shops").insert(data).execute()
        return result.data[0]["id"]

    def _insert_transaction(
        self,
        receipt_id: str,
        line_number: int,
        line_type: str,
        ocr_raw_text: str,
        product_name: str,
        quantity: int,
        unit_price: int,
        displayed_amount: int,
        discount_text: str = None,
        base_price: int = None,
        tax_amount: int = None,
        tax_included_amount: int = None,
        tax_display_type: str = None,
        tax_rate: int = None,
        normalized: Dict = None,
        situation_id: str = None
    ) -> str:
        """トランザクション情報 + 標準化データをDBに登録（統合テーブル）"""
        data = {
            "receipt_id": receipt_id,
            "line_number": line_number,
            "line_type": line_type,
            "ocr_raw_text": ocr_raw_text,
            "product_name": product_name,
            "item_name": product_name,  # product_nameと同じ値を設定
            "quantity": quantity,
            "unit_price": unit_price,
            "displayed_amount": displayed_amount,  # レシート記載の表示金額
            "discount_text": discount_text,
            "base_price": base_price,  # 本体価（税抜）
            "tax_amount": tax_amount,  # 税額
            "tax_included_amount": tax_included_amount,  # 税込価
            "tax_display_type": tax_display_type,  # 外税or内税
            "tax_rate": tax_rate  # 税率（8 or 10）
        }

        # 標準化データがあれば追加
        if normalized:
            data.update({
                "official_name": normalized["product_name"],
                "category_id": normalized.get("category_id"),
                "situation_id": situation_id,
                "std_unit_price": base_price,  # 税抜本体価格
                "std_amount": tax_included_amount,  # 税込額
                "needs_review": normalized.get("tax_rate_source") != "master"
            })

        result = self.db.client.table("Rawdata_RECEIPT_items").insert(data).execute()
        return result.data[0]["id"]

    def _log_processing_success(
        self,
        file_name: str,
        drive_file_id: str,
        receipt_id: str,
        transaction_ids: List[str],
        model_name: str = None
    ) -> str:
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

        # file_name をユニークキーとして upsert
        result = self.db.client.table("99_lg_image_proc_log").upsert(
            log_data,
            on_conflict="file_name"
        ).execute()
        return result.data[0]["id"]

    def _log_processing_error(
        self,
        file_name: str,
        drive_file_id: str,
        error_info: Dict,
        model_name: str = None,
        receipt_id: str = None
    ):
        """処理エラーをログに記録"""
        log_data = {
            "file_name": file_name,
            "drive_file_id": drive_file_id,
            "status": "failed",
            "error_message": error_info.get("error")
        }
        if model_name:
            log_data["ocr_model"] = model_name
        if receipt_id:
            log_data["receipt_id"] = receipt_id

        # file_name をユニークキーとして upsert
        self.db.client.table("99_lg_image_proc_log").upsert(
            log_data,
            on_conflict="file_name"
        ).execute()
