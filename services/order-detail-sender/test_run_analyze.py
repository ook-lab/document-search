import sys
import tempfile
import json
from pathlib import Path

# パスの追加
project_dir = Path("c:/Users/ookub/document-management-system")
sys.path.insert(0, str(project_dir))
sys.path.insert(0, str(project_dir / "services" / "order-detail-sender"))

# 環境変数の読み込み
try:
    from dotenv import load_dotenv
    load_dotenv(project_dir / ".env")
except Exception:
    pass

from app import analyze_and_split_pdfs_gdrive

def test_analyze():
    # デフォルトのテスト用フォルダID
    folder_id = "11kzVIXBGob4b-EALJtoXmG_KcOmasJje"
    
    with tempfile.TemporaryDirectory() as temp_dir:
        print(f"Analyzing folder: {folder_id}")
        try:
            preview_data, prefix = analyze_and_split_pdfs_gdrive(folder_id, temp_dir)
            print("\n--- Analysis Success! ---")
            print(f"Prefix: {prefix}")
            print(f"Total Jobs Found: {len(preview_data)}")
            
            # 結果をきれいに表示
            for job_id, info in preview_data.items():
                print(f"\nJob ID: {job_id}")
                print(f"  Name: {info['name']}")
                print(f"  Code: {info['code']}")
                print(f"  Email: {info['email']}")
                print(f"  Type: {info['doc_type_name']} ({info['doc_type']})")
                print(f"  Sent: {info['sent']}")
                print(f"  Source PDFs: {info['source_pdf_names']}")
                print(f"  Page detail: {list(info['jobs'].keys())}")
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"Error during analysis: {e}")

if __name__ == "__main__":
    test_analyze()
