import sys
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

from app import app, load_sent_status_gdrive

def test_send():
    # Flaskのテストクライアントを作成
    client = app.test_client()
    
    # テスト対象フォルダURL
    folder_url = "https://drive.google.com/drive/u/0/folders/11kzVIXBGob4b-EALJtoXmG_KcOmasJje"
    
    # 送信対象のジョブIDを指定（ここではダミーのアドレスに送るか、テスト的に無害なジョブを選択）
    # 例として、宛先マスタにテスト用のメールアドレスを登録して送信する
    # 一括送信APIを呼び出す
    payload = {
        "folder_url": folder_url,
        "codes": ["5020_order"] # (株)DNP出版プロダクツ宛の注文明細書ジョブ
    }
    
    print("Calling /api/send_emails via TestClient...")
    response = client.post(
        "/api/send_emails",
        data=json.dumps(payload),
        content_type="application/json"
    )
    
    print(f"\nResponse Code: {response.status_code}")
    try:
        res_data = response.get_json()
        print("Response JSON:")
        print(json.dumps(res_data, indent=2, ensure_ascii=False))
    except Exception as e:
        print(f"Error decoding response JSON: {e}")
        print(f"Raw data: {response.data}")

if __name__ == "__main__":
    test_send()
