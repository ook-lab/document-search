import re

path = r'c:\Users\ookub\document-management-system\services\pdf-toolbox\blueprints\md_embedder.py'

with open(path, 'r', encoding='utf-8', errors='ignore') as f:
    content = f.read()

new_prompt_full = r'''        prompt = """
        あなたは高度なOCRおよびデータ抽出システムです。
        提供された画像からすべての情報を抽出し、マークダウン形式で返してください。

        【思考プロセス（重要）】
        正確な抽出のために、以下の手順で思考してください。
        1. **全体俯瞰**: まず画像全体のタイトル、日付、注釈を特定し、文書全体の目的と構造を把握します。
        2. **垂直・水平グリッドの認識**: 表を単なる「行の集まり」ではなく、**「垂直な列」と「水平な行」が交差するグリッド**として認識してください。
        3. **垂直アライメントの追跡 (Vertical Tracing)**: ヘッダーの各項目（「月」「火」「水」等）の**水平方向の中心座標**を基準線（コンテナ）とし、その真下にあるデータをその列に割り当ててください。データがない列は飛ばさず、必ず空のセルとして保持します。
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
        """'''

# Use a non-greedy match to find the prompt assignment and replace it entirely
# We look for prompt = """ ... """ and replace it
pattern = re.compile(r'        prompt = """.*?"""', re.DOTALL)
if pattern.search(content):
    content = pattern.sub(new_prompt_full, content)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    print("Successfully cleaned up the prompt block.")
else:
    print("Could not find the prompt block to replace.")
