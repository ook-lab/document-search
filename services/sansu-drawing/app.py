import os
import sys
import re
import subprocess
import shutil
from pathlib import Path
import streamlit as st
from dotenv import load_dotenv

# PYTHONPATHの設定と環境変数のロード
_here = Path(__file__).resolve().parent
_repo = _here.parent.parent
if str(_repo) not in sys.path:
    sys.path.insert(0, str(_repo))
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

load_dotenv(_repo / ".env")
load_dotenv(_here / ".env")

import google.generativeai as genai

# Gemini APIの初期化
api_key = os.getenv("GOOGLE_AI_API_KEY")
if api_key:
    genai.configure(api_key=api_key)
else:
    st.warning("GOOGLE_AI_API_KEY が設定されていません。.env ファイルを確認してください。")

# ページ基本設定
st.set_page_config(
    page_title="図形描画・アニメーション算数ラボ",
    page_icon="📐",
    layout="wide",
    initial_sidebar_state="expanded"
)

# カスタムCSSの適用
st.markdown("""
<style>
    .reportview-container {
        background: #f8fafc;
    }
    .main-title {
        font-size: 2.2rem;
        font-weight: 800;
        background: linear-gradient(135deg, #1e3a8a 0%, #3b82f6 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 0.5rem;
    }
    .sub-title {
        font-size: 1.0rem;
        color: #64748b;
        margin-bottom: 2rem;
    }
    .metric-card {
        background-color: white;
        border: 1px solid #e2e8f0;
        padding: 1.25rem;
        border-radius: 12px;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);
        margin-bottom: 1rem;
    }
    .metric-title {
        font-size: 0.85rem;
        color: #64748b;
        font-weight: 600;
        text-transform: uppercase;
        margin-bottom: 0.25rem;
    }
    .metric-value {
        font-size: 1.8rem;
        font-weight: 700;
        color: #1e3a8a;
    }
    .error-box {
        background-color: #fef2f2;
        border: 1px solid #fee2e2;
        padding: 1rem;
        border-radius: 8px;
        color: #991b1b;
        margin-bottom: 1rem;
    }
    .success-box {
        background-color: #f0fdf4;
        border: 1px solid #dcfce7;
        padding: 1rem;
        border-radius: 8px;
        color: #166534;
        margin-bottom: 1rem;
    }
</style>
""", unsafe_allow_html=True)

# サンドボックス用ディレクトリの設定
SANDBOX_DIR = _here / "temp_sandbox"
SANDBOX_DIR.mkdir(exist_ok=True)

def cleanup_sandbox():
    """サンドボックス内のファイルをクリアする"""
    for file in SANDBOX_DIR.glob("*"):
        try:
            if file.is_file():
                file.unlink()
            elif file.is_dir():
                shutil.rmtree(file)
        except Exception as e:
            pass

def run_python_code(code_str: str):
    """生成されたPythonコードをサンドボックス内で実行し、結果を返す"""
    cleanup_sandbox()
    
    # 実行ファイルパス
    script_path = SANDBOX_DIR / "run_geometry.py"
    
    # GUIウィンドウを開かないようにAggバックエンドを強制し、コードを保存
    # また、出力画像の保存先をサンドボックス内に指定しやすくするためのラッパーコードを追加
    modified_code = f"""
import matplotlib
matplotlib.use('Agg') # GUIバックエンドを無効化
import os
import matplotlib.pyplot as plt

# コード実行のメイン処理
{code_str}
"""
    
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(modified_code)
        
    try:
        # サンドボックスのディレクトリを作業ディレクトリとしてスクリプトを実行
        result = subprocess.run(
            [sys.executable, str(script_path)],
            capture_output=True,
            text=True,
            cwd=str(SANDBOX_DIR),
            timeout=20  # タイムアウト無限ループ防止
        )
        
        # 生成された画像ファイル（gifまたはpng）を探す
        gif_files = list(SANDBOX_DIR.glob("*.gif"))
        png_files = list(SANDBOX_DIR.glob("*.png"))
        
        image_path = None
        is_animation = False
        
        # アニメーションGIFを優先
        if gif_files:
            image_path = gif_files[0]
            is_animation = True
        elif png_files:
            image_path = png_files[0]
            
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "image_path": image_path,
            "is_animation": is_animation
        }
        
    except subprocess.TimeoutExpired as e:
        return {
            "success": False,
            "stdout": e.stdout or "",
            "stderr": "実行がタイムアウトしました(20秒)。アニメーションのフレーム数が多すぎるか、無限ループが発生している可能性があります。",
            "image_path": None,
            "is_animation": False
        }
    except Exception as e:
        return {
            "success": False,
            "stdout": "",
            "stderr": f"予期せぬエラーが発生しました: {str(e)}",
            "image_path": None,
            "is_animation": False
        }

