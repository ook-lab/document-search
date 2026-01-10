"""
7要素データ構造の検証スクリプト

DB に保存された商品データが7要素構造になっているか確認
"""
from shared.common.database.client import DatabaseClient
from loguru import logger
import json


def verify_7_elements():
    """7要素データ構造を検証"""
    db = DatabaseClient(use_service_role=True)

    # レシート一覧を取得
    receipts = db.client.table("Rawdata_RECEIPT_shops").select("*").limit(3).execute()

    for receipt in receipts.data:
        logger.info(f"\n{'='*80}")
        logger.info(f"📄 レシート: {receipt['shop_name']} ({receipt['transaction_date']})")
        logger.info(f"   receipt_id: {receipt['id']}")
        logger.info(f"   合計: {receipt['total_amount_check']}円")
        logger.info(f"   小計: {receipt['subtotal_amount']}円")

        # 外税 or 内税判定
        if receipt['subtotal_amount'] and receipt['total_amount_check']:
            if receipt['subtotal_amount'] < receipt['total_amount_check']:
                tax_type = "外税"
            else:
                tax_type = "内税"
        else:
            tax_type = "不明"
        logger.info(f"   税表示タイプ: {tax_type}")

        # トランザクションを取得
        transactions = db.client.table("Rawdata_RECEIPT_items").select("*").eq("receipt_id", receipt['id']).execute()

        logger.info(f"\n   商品一覧:")
        for trans in transactions.data:
            # 正規化データは同じレコードに含まれる
            logger.info(f"\n   商品: {trans['product_name']}")
            logger.info(f"     1. 数量:      {trans['quantity']}")
            logger.info(f"     2. 表示額:    {trans.get('unit_price', 'N/A')}円 (※unit_priceから推測)")
            logger.info(f"     3. 外or内:    (レシートレベルで{tax_type})")
            logger.info(f"     4. 税率:      {trans.get('tax_rate', 'N/A')}%")
            logger.info(f"     5. 本体価:    {trans.get('std_unit_price', 'N/A')}円")
            logger.info(f"     6. 税額:      {trans.get('tax_amount', 'N/A')}円")
            logger.info(f"     7. 税込価:    {trans.get('std_amount', 'N/A')}円")

            # 計算検証
            if trans.get('std_unit_price') is not None and trans.get('std_amount') is not None and trans.get('tax_amount') is not None:
                if tax_type == "外税":
                    expected_total = trans['std_unit_price'] + trans['tax_amount']
                    if expected_total == trans['std_amount']:
                        logger.success(f"     ✅ 計算正しい: {trans['std_unit_price']} + {trans['tax_amount']} = {trans['std_amount']}")
                    else:
                        logger.warning(f"     ⚠️  計算ずれ: {trans['std_unit_price']} + {trans['tax_amount']} ≠ {trans['std_amount']}")
                else:
                    expected_base = trans['std_amount'] - trans['tax_amount']
                    if expected_base == trans['std_unit_price']:
                        logger.success(f"     ✅ 計算正しい: {trans['std_amount']} - {trans['tax_amount']} = {trans['std_unit_price']}")
                    else:
                        logger.warning(f"     ⚠️  計算ずれ: {trans['std_amount']} - {trans['tax_amount']} ≠ {trans['std_unit_price']}")


if __name__ == "__main__":
    verify_7_elements()
