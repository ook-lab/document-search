import re

md_text = """# 算数・立体図形 切断と平面投影分析

## 【問題】
右の図のような、辺の長さがすべて $10\\text{ cm}$ の四角錐 $O\\text{-}ABCD$ があります。
辺 $OA$, $OB$, $OC$ 上に点 $P$, $Q$, $R$ を、
$$(OP\\text{ の長さ}) = (OR\\text{ の長さ}),\\quad (OQ\\text{ の長さ}) = 6\\text{ cm}$$
となるようにとると、$3$ 点 $P$, $Q$, $R$ を通る平面上に点 $D$ があります。
このとき、$OP$（$OR$）の長さは何 $\\text{cm}$ になるか求めなさい。

### 【図1】四角錐 $O\\text{-}ABCD$ の切断見取り図
![切断見取り図](data:image/png;base64,iVBORw0KGgo...)

---

## 【解答】
**答 7.5 cm**

---

## 【解法の核心】
立体図形の切断に関する比の算出は、対角線を通る面を正面から見た投影図（断面図）上で行い、天秤による重心モーメント、または平行線と砂時計型相似（相似比）の利用で捉えるのが定石です。

---

## 【解説】
解説テキスト...
"""

data = {}

# 2. 問題と解説の境界抽出
problem_match = re.search(r'^##\s*【?\s*(?:問題|問題文)\s*】?$', md_text, re.MULTILINE)
explanation_match = re.search(r'^##\s*【?\s*(?:解説|解説文)\s*】?$', md_text, re.MULTILINE)

problem_text = ""
explanation_text = ""

if problem_match:
    start_idx = problem_match.end()
    end_idx = explanation_match.start() if explanation_match else len(md_text)
    problem_text = md_text[start_idx:end_idx].strip()
    
if explanation_match:
    start_idx = explanation_match.end()
    explanation_text = md_text[start_idx:].strip()

# Correct character class with escaped hyphen or placing it at the end
core_match = re.search(
    r'^(?:[#\s-]*)?【?\s*(?:解法コア|コア戦略|解法の核心・企み|解法の核心)\s*】?\s*[:：\s]+\s*(.+)$', 
    md_text, 
    re.MULTILINE
)

if not core_match:
    core_match = re.search(
        r'^(?:[#\s-]*)?【?\s*(?:解法コア|コア戦略|解法の核心・企み|解法の核心)\s*】?\s*[:：\s]*\n\s*(.+)$', 
        md_text, 
        re.MULTILINE
    )

if core_match:
    print("MATCH FOUND!")
    print(f"Extracted: '{core_match.group(1).strip()}'")
    
    pattern_same = r'^(?:[#\s-]*)?【?\s*(?:解法コア|コア戦略|解法の核心・企み|解法の核心)\s*】?\s*[:：\s]+\s*.+$'
    problem_text = re.sub(pattern_same, "", problem_text, flags=re.MULTILINE)
    
    pattern_next = r'^(?:[#\s-]*)?【?\s*(?:解法コア|コア戦略|解法の核心・企み|解法の核心)\s*】?\s*[:：\s]*\n\s*.+$'
    problem_text = re.sub(pattern_next, "", problem_text, flags=re.MULTILINE)
    
    print("\n--- Cleaned problem_text ---")
    print(problem_text)
else:
    print("NO MATCH")