def extract_code_and_text(ai_response: str):
    """AIのレスポンスからPythonコードブロックと通常のテキスト（解説）を抽出する"""
    code_pattern = r"```python\s*(.*?)\s*```"
    code_match = re.search(code_pattern, ai_response, re.DOTALL)
    
    if code_match:
        code_content = code_match.group(1)
        # コード部分を除いたテキストを解説とする
        explanation = re.sub(code_pattern, "", ai_response, flags=re.DOTALL).strip()
        return code_content, explanation
    else:
        return None, ai_response

def parse_results_from_stdout(stdout_str: str):
    """標準出力から [RESULT_XXX] 形式の計算結果を抽出する"""
    results = {}
    patterns = {
        "area": r"\[RESULT_AREA\]\s*(.+)",
        "perimeter": r"\[RESULT_PERIMETER\]\s*(.+)",
        "volume": r"\[RESULT_VOLUME\]\s*(.+)",
        "surface_area": r"\[RESULT_SURFACE_AREA\]\s*(.+)",
        "other": r"\[RESULT_OTHER\]\s*(.+)"
    }
    
    for key, val in patterns.items():
        match = re.search(val, stdout_str)
        if match:
            results[key] = match.group(1).strip()
            
    return results

# セッション状態の初期化
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
if "generated_code" not in st.session_state:
    st.session_state.generated_code = ""
if "explanation" not in st.session_state:
    st.session_state.explanation = "左側のプリセットを選択するか、チャットに指示を入力してください。"
if "current_image" not in st.session_state:
    st.session_state.current_image = None
if "is_animation" not in st.session_state:
    st.session_state.is_animation = False
if "stdout" not in st.session_state:
    st.session_state.stdout = ""
if "stderr" not in st.session_state:
    st.session_state.stderr = ""
if "parsed_results" not in st.session_state:
    st.session_state.parsed_results = {}

# プリセットデータ
PRESETS = {
    "正三角形の外側を転がる円": {
        "prompt": "1辺の長さが6の正三角形の外側を、半径1の円が滑らずに1周します。円の軌跡（中心の移動経路）と、円が通過した領域を描画してください。また、円の軌跡の長さ、および円が通過した領域の面積を計算して表示してください。",
        "desc": "代表的な「転がり移動」の問題です。コーナーを曲がるときの扇形の軌跡がポイントになります。"
    },
    "正方形の内側を転がる円": {
        "prompt": "1辺の長さが8の正方形の内側を、半径1.5の円が辺に接しながら滑らずに1周します。円が通過した領域を描画し、通過しなかった中央の領域の面積を計算して表示してください。",
        "desc": "内側を転がる場合、コーナーに円が届かないため、角に隙間（正方形から扇形を引いた形）が残ります。"
    },
    "直角三角形の回転体（円錐）": {
        "prompt": "底辺の長さが3、高さが4、斜辺が5の直角三角形があります。この直角三角形の高さ（長さ4の辺）を軸として1回転させてできる立体の形状を3Dで描画・アニメーションしてください。また、その立体の体積と表面積を計算して表示してください。",
        "desc": "回転軸を中心に図形を1回転させてできる3D立体（円錐）の可視化と計算を行います。"
    },
    "おうぎ形の転がり移動": {
        "prompt": "半径6、中心角90度のおうぎ形が、直線に沿って滑ることなく転がります。おうぎ形が再び元の姿勢（平らな面が下）に戻るまでに、中心（おうぎ形の角の頂点）が動いた軌跡を描画してください。また、その軌跡の長さを計算して表示してください。",
        "desc": "おうぎ形が直線の上を転がる難度の高い問題です。直線と転がりの複合的な動きがアニメーションで直感的に理解できます。"
    }
}

