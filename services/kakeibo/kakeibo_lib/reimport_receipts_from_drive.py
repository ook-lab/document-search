"""
Google Drive の INBOX からレシート画像を取り込み、OCR 後に DB へ登録する。

Flask の POST /api/receipts/import と同じ処理経路（services/kakeibo のモジュール）を使う。
GitHub Actions の import-receipts ワークフローから呼ばれる想定。
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import tempfile
from pathlib import Path

_SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SERVICE_ROOT))

from config import (
    ARCHIVE_FOLDER_ID,
    ERROR_FOLDER_ID,
    GEMINI_MODEL_EASY,
    GEMINI_MODEL_HARD,
    GEMINI_PROMPT,
    GEMINI_TEMPERATURE,
    INBOX_EASY_FOLDER_ID,
    INBOX_HARD_FOLDER_ID,
)
from db_client import get_db
from drive_client import DriveClient
from gemini_client import GeminiClient
from transaction_processor import TransactionProcessor


def main() -> None:
    parser = argparse.ArgumentParser(description="Import receipts from Drive INBOX folders.")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    batch_limit = max(1, args.limit)

    drive = DriveClient()
    llm = GeminiClient()
    processor = TransactionProcessor()

    folders = [
        {"id": INBOX_EASY_FOLDER_ID, "model": GEMINI_MODEL_EASY, "name": "INBOX_EASY"},
        {"id": INBOX_HARD_FOLDER_ID, "model": GEMINI_MODEL_HARD, "name": "INBOX_HARD"},
    ]

    processed = 0
    success = 0
    failed = 0
    errors: list[str] = []

    for folder in folders:
        if not folder["id"]:
            continue

        try:
            files = drive.list_files_in_folder(folder["id"])
            image_files = [f for f in files if f.get("mimeType", "").startswith("image/")]
        except Exception as e:
            errors.append(f"フォルダ取得エラー ({folder['name']}): {e}")
            continue

        db = get_db()
        log_res = (
            db.table("99_lg_image_proc_log")
            .select("drive_file_id")
            .eq("status", "success")
            .execute()
        )
        already_done = {r["drive_file_id"] for r in log_res.data if r.get("drive_file_id")}

        for file_info in image_files:
            if processed >= batch_limit:
                break

            file_id = file_info["id"]
            file_name = file_info["name"]

            if file_id in already_done:
                if ARCHIVE_FOLDER_ID:
                    drive.move_file(file_id, ARCHIVE_FOLDER_ID)
                continue

            processed += 1

            try:
                with tempfile.TemporaryDirectory() as temp_dir:
                    local_path = drive.download_file(file_id, file_name, temp_dir)
                    if not local_path:
                        raise RuntimeError("ダウンロード失敗")

                    with open(local_path, "rb") as f:
                        image_bytes = f.read()
                    image_base64 = base64.b64encode(image_bytes).decode("utf-8")

                    ocr_response = llm.generate_with_images(
                        prompt=GEMINI_PROMPT,
                        image_data=image_base64,
                        model=folder["model"],
                        temperature=GEMINI_TEMPERATURE,
                    )

                    json_start = ocr_response.find("{")
                    json_end = ocr_response.rfind("}") + 1
                    if json_start == -1 or json_end == 0:
                        raise RuntimeError("JSONが見つかりません")

                    ocr_result = json.loads(ocr_response[json_start:json_end])

                    if "error" in ocr_result:
                        raise RuntimeError(
                            f"OCRエラー: {ocr_result.get('message', ocr_result['error'])}"
                        )

                    if "transaction_info" in ocr_result and "date" in ocr_result["transaction_info"]:
                        ocr_result["transaction_date"] = ocr_result["transaction_info"]["date"]

                    if "shop_info" in ocr_result and "name" in ocr_result["shop_info"]:
                        ocr_result["shop_name"] = ocr_result["shop_info"]["name"]

                    if "items" in ocr_result:
                        for item in ocr_result["items"]:
                            if "line_type" not in item:
                                item["line_type"] = "ITEM"
                            if "line_text" not in item and "product_name" in item:
                                item["line_text"] = item["product_name"]

                    if "amounts" in ocr_result:
                        amounts = ocr_result["amounts"]
                        ocr_result["subtotal_amount"] = amounts.get("subtotal")
                        ocr_result["total_amount_check"] = amounts.get("total")
                        ocr_result["tax_summary"] = {
                            "tax_8_subtotal": amounts.get("tax_8_base"),
                            "tax_8_amount": amounts.get("tax_8_amount"),
                            "tax_10_subtotal": amounts.get("tax_10_base"),
                            "tax_10_amount": amounts.get("tax_10_amount"),
                            "total_amount": amounts.get("total"),
                            "tax_type": amounts.get("tax_type", "内税"),
                        }

                    result = processor.process(
                        ocr_result=ocr_result,
                        file_name=file_name,
                        drive_file_id=file_id,
                        model_name=folder["model"],
                        source_folder=folder["name"],
                    )

                    if "error" in result:
                        raise RuntimeError(result.get("message", result["error"]))

                    if ARCHIVE_FOLDER_ID:
                        drive.move_file(file_id, ARCHIVE_FOLDER_ID)

                    success += 1

            except Exception as e:
                failed += 1
                errors.append(f"{file_name}: {e}")
                if ERROR_FOLDER_ID:
                    drive.move_file(file_id, ERROR_FOLDER_ID)

    summary = {
        "status": "success",
        "processed": processed,
        "success": success,
        "failed": failed,
        "errors": errors[:10],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
