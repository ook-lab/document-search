import time
import requests

BASE_URL = "http://127.0.0.1:5058"

TEST_MARKDOWN = """# [GEO-002] 算数特別解説 第5章
- 単元: 立体図形
- 解法コア: 四角錐の切断公式、対角比の和の保存

## 問題
右の図のような、すべての辺の長さが 10 cm の四角錐 O-ABCD があります。辺 OA, OB, OC 上にそれぞれ点 P, Q, R を、(OP の長さ) = (OR の長さ)、(OQ の長さ) = 6 cm となるようにとると、3 点 P, Q, R を通る平面上に点 D があります。このとき、OP の長さを求めなさい。

## 解説
### Step 1: 四角錐 of 切断における比 of 公式 of 導入
底面が平行四辺形（正方形を含む）の四角錐 O-ABCD において、側面の各辺上の点 P, Q, R, S が同一平面上にあるとき、対角の位置にある辺の比の和は等しくなります。
$$\\frac{OA}{OP} + \\frac{OC}{OR} = \\frac{OB}{OQ} + \\frac{OD}{OS}$$

### Step 2: 条件の整理と代入
本問では、3点 P, Q, R を通る平面上に点 D があるため、公式の点 S は「頂点 D そのもの」とみなすことができます。したがって $OS = OD = 10\\text{ cm}$ です。
また、すべての辺の長さが 10 cm であるため、次の値がわかっています。
- $OA = OB = OC = OD = 10$
- $OP = OR = x$（求める長さ）
- $OQ = 6$
- $OS = 10$

これらを公式に代入します。
$$\\frac{10}{x} + \\frac{10}{x} = \\frac{10}{6} + \\frac{10}{10}$$

### Step 3: 計算と解答
右辺を計算します。
$$\\frac{10}{6} + \\frac{10}{10} = \\frac{5}{3} + 1 = \\frac{8}{3}$$

左辺をまとめます。
$$\\frac{20}{x} = \\frac{8}{3}$$

これを $x$ について解きます。
$$8x = 60$$
$$x = 7.5$$

よって、OP の長さは **$7.5\\text{ cm}$** です。"""

def run_tests():
    print("Waiting for server to fully initialize...")
    time.sleep(2)  # サーバー起動を待つ
    
    print("Cleaning up existing test data if any...")
    try:
        res = requests.get(f"{BASE_URL}/api/problems?q=GEO-002")
        if res.status_code == 200:
            problems = res.json()
            for p in problems:
                requests.delete(f"{BASE_URL}/api/problems/{p['id']}")
                print(f"Cleaned up old test record ID: {p['id']}")
    except Exception as e:
        print(f"Pre-cleanup warning: {e}")

    print("\n--- Test 1: Parse Markdown API ---")
    try:
        res = requests.post(f"{BASE_URL}/api/problems/parse", json={"markdown": TEST_MARKDOWN})
        print(f"Status Code: {res.status_code}")
        parsed_data = res.json()
        print("Parsed Output Summary:")
        print(f"  display_id: {parsed_data.get('display_id')}")
        print(f"  source_book: {parsed_data.get('source_book')}")
        print(f"  chapter: {parsed_data.get('chapter')}")
        print(f"  unit: {parsed_data.get('unit')}")
        print(f"  strategy_summary: {parsed_data.get('strategy_summary')}")
        assert parsed_data.get("display_id") == "GEO-002"
        # 教材名、章、単元はパースから除外され手動/履歴入力になったため空であることを確認
        assert parsed_data.get("source_book") == ""
        assert parsed_data.get("chapter") == ""
        assert parsed_data.get("unit") == ""
        print("Test 1: PASSED")
    except Exception as e:
        print(f"Test 1 FAILED: {e}")
        return

    print("\n--- Test 2: Save Problem API (DB & Drive 二重保存) ---")
    problem_id = None
    try:
        # 手動入力/履歴選択をシミュレートして値をセット
        parsed_data["source_book"] = "算数特別解説"
        parsed_data["chapter"] = "第5章"
        parsed_data["unit"] = "立体図形"
        
        # 登録
        res = requests.post(f"{BASE_URL}/api/problems", json=parsed_data)
        print(f"Status Code: {res.status_code}")
        result = res.json()
        print(f"Result: {result.get('status')} - Drive Synced: {result.get('drive_synced')}")
        assert res.status_code == 201
        print("Test 2: PASSED")
    except Exception as e:
        print(f"Test 2 FAILED: {e}")
        return

    print("\n--- Test 3: List Problems API (検索 & 取得) ---")
    try:
        # 一覧取得してIDを特定
        res = requests.get(f"{BASE_URL}/api/problems?q=GEO-002")
        print(f"Status Code: {res.status_code}")
        problems = res.json()
        print(f"Found {len(problems)} problems matching 'GEO-002'")
        assert len(problems) > 0
        problem_id = problems[0]["id"]
        print(f"Created Problem UUID: {problem_id}")
        print("Test 3: PASSED")
    except Exception as e:
        print(f"Test 3 FAILED: {e}")
        return

    print("\n--- Test 4: Generate Variant API (Gemini + Code Execution) ---")
    try:
        print("Requesting Gemini to generate a variant. This takes a few seconds...")
        res = requests.post(f"{BASE_URL}/api/problems/{problem_id}/generate-variant")
        print(f"Status Code: {res.status_code}")
        result = res.json()
        print("Generated Variant Preview:")
        variant_text = result.get("variant", "")
        print(variant_text[:300] + "...")
        assert "類題" in variant_text
        print("Test 4: PASSED")
    except Exception as e:
        print(f"Test 4 FAILED: {e}")

    print("\n--- Test 5: Cleanup (Delete Test Problem from DB) ---")
    if problem_id:
        try:
            res = requests.delete(f"{BASE_URL}/api/problems/{problem_id}")
            print(f"Status Code: {res.status_code}")
            result = res.json()
            print(f"Result: {result.get('status')} - {result.get('message')}")
            assert res.status_code == 200
            print("Test 5: PASSED")
        except Exception as e:
            print(f"Test 5 FAILED: {e}")

if __name__ == "__main__":
    run_tests()
