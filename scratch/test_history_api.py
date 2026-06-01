import requests
import json

BASE_URL = "http://127.0.0.1:5058"

def test_history():
    print("Inserting test problem...")
    test_data = {
        "display_id": "TEST-HIST-001",
        "source_book": "テスト教材A",
        "chapter": "第1章",
        "unit": "平面図形",
        "strategy_summary": "テスト解法",
        "problem_markdown": "テスト問題",
        "explanation_markdown": "テスト解説"
    }
    
    res_save = requests.post(f"{BASE_URL}/api/problems", json=test_data)
    print(f"Save Status: {res_save.status_code}")
    assert res_save.status_code == 201
    
    # Also save another chapter for the same source book
    test_data_2 = test_data.copy()
    test_data_2["display_id"] = "TEST-HIST-002"
    test_data_2["chapter"] = "第2章"
    res_save_2 = requests.post(f"{BASE_URL}/api/problems", json=test_data_2)
    assert res_save_2.status_code == 201
    
    # Save a different book
    test_data_3 = {
        "display_id": "TEST-HIST-003",
        "source_book": "テスト教材B",
        "chapter": "第10章",
        "unit": "立体図形",
        "strategy_summary": "テスト解法B",
        "problem_markdown": "テスト問題B",
        "explanation_markdown": "テスト解説B"
    }
    res_save_3 = requests.post(f"{BASE_URL}/api/problems", json=test_data_3)
    assert res_save_3.status_code == 201

    try:
        print("\nFetching source books history...")
        res = requests.get(f"{BASE_URL}/api/history/source-books")
        print(f"Status Code: {res.status_code}")
        assert res.status_code == 200
        books = res.json()
        print(f"Source books: {json.dumps(books, ensure_ascii=False)}")
        assert "テスト教材A" in books
        assert "テスト教材B" in books
        
        print("\nFetching chapters history for 'テスト教材A'...")
        res2 = requests.get(f"{BASE_URL}/api/history/chapters?source_book=テスト教材A")
        print(f"Status Code: {res2.status_code}")
        assert res2.status_code == 200
        chapters = res2.json()
        print(f"Chapters for 'テスト教材A': {json.dumps(chapters, ensure_ascii=False)}")
        assert "第1章" in chapters
        assert "第2章" in chapters
        assert "第10章" not in chapters

        print("\nFetching chapters history for 'テスト教材B'...")
        res3 = requests.get(f"{BASE_URL}/api/history/chapters?source_book=テスト教材B")
        print(f"Status Code: {res3.status_code}")
        assert res3.status_code == 200
        chapters_b = res3.json()
        print(f"Chapters for 'テスト教材B': {json.dumps(chapters_b, ensure_ascii=False)}")
        assert "第10章" in chapters_b
        assert "第1章" not in chapters_b

        print("\nFetching unit history for 'テスト教材A' and '第1章'...")
        res_unit_a1 = requests.get(f"{BASE_URL}/api/history/unit?source_book=テスト教材A&chapter=第1章")
        print(f"Status Code: {res_unit_a1.status_code}")
        assert res_unit_a1.status_code == 200
        unit_data_a1 = res_unit_a1.json()
        print(f"Unit: {unit_data_a1.get('unit')}")
        assert unit_data_a1.get("unit") == "平面図形"

        print("\nFetching unit history for 'テスト教材B' and '第10章'...")
        res_unit_b10 = requests.get(f"{BASE_URL}/api/history/unit?source_book=テスト教材B&chapter=第10章")
        assert res_unit_b10.status_code == 200
        assert res_unit_b10.json().get("unit") == "立体図形"

        # Verify that parsing markdown does NOT extract textbook or chapter or unit,
        # but DOES extract strategy summary (解法の核心) and cleans it up from the text
        print("\nTesting parse endpoint parsing rules...")
        markdown_sample = """# [TEST-PARSED-1] 算数特別解説 第5章
- 単元: 平面図形

## 【問題】
右の図のような、すべての辺の長さが 10 cm の四角錐 O-ABCD があります。
【解法の核心】
3:4:5の相似連鎖

## 【解説】
解説テキスト
"""
        res_parse = requests.post(f"{BASE_URL}/api/problems/parse", json={"markdown": markdown_sample})
        print(f"Parse Status Code: {res_parse.status_code}")
        assert res_parse.status_code == 200
        parse_result = res_parse.json()
        print(f"Parsed JSON: {json.dumps(parse_result, ensure_ascii=False, indent=2)}")
        
        # Textbook name, chapter and unit MUST be empty
        assert parse_result.get("source_book") == ""
        assert parse_result.get("chapter") == ""
        assert parse_result.get("unit") == ""
        
        # Display ID must be extracted
        assert parse_result.get("display_id") == "TEST-PARSED-1"
        
        # Strategy summary must be extracted
        assert parse_result.get("strategy_summary") == "3:4:5の相似連鎖"
        
        # Strategy summary must be removed from problem markdown
        assert "【解法の核心】" not in parse_result.get("problem_markdown")
        assert "3:4:5の相似連鎖" not in parse_result.get("problem_markdown")
        print("Parsing checks PASSED!")

    finally:
        print("\nCleaning up test problems...")
        for did in ["TEST-HIST-001", "TEST-HIST-002", "TEST-HIST-003"]:
            # Find and delete
            res_list = requests.get(f"{BASE_URL}/api/problems?q={did}")
            if res_list.status_code == 200 and res_list.json():
                pid = res_list.json()[0]["id"]
                requests.delete(f"{BASE_URL}/api/problems/{pid}")
                print(f"Deleted {did}")

if __name__ == "__main__":
    test_history()
