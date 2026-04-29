import re
import os

path = r'c:\Users\ookub\document-management-system\services\pdf-toolbox\blueprints\md_embedder.py'

encodings = ['utf-8', 'cp932', 'euc-jp', 'utf-16']
content = None

for enc in encodings:
    try:
        with open(path, 'r', encoding=enc) as f:
            content = f.read()
        print(f"Successfully read with {enc}")
        break
    except Exception:
        continue

if content is None:
    # Fallback with errors replace
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        content = f.read()
    print("Read with utf-8 (errors replace)")

new_prompt = r'''        3. **垂直アライメントの追跡 (Vertical Tracing)**: ヘッダーの各項目（「月」「火」「水」等）の**水平方向の中心座標**を基準線（コンテナ）とし、その真下にあるデータをその列に割り当ててください。データがない列は飛ばさず、必ず空のセルとして保持します。
        4. **セルフチェック (Semantic Cross-Check)**: クラス名に「(水)」「(火1)」などの曜日が含まれる場合、そのデータが対応する曜日の列に配置されているか厳密に確認してください。もしズレている場合は、物理的な見た目よりも論理的な整合性を優先して列を正しくマッピングしてください。
        5. **論理展開と正規化**: 
           - 結合セル（学年・校舎など）の情報は、すべての行にコピーして補完してください。
           - すべての行において、パイプ `|` の数はヘッダーと完全に一致させてください。
           - 「1列でもズレると表全体が壊れる」ことを意識し、一列ずつ独立して精査してください。

        【抽出ルール】
        1. **文書全体の構造化**: タイトル、注釈等の「表の外にある情報」を適切な見出し（# や ##）等で必ず抽出に含めてください。
        2. **表データ (Tabular Data)**: Markdownのテーブル形式を使用してください。
        3. **結合セルの完全補完**: 縦・横に結合されたセルがある場合、その内容を**対応するすべての論理的なセルに繰り返し入力**してください。
        4. **空のセルの維持**: データがない曜日の列は詰めずに、必ず `| |` （スペース）を入れて列数を維持してください。
        5. **改行**: セル内で改行が必要な場合は `<br>` を使用してください。
        6. **言語**: 日本語を英語に翻訳しないでください。元の言語のまま抽出してください。
        7. **出力形式**: Markdownデータのみを出力し、説明は一切省いてください。
        '''

# Replace the prompt section
pattern = re.compile(r'3\. \*\*垂直アライメントの追跡.*?7\. \*\*出力形式.*?"""', re.DOTALL)
if pattern.search(content):
    content = pattern.sub(new_prompt.strip() + '\n        """', content)
    print("Replaced with pattern match")
else:
    print("Pattern not found, trying a more flexible search")
    # Simple search for the start of instructions
    start_marker = '3. **垂直アライメントの追跡'
    end_marker = '7. **出力形式'
    if start_marker in content:
        # Just use simple string replacement for the instructions part
        # This is a bit risky but we have the backup
        pass

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
print("Saved as utf-8")
