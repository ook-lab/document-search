import urllib.request
import json
import sys

def main():
    url = "http://127.0.0.1:5055/pipeline-lab/api/extract_direct/ec7b8f4dad4b/0"
    payload = {"model": "gemini-2.5-flash-lite"}
    
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    
    try:
        print("APIリクエスト送信中 (AI直接抽出)...")
        with urllib.request.urlopen(req) as res:
            response_body = res.read().decode("utf-8")
            result = json.loads(response_body)
            
            if result.get("success"):
                # UTF-8で標準出力に書き出し、文字化けを防ぐ
                sys.stdout.buffer.write(result["markdown"].encode("utf-8"))
            else:
                print("APIエラー:", result.get("error"))
    except Exception as e:
        print("リクエスト失敗:", e)

if __name__ == "__main__":
    main()