# サイドバー
with st.sidebar:
    st.title("⚙️ コントロールパネル")
    
    # モデル選択
    selected_model = st.selectbox(
        "🧠 AIモデル",
        ["gemini-3.5-flash", "gemini-3.1-flash-lite"],
        help="通常は高品質な gemini-3.5-flash を推奨します。動作が重い場合は gemini-3.1-flash-lite をお試しください。"
    )
    
    st.divider()
    
    # プリセット
    st.subheader("📋 定番テンプレート")
    for name, data in PRESETS.items():
        if st.button(name, use_container_width=True, help=data["desc"]):
            st.session_state.preset_trigger = data["prompt"]
            st.rerun()
            
    st.divider()
    
    # 履歴クリア
    if st.button("💬 チャット履歴クリア", use_container_width=True):
        st.session_state.chat_history = []
        st.session_state.generated_code = ""
        st.session_state.explanation = "チャット履歴をクリアしました。指示を入力してください。"
        st.session_state.current_image = None
        st.session_state.parsed_results = {}
        st.session_state.stdout = ""
        st.session_state.stderr = ""
        cleanup_sandbox()
        st.success("履歴をクリアしました")
        st.rerun()

# メインコンテンツ
st.markdown('<div class="main-title">📐 図形描画・アニメーション算数ラボ</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-title">AIに言葉で指示するだけで、Python(Matplotlib)が動的に図形の移動や立体アニメーションを描画し、面積や体積を計算します。</div>', unsafe_allow_html=True)

# 左右のレイアウト
col_visual, col_info = st.columns([3, 2])

# 左側：ビジュアル表示エリア
with col_visual:
    st.subheader("🎬 図形描画・アニメーション")
    
    # 画像またはアニメーションの表示
    if st.session_state.current_image and os.path.exists(st.session_state.current_image):
        if st.session_state.is_animation:
            st.image(st.session_state.current_image, caption="動的アニメーション (GIF)", use_container_width=True)
        else:
            st.image(st.session_state.current_image, caption="静的プロット (PNG)", use_container_width=True)
    else:
        # 初期状態プレビュープレースホルダ
        st.info("右下のチャットに指示を入力するか、サイドバーのテンプレートを選択すると、ここにアニメーションが描画されます。")
        
    # コード編集・再実行の表示
    with st.expander("📝 生成されたPythonコードの確認・直接編集"):
        edited_code = st.text_area(
            "Pythonコード (Matplotlib)",
            value=st.session_state.generated_code,
            height=300,
            key="code_editor"
        )
        if st.button("🛠️ コードを修正して手動実行", use_container_width=True):
            if edited_code.strip():
                st.session_state.generated_code = edited_code
                with st.spinner("コードを実行中..."):
                    res = run_python_code(edited_code)
                    if res["success"]:
                        # 実行成功
                        st.session_state.stdout = res["stdout"]
                        st.session_state.stderr = res["stderr"]
                        if res["image_path"]:
                            # 新しい画像パスを永続的な場所にコピー（再読み込み時に消えないように）
                            save_dest = _here / f"output_temp{'.gif' if res['is_animation'] else '.png'}"
                            shutil.copy(res["image_path"], save_dest)
                            st.session_state.current_image = str(save_dest)
                            st.session_state.is_animation = res["is_animation"]
                        st.session_state.parsed_results = parse_results_from_stdout(res["stdout"])
                        st.toast("実行に成功しました！", icon="✅")
                    else:
                        st.session_state.stdout = res["stdout"]
                        st.session_state.stderr = res["stderr"]
                        st.error("コード実行中にエラーが発生しました。下の『実行時のエラー・出力』タブを確認してください。")
                    st.rerun()

