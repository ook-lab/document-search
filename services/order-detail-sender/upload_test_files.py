import sys
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

from google_drive_connector import GoogleDriveConnector

def upload_tests():
    drive = GoogleDriveConnector()
    folder_id = "11kzVIXBGob4b-EALJtoXmG_KcOmasJje"
    
    # テスト対象ファイルのパス
    test_paper = "C:/Users/ookub/.gemini/antigravity/brain/49c5fe6d-5028-42d5-8c75-d1a9491edda5/scratch/test_paper_monthly_report.pdf"
    test_matching = "C:/Users/ookub/.gemini/antigravity/brain/49c5fe6d-5028-42d5-8c75-d1a9491edda5/scratch/test_matching_detail.pdf"
    
    print(f"Uploading paper monthly report to folder {folder_id}...")
    fid_paper = drive.upload_file_from_path(test_paper, folder_id=folder_id)
    print(f"Uploaded paper PDF! ID: {fid_paper}")
    
    print(f"Uploading matching detail to folder {folder_id}...")
    fid_matching = drive.upload_file_from_path(test_matching, folder_id=folder_id)
    print(f"Uploaded matching PDF! ID: {fid_matching}")

if __name__ == "__main__":
    upload_tests()
