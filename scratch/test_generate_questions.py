import requests
import json

BASE_URL = "http://127.0.0.1:5000"

def test_generate_questions():
    print("Testing POST /api/generate-questions...")
    
    # 科目IDを取得
    res_subjects = requests.get(f"{BASE_URL}/api/subjects")
    subjects = res_subjects.json()
    if not subjects:
        print("No subjects found.")
        return
        
    target_subject = subjects[0]
    print(f"Using subject: {target_subject['name']} (id: {target_subject['id']})")
    
    # 苦手分析レベル: weak でテスト
    payload = {
        "subject_id": target_subject["id"],
        "question_count": 2,
        "history_analysis_level": "weak",
        "source_text": "鎌倉幕府は、1185年に源頼朝が守護・地頭を設置したことによって実質的に成立したとされる中世の武家政権である。それまでは1192年の征夷大将軍就任が成立とされていたが、現在では1185年説が有力である。頼朝は鎌倉を拠点とし、御家人と呼ばれる武士たちと主従関係を結んで支配を広げた。"
    }
    
    # 送信
    try:
        res = requests.post(f"{BASE_URL}/api/generate-questions", data=payload)
        if res.status_code == 200:
            result = res.json()
            print("Success!")
            print(f"Generated {len(result['questions'])} questions.")
            print(f"Tokens used: {result['tokens']}")
            for idx, q in enumerate(result['questions']):
                print(f"\nQ{idx+1}: {q['question']}")
                print(f"Options: {q['options']}")
                print(f"Correct: {q['correctAnswer']}")
                print(f"Short explanation: {q['explanationShort']}")
        else:
            print(f"Failed! Status: {res.status_code}")
            print(f"Error body: {res.text}")
    except Exception as e:
        print(f"Request exception: {e}")

if __name__ == "__main__":
    test_generate_questions()
