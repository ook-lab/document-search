import re

path = r'c:\Users\ookub\document-management-system\services\pdf-toolbox\blueprints\md_embedder.py'

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

new_prompt_general = r'''        prompt = """
        あなたは高度なOCRおよびデータ抽出システムです。
        提供された画像からすべての情報を抽出し、マークダウン形式で返してください。

        【思考プロセス（重要）】
        正確な抽出のために、以下の手順で思考してください。
        1. **全体俯瞰**: まず画像全体のタイトル、注釈を特定し、文書全体の目的と構造を把握します。
        2. **垂直・水平グリッドの認識**: 表を単なる「行の集まり」ではなく、**「垂直な列」と「水平な行」が交差するグリッド**として認識してください。
        3. **垂直アライメントの追跡 (Vertical Tracing)**: ヘッダー行の各項目の**水平方向の表示範囲（左端から右端まで）**を基準とし、その真下（垂直方向の延長線上）にあるデータをその列に割り当ててください。データがない列は詰めずに、必ず空のセルとして保持します。
        4. **論理展開と正規化**: 
           - 結合セル（複数の行や列にまたがる項目）の情報は、省略せずにすべての該当する行・列にコピーして補完してください。
           - すべての行において、パイプ `|` の総数をヘッダーの列数と完全に一致させてください。
           - 物理的な配置から「どの列に属するか」を判断する際、隣の列と混同しないよう一列ずつ独立して精査してください。

        【抽出ルール】
        1. **文書全体の構造化**: タイトル、注釈等の「表の外にある情報」を適切な見出し（# や ##）等を用いて必ず抽出に含めてください。
        2. **表データ (Tabular Data)**: Markdownのテーブル形式を使用してください。
        3. **結合セルの完全補完**: 縦・横に結合されたセルがある場合、その内容を**対応するすべての論理的なセルに繰り返し入力**してください。
        4. **空のセルの維持**: データがない列は左に詰めたりせず、必ず `| |` （スペース）を入れて列数を維持してください。
        5. **改行**: セル内で改行が必要な場合は `<br>` を使用してください。
        6. **言語**: 日本語を英語に翻訳しないでください。元の言語のまま抽出してください。
        7. **出力形式**: Markdownデータのみを出力し、説明は一切省いてください。
        """'''

pattern = re.compile(r'        prompt = """.*?"""', re.DOTALL)
if pattern.search(content):
    content = pattern.sub(new_prompt_general, content)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    print("Successfully generalized the prompt (removed calendar-specific logic).")
else:
    print("Could not find the prompt block to replace.")