# 右側：計算結果と解説
with col_info:
    st.subheader("💡 計算結果と解説")
    
    # 計算結果カードの表示
    if st.session_state.parsed_results:
        st.markdown('<div class="metric-container" style="display:flex; flex-wrap:wrap; gap:10px;">', unsafe_allow_html=True)
        for key, val in st.session_state.parsed_results.items():
            title_jp = {
                "area": "軌跡・通過領域の面積",
                "perimeter": "軌跡・外周の長さ",
                "volume": "回転体の体積",
                "surface_area": "回転体の表面積",
                "other": "計算結果"
            }.get(key, key)
            
            st.markdown(f"""
            <div class="metric-card" style="flex: 1; min-width: 180px;">
                <div class="metric-title">{title_jp}</div>
                <div class="metric-value">{val}</div>
            </div>
            """, unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)
        
    # 解説の表示
    st.markdown(st.session_state.explanation)
    
    # コンソール出力タブ
    with st.tabs(["💻 コンソール出力", "🚨 エラーログ"])[0]:
        if st.session_state.stdout:
            st.text_area("標準出力 (stdout)", st.session_state.stdout, height=120)
        else:
            st.caption("標準出力はありません。")
            
    with st.container():
        # エラー表示（もしあれば）
        if st.session_state.stderr:
            st.markdown('<div class="error-box"><strong>エラーログ:</strong><br>' + st.session_state.stderr.replace('\n', '<br>') + '</div>', unsafe_allow_html=True)

# チャット履歴と入力インターフェース
st.divider()
st.subheader("💬 AIとチャットしながら調整する")

# チャット履歴の表示
for msg in st.session_state.chat_history:
    with st.chat_message(msg["role"]):
        st.write(msg["content"])

