"""Verify the latest receipt's 7-element structure"""
from shared.common.database.client import DatabaseClient
from loguru import logger


def verify_latest():
    db = DatabaseClient(use_service_role=True)

    # Get the most recent receipt (by created_at)
    receipts = db.client.table("Rawdata_RECEIPT_shops").select("*").order("created_at", desc=True).limit(1).execute()

    if not receipts.data:
        logger.error("No receipts found!")
        return

    receipt = receipts.data[0]
    logger.info(f"\n{'='*80}")
    logger.info(f"📄 最新レシート: {receipt['shop_name']} ({receipt['transaction_date']})")
    logger.info(f"   receipt_id: {receipt['id']}")
    logger.info(f"   合計: {receipt['total_amount_check']}円")
    logger.info(f"   小計: {receipt['subtotal_amount']}円")

    # Tax type detection
    if receipt['subtotal_amount'] and receipt['total_amount_check']:
        if receipt['subtotal_amount'] < receipt['total_amount_check']:
            tax_type = "外税"
        else:
            tax_type = "内税"
    else:
        tax_type = "不明"
    logger.info(f"   税表示タイプ: {tax_type}")

    # Get transactions
    transactions = db.client.table("Rawdata_RECEIPT_items").select("*").eq("receipt_id", receipt['id']).execute()
    logger.info(f"\n   商品数: {len(transactions.data)}件")

    for trans in transactions.data[:3]:  # Show first 3 items
        # 正規化データは同じレコードに含まれる
        logger.info(f"\n   商品: {trans['product_name']}")
        logger.info(f"     1. 数量:      {trans['quantity']}")
        logger.info(f"     2. 表示額:    {trans.get('unit_price', 'N/A')}円")
        logger.info(f"     3. 外or内:    {tax_type}")
        logger.info(f"     4. 税率:      {trans.get('tax_rate', 'N/A')}%")
        logger.info(f"     5. 本体価:    {trans.get('std_unit_price', 'N/A')}円")
        logger.info(f"     6. 税額:      {trans.get('tax_amount', 'N/A')}円")
        logger.info(f"     7. 税込価:    {trans.get('std_amount', 'N/A')}円")

        # Verify calculation
        if trans.get('std_unit_price') is not None and trans.get('std_amount') is not None and trans.get('tax_amount') is not None:
            if tax_type == "外税":
                expected = trans['std_unit_price'] + trans['tax_amount']
                if expected == trans['std_amount']:
                    logger.success(f"     ✅ 計算正しい: {trans['std_unit_price']} + {trans['tax_amount']} = {trans['std_amount']}")
                else:
                    logger.warning(f"     ⚠️  計算ずれ: {trans['std_unit_price']} + {trans['tax_amount']} = {expected} ≠ {trans['std_amount']}")
            else:
                expected = trans['std_amount'] - trans['tax_amount']
                if expected == trans['std_unit_price']:
                    logger.success(f"     ✅ 計算正しい: {trans['std_amount']} - {trans['tax_amount']} = {trans['std_unit_price']}")
                else:
                    logger.warning(f"     ⚠️  計算ずれ: {trans['std_amount']} - {trans['tax_amount']} = {expected} ≠ {trans['std_unit_price']}")


if __name__ == "__main__":
    verify_latest()
