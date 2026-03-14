import sys
import os
import json

sys.path.append("c:/Users/ookub/document-management-system/services/kakeibo")

from app import app
from db_client import get_db
from transaction_processor import TransactionProcessor

def test_single_import():
    db = get_db()
    # Get one file from Drive (EASY folder)
    from drive_client import DriveClient
    from config import INBOX_EASY_FOLDER_ID, GEMINI_MODEL_EASY
    drive = DriveClient()
    files = drive.list_files_in_folder(INBOX_EASY_FOLDER_ID)
    if not files:
        print("No files found in EASY folder")
        return
    
    file_info = files[0]
    print(f"Testing with file: {file_info['name']}")
    
    # We will call the logic from app.py but manually for better control
    with app.app_context():
        # (Simplified logic from import_receipts)
        from gemini_client import GeminiClient
        llm = GeminiClient()
        processor = TransactionProcessor()
        
        # Download
        import tempfile
        with tempfile.TemporaryDirectory() as temp_dir:
            local_path = drive.download_file(file_info['id'], file_info['name'], temp_dir)
            with open(local_path, 'rb') as f:
                image_bytes = f.read()
            import base64
            image_base64 = base64.b64encode(image_bytes).decode('utf-8')
            
            from config import GEMINI_PROMPT, GEMINI_TEMPERATURE
            ocr_response = llm.generate_with_images(
                prompt=GEMINI_PROMPT,
                image_data=image_base64,
                model=GEMINI_MODEL_EASY,
                temperature=GEMINI_TEMPERATURE
            )
            
            json_start = ocr_response.find('{')
            json_end = ocr_response.rfind('}') + 1
            ocr_result = json.loads(ocr_response[json_start:json_end])
            
            if "transaction_info" in ocr_result and "date" in ocr_result["transaction_info"]:
                ocr_result["transaction_date"] = ocr_result["transaction_info"]["date"]
            if "shop_info" in ocr_result and "name" in ocr_result["shop_info"]:
                ocr_result["shop_name"] = ocr_result["shop_info"]["name"]
            
            # Convert items
            for item in ocr_result.get("items", []):
                item["line_type"] = item.get("line_type", "ITEM")
                item["line_text"] = item.get("line_text", item.get("product_name"))

            # Process
            print("Calling processor.process ...")
            try:
                result = processor.process(
                    ocr_result=ocr_result,
                    file_name=file_info['name'],
                    drive_file_id=file_info['id'],
                    model_name=GEMINI_MODEL_EASY,
                    source_folder="INBOX_EASY"
                )
                print(f"Result: {result}")
            except Exception as e:
                with open("error.log", "w", encoding="utf-8") as f:
                    import traceback
                    f.write(traceback.format_exc())
                    f.write("\n\n")
                    f.write(str(e))
                print(f"Processor CRASHED. Check error.log")

if __name__ == "__main__":
    test_single_import()