# AIによるコード生成ロジック
def handle_user_input(user_prompt: str):
    """ユーザーの入力を処理し、Gemini APIを呼び出してコード生成・実行を行う"""
    st.session_state.chat_history.append({"role": "user", "content": user_prompt})
    
    # システムプロンプトの設計
    system_prompt = """あなたは中学受験算数および数学のビジュアル教材を作成するスペシャリストです。
ユーザーの要望に応じて、図形の移動（転がり移動、点移動など）や回転体（3D）のシミュレーションとアニメーションを行うPythonコード（Matplotlib）を生成してください。

以下の【絶対ルール】を厳守してください。

1. 【Pythonコードの出力フォーマット】
   - コードは必ず ```python ... ``` ブロックで囲んでください。レスポンス内に含めるコードブロックは1つだけにしてください。
   - コード以外のテキスト部分には、問題の解き方や数学的な解説（面積・長さ・体積の公式と計算手順）を分かりやすく日本語で記述してください。

2. 【Matplotlibコード作成のガイドライン】
   - GUIウィンドウを表示する `plt.show()` は絶対に呼び出さないでください。
   - バックグラウンド実行（Aggバックエンド）に対応するため、スクリプトの先頭で `import matplotlib; matplotlib.use('Agg')` を呼び出している前提で動作するようにしてください。
   - アニメーションは `matplotlib.animation.FuncAnimation` を使用して作成し、最後に必ず `'animation.gif'` というファイル名で保存してください。
     保存時のコード例：`ani.save('animation.gif', writer='pillow', fps=15)`
   - 動的アニメーションが不要な静的プロット（例：3D完成予想図のみ）の場合は、`'plot.png'` というファイル名で保存してください：`plt.savefig('plot.png', dpi=150, bbox_inches='tight')`。ただし、可能な限り動きがわかるアニメーション（GIF）を作成してください。
   - 3Dの回転体の場合は、3Dプロット（`ax = fig.add_subplot(111, projection='3d')`）を使用し、カメラ角度（`ax.view_init`）を徐々に回転させるか、回転角 $\\theta$ を $0$ から $2\\pi$ までスイープして描画するアニメーションにしてください。

3. 【視覚的な美しさと分かりやすさ（Aesthetics）】
   - 背景は白や薄いグレーにし、グリッド（`ax.grid(True, linestyle='--', alpha=0.6)`）を表示して座標が分かりやすいようにしてください。
   - 元の図形（三角形、正方形など）、移動する図形（円など）、そして「軌跡（通った跡）」の3要素が明確に色分けされている必要があります。
     - 例：元の図形＝青（実線）、移動する円＝赤（半透明または実線）、軌跡＝黄色やオレンジ（塗りつぶし、または太い線）
   - 各軸のスケールを均等に設定してください（2Dの場合は `ax.set_aspect('equal')` が必須です。3Dの場合もアスペクト比を適切に設定してください）。
   - 描画範囲（`xlim`, `ylim`）は、アニメーション全体が十分収まるように余裕を持って設定してください。

4. 【計算結果の出力】
   - スクリプトの最後で、計算した重要な数値を標準出力（`print`）に以下のタグ形式で必ず出力してください。
     - 通過領域の面積: `print(f"[RESULT_AREA] {area_value:.2f}")` または分数やπを含む表現
     - 軌跡の長さ・外周: `print(f"[RESULT_PERIMETER] {perimeter_value:.2f}")`
     - 回転体の体積: `print(f"[RESULT_VOLUME] {volume_value:.2f}")`
     - 回転体の表面積: `print(f"[RESULT_SURFACE_AREA] {surface_area_value:.2f}")`
     - その他: `print(f"[RESULT_OTHER] {other_value}")`
   - 数値は可能な限り、円周率 $\\pi$ を含む表現（例：「12π + 36」や「$18\\pi$」）と、小数の近似値（例：「73.68」）の両方を print や解説に含めると学習効果が高いです。

5. 【対話的な修正への対応】
   - チャット履歴がある場合、ユーザーは前回のコードに対する修正（「円の半径を大きくして」「色を青にして」など）を求めています。
   - 前回のコードをベースにし、変更要求があった箇所のみをスマートに書き換え、全体として完全に動作するPythonコードを再出力してください。
"""

    # プロンプト履歴の構築
    messages = [{"role": "user", "parts": [system_prompt]}]
    
    # 過去の会話をメッセージ履歴に追加
    for msg in st.session_state.chat_history[:-1]:
        messages.append({
            "role": "model" if msg["role"] == "assistant" else "user",
            "parts": [msg["content"]]
        })
        
    # 最新のユーザー指示を追加（現在のコードがあれば文脈として渡す）
    current_context = ""
    if st.session_state.generated_code:
        current_context = f"\n\nなお、現在のPythonコードは以下の通りです。このコードをベースに必要な修正を行ってください：\n```python\n{st.session_state.generated_code}\n```"
        
    messages.append({
        "role": "user",
        "parts": [user_prompt + current_context]
    })
    
    try:
        # Gemini API呼び出し
        model = genai.GenerativeModel(model_name=selected_model)
        
        # UI上で待機表示
        with st.spinner("AIがコードを生成・計算しています..."):
            response = model.generate_content(messages)
            ai_text = response.text
            
        # コードとテキストの分離
        code, explanation = extract_code_and_text(ai_text)
        
        if code:
            st.session_state.generated_code = code
            st.session_state.explanation = explanation
            
            # Pythonコードの実行
            with st.spinner("アニメーションをレンダリングしています..."):
                res = run_python_code(code)
                
                st.session_state.stdout = res["stdout"]
                st.session_state.stderr = res["stderr"]
                
                if res["success"]:
                    if res["image_path"]:
                        # 成功した画像を永続的なファイル名にコピーして保存
                        save_dest = _here / f"output_temp{'.gif' if res['is_animation'] else '.png'}"
                        shutil.copy(res["image_path"], save_dest)
                        st.session_state.current_image = str(save_dest)
                        st.session_state.is_animation = res["is_animation"]
                    else:
                        st.session_state.stderr += "\n実行は成功しましたが、画像/アニメーションファイル（*.gif または *.png）が生成されませんでした。コード内に保存処理があるか確認してください。"
                    
                    st.session_state.parsed_results = parse_results_from_stdout(res["stdout"])
                    st.toast("アニメーションの生成に成功しました！", icon="🎨")
                else:
                    st.error("生成されたコードの実行中にエラーが発生しました。コードを自動調整するか、直接編集してください。")
            
            # アシスタントの返答を履歴に追加
            st.session_state.chat_history.append({"role": "assistant", "content": explanation})
        else:
            st.warning("Pythonコードが生成されませんでした。プロンプトをより具体的に書き直してください。")
            st.session_state.chat_history.append({"role": "assistant", "content": ai_text})
            st.session_state.explanation = ai_text
            
    except Exception as e:
        st.error(f"AIとの通信中にエラーが発生しました: {str(e)}")

# プリセットがトリガーされた場合の処理
if "preset_trigger" in st.session_state and st.session_state.preset_trigger:
    prompt = st.session_state.preset_trigger
    # トリガーをクリア
    st.session_state.preset_trigger = None
    handle_user_input(prompt)
    st.rerun()

# チャット入力
if user_input := st.chat_input("どのような図形を描画・動かしますか？ (例: 1辺が4の正方形の周りを半径1の円が転がる)"):
    handle_user_input(user_input)
    st.rerun()
