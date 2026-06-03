import urllib.request
import json
import sys

def main():
    try:
        # 1. Get subjects
        url_subjects = "https://quiz-maker-983922127476.asia-northeast1.run.app/api/subjects"
        with urllib.request.urlopen(url_subjects) as res:
            subjects = json.loads(res.read().decode('utf-8'))
        
        if not subjects:
            print("No subjects found.")
            sys.exit(1)
            
        subject_id = subjects[0]['id']
        subject_name = subjects[0]['name']
        print(f"Using subject: {subject_name} ({subject_id})")

        # 2. Post test history
        url_save = "https://quiz-maker-983922127476.asia-northeast1.run.app/api/save-history"
        payload = {
            "subject_id": subject_id,
            "source_name": "test_script_run.pdf",
            "history": [
                {
                    "question": "Pythonの接続テスト問題です。この履歴は正常に保存されますか？",
                    "correctAnswer": "はい",
                    "userAnswer": "はい",
                    "isCorrect": True
                }
            ]
        }
        
        req = urllib.request.Request(
            url_save, 
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
            method='POST'
        )
        
        with urllib.request.urlopen(req) as res:
            response_data = json.loads(res.read().decode('utf-8'))
            print("Response:", response_data)
            if response_data.get("success"):
                print("Save history test succeeded!")
            else:
                print("Save history test failed:", response_data)
                sys.exit(1)
                
    except Exception as e:
        print("Error during test:", e)
        sys.exit(1)

if __name__ == "__main__":
    main()
