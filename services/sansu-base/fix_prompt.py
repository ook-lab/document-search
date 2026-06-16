# Fix double \r\n in app.py and update the diagram prompt
with open('app.py', 'rb') as f:
    raw = f.read()

# Fix double carriage returns: \r\r\n -> \r\n
fixed = raw.replace(b'\r\r\n', b'\r\n')

with open('app.py', 'wb') as f:
    f.write(fixed)

print("Fixed double CRLF")

# Now update the diagram prompt
with open('app.py', 'r', encoding='utf-8') as f:
    content = f.read()

content = content.replace('\r\n', '\n')

MARKER = '    elif part == "diagram":'
idx = content.find(MARKER)
end_marker = '\n    else:  # all'
end_idx = content.find(end_marker, idx)

if idx == -1 or end_idx == -1:
    print("FAILED: markers not found")
else:
    new_block = '    elif part == "diagram":\n        prompt = f"""あなたはプロの中学受験算数講師、および厳密な幾何学図形描画プログラムです。\n提供された画像（またはPDFの最初のページ）に含まれる図形を、数学的に正確に分析・計算し、Pythonコードで再現してください。\n\n【ヒント】:\n{hint if hint else "なし"}\n\n【最重要ルール】: まず図形が「2D平面図形」か「3D立体図形」かを判断してください。\n\n■ 図形が【3D立体図形】（直方体・立方体・三角錐・円錐・角柱・球など）の場合:\n1. from mpl_toolkits.mplot3d import Axes3D を使い ax = fig.add_subplot(111, projection=\'3d\') で3Dプロットを作成する\n2. 立体の全頂点座標を、問題文中の寸法・数値から数学的に計算して求める（目分量は絶対に使わない）\n3. 見える辺（手前側）は実線で、見えない辺（奥側・隠れ線）は破線（linestyle=\'--\', alpha=0.4）で描く\n4. 頂点ラベル（A, B, C...）や辺の長さを ax.text() で付ける\n5. 必ず ax.view_init(elev=30, azim=-60) を記述する（ユーザーがスライダーで視点を変更できるようにするため必須）\n6. plt.savefig("problem_diagram.png", dpi=150, bbox_inches="tight") で保存する\n\n■ 図形が【2D平面図形】（三角形・四角形・円・多角形など）の場合:\n1. 問題文中の寸法・角度・比率から数学的に計算して各頂点の座標を求める\n2. 補助線や角度マークも正確に描く\n3. ax.set_aspect(\'equal\') で縦横比を正確に保つ\n4. 頂点ラベル・辺の長さを plt.text や plt.annotate で記述する\n5. plt.savefig("problem_diagram.png", dpi=150, bbox_inches="tight") で保存する\n\n【共通禁止事項】:\n- plt.show() は絶対に呼ばない\n- 目分量の座標を使わない。必ず数値から計算した座標を使う\n- コードブロックタグ（```python など）を含めない\n\n【出力フォーマット】:\n必ず以下のJSON形式のみで出力してください（前置きの挨拶などは一切不要です）。\n{{\n  "matplotlib_code": "Pythonコード"\n}}"""'

    new_content = content[:idx] + new_block + content[end_idx:]

    with open('app.py', 'w', encoding='utf-8', newline='\n') as f:
        f.write(new_content)
    print("SUCCESS: prompt updated")

# Verify syntax
import subprocess, sys
result = subprocess.run([sys.executable, '-m', 'py_compile', 'app.py'], capture_output=True, text=True)
if result.returncode == 0:
    print("Syntax OK")
else:
    print("Syntax ERROR:", result.stderr)
