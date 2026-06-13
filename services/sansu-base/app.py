# -*- coding: utf-8 -*-
import os
import sys
import re
import uuid
import tempfile
import subprocess
import shutil
import json
from pathlib import Path
from flask import Flask, render_template, request, jsonify, redirect
from flask_cors import CORS
from loguru import logger
from typing import Optional
from dotenv import load_dotenv
import pypdfium2 as pdfium
from PIL import Image

# PDFから画像への変換ヘルパー
def convert_pdf_to_images(pdf_path: Path) -> list:
    """PDFの全ページをPIL Imageのリストに変換する"""
    try:
        doc = pdfium.PdfDocument(str(pdf_path))
        images = []
        for page in doc:
            bitmap = page.render(scale=2)  # 解像度向上のためscale=2
            pil_img = bitmap.to_pil()
            images.append(pil_img)
        return images
    except Exception as e:
        logger.error(f"Failed to convert PDF to images: {e}")
        return []

# 図形描画用Pythonコードの実行ヘルパー
def run_diagram_code(code_str: str, session_id: str, suffix: str, elev: Optional[float] = None, azim: Optional[float] = None) -> dict:
    """生成されたMatplotlibコードを実行し、生成された画像を static/generated/ に保存する"""
    static_gen_dir = _here / "static" / "generated"
    static_gen_dir.mkdir(exist_ok=True, parents=True)
    
    output_filename = f"temp_{session_id}_{suffix}.png"
    dest_path = static_gen_dir / output_filename
    
    # すでに古いファイルがあれば削除
    if dest_path.exists():
        dest_path.unlink()
        
    if not code_str or code_str.strip() == "":
        return {"success": True, "image_url": "", "error": ""}

    # Remove plt.show() calls to prevent figure cleanups and Agg backend warnings
    import re
    cleaned_code_str = re.sub(r'(?m)^\s*plt\.show\(\s*\)', '# plt.show() # removed for web preview', code_str)
        
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        script_path = tmp_path / "draw.py"
        
        view_override = ""
        if elev is not None and azim is not None:
            # Extract output filenames from the user code
            saved_files = re.findall(r"plt\.savefig\(\s*['\"]([^'\"]+)['\"]", cleaned_code_str)
            saved_files_repr = repr(saved_files)
            
            view_override = f"""
try:
    overridden = False
    for ax in plt.gcf().get_axes():
        if hasattr(ax, 'view_init'):
            ax.view_init(elev={elev}, azim={azim})
            overridden = True
    if overridden:
        saved_files = {saved_files_repr}
        if not saved_files:
            saved_files = ["{suffix}_diagram.png", "problem_diagram.png", "explanation_diagram.png"]
        for f in saved_files:
            plt.savefig(f, dpi=150, bbox_inches="tight")
except Exception as e:
    print("Failed to override 3D view and re-save:", e)
"""

        # Aggバックエンドを強制し、GUIウインドウを開かないようにする
        modified_code = f"""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

{cleaned_code_str}

{view_override}
"""
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(modified_code)
            
        try:
            result = subprocess.run(
                [sys.executable, str(script_path)],
                capture_output=True,
                text=True,
                cwd=str(tmp_path),
                timeout=20
            )
            
            logger.info(f"Matplotlib execution stdout: {result.stdout}")
            if result.stderr:
                logger.warning(f"Matplotlib execution stderr: {result.stderr}")
                
            # 作成されたはずの画像ファイルを探す
            expected_names = [f"{suffix}_diagram.png", "problem_diagram.png", "explanation_diagram.png"]
            found_image = None
            for name in expected_names:
                p = tmp_path / name
                if p.exists():
                    found_image = p
                    break
                    
            # 見つからない場合は任意のPNGファイルを探す
            if not found_image:
                png_files = list(tmp_path.glob("*.png"))
                if png_files:
                    found_image = png_files[0]
                    
            if found_image:
                shutil.copy(found_image, dest_path)
                logger.info(f"Successfully generated diagram in static: {output_filename}")
                return {
                    "success": True,
                    "image_url": f"/static/generated/{output_filename}",
                    "error": result.stderr
                }
            else:
                return {
                    "success": False,
                    "image_url": "",
                    "error": f"画像ファイルが生成されませんでした。\n標準エラー出力:\n{result.stdout}\n標準エラー:\n{result.stderr}"
                }
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "image_url": "",
                "error": "図形の描画実行がタイムアウト（20秒）しました。"
            }
        except Exception as e:
            return {
                "success": False,
                "image_url": "",
                "error": f"描画コード実行エラー: {str(e)}"
            }


# PYTHONPATHの設定と環境変数のロード
_here = Path(__file__).resolve().parent
_repo = _here.parent.parent
if str(_repo) not in sys.path:
    sys.path.insert(0, str(_repo))
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

load_dotenv(_repo / ".env")
load_dotenv(_here / ".env")

# GCP Secret Manager からシークレットを取得するヘルパー（本番環境用）
def load_secrets_from_secret_manager():
    # Cloud Run環境（K_SERVICE環境変数が存在）であるかを判定
    if not os.environ.get("K_SERVICE"):
        # ローカル環境では環境変数のみを使用し、フォールバックや例外キャッチは行わない
        if not os.environ.get("GOOGLE_AI_API_KEY"):
            raise ValueError("GOOGLE_AI_API_KEY is not set in the environment.")
        return

    # 本番環境（Cloud Run）では GCP Secret Manager から直接ロードする
    # 失敗した場合はフォールバック（警告ログのみ等）を行わず、例外を発生させて起動失敗（Fail-fast）とする
    gcp_project = os.environ.get("GCP_PROJECT_ID") or os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not gcp_project:
        raise ValueError("GCP_PROJECT_ID or GOOGLE_CLOUD_PROJECT must be set in Cloud Run environment.")

    from google.cloud import secretmanager
    client = secretmanager.SecretManagerServiceClient()
    
    # 取得対象の環境変数とSecret Manager上のシークレットIDのマッピング
    secrets_to_fetch = {
        "GOOGLE_AI_API_KEY": "GOOGLE_AI_API_KEY",
        "SUPABASE_URL": "SUPABASE_URL",
        "SUPABASE_SERVICE_ROLE_KEY": "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_KEY": "SUPABASE_KEY"
    }
    
    for env_var, secret_id in secrets_to_fetch.items():
        name = f"projects/{gcp_project}/secrets/{secret_id}/versions/latest"
        response = client.access_secret_version(request={"name": name})
        val = response.payload.data.decode("utf-8").strip()
        if not val:
            raise ValueError(f"Secret {secret_id} is empty in GCP Secret Manager.")
        os.environ[env_var] = val
        logger.info(f"Successfully retrieved {env_var} from Secret Manager")

load_secrets_from_secret_manager()


# 共通データベースクライアントとGoogle Driveハンドラ、Gemini APIのインポート
from dms.common.database.client import DatabaseClient
from google_drive_handler import GoogleDriveHandler
import google.generativeai as genai

# Flaskアプリ初期化
app = Flask(__name__, static_folder="static", static_url_path="/static")
CORS(app)
app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY") or os.urandom(32).hex()

# 各種モジュールの遅延初期化
_db_client = None
_drive_handler = None

def get_db():
    global _db_client
    if _db_client is None:
        _db_client = DatabaseClient(use_service_role=True)
    return _db_client

def get_drive():
    global _drive_handler
    if _drive_handler is None:
        _drive_handler = GoogleDriveHandler()
    return _drive_handler

# Gemini API の初期化
genai.configure(api_key=os.getenv("GOOGLE_AI_API_KEY"))


# 単元名からID用プレフィックスへのマッピング
UNIT_PREFIX_MAP = {
    "平面図形": "GEO",
    "立体図形": "SOL",
    "文章題": "WRD",
    "数の性質": "NUM",
    "場合の数": "COM",
    "規則性": "REG"
}

def get_next_display_id(unit: str) -> str:
    """指定された単元の次の問題IDを自動発番する"""
    db = get_db()
    prefix = UNIT_PREFIX_MAP.get(unit, "PRB")
    
    try:
        res = db.client.table("math_problems") \
            .select("display_id") \
            .like("display_id", f"{prefix}-%") \
            .order("display_id", desc=True) \
            .limit(1) \
            .execute()
            
        rows = res.data or []
        if rows:
            latest_id = rows[0]["display_id"]
            match = re.search(r'-(\d+)$', latest_id)
            if match:
                next_num = int(match.group(1)) + 1
                return f"{prefix}-{next_num:03d}"
        
        return f"{prefix}-001"
    except Exception as e:
        logger.error(f"Failed to generate next ID: {e}")
        return f"{prefix}-001"

# Markdownパーサー関数
def parse_markdown_problem(md_text: str) -> dict:
    data = {
        "display_id": "",
        "source_book": "",
        "chapter": "",
        "unit": "",
        "strategy_summary": "",
        "problem_markdown": "",
        "explanation_markdown": ""
    }
    
    # 1. display_id のみタイトル行の括弧から抽出 (教材名や章・単元は自動で投入しない)
    title_match = re.search(r'^#\s*\[(.*?)\]', md_text, re.MULTILINE)
    if title_match:
        data["display_id"] = title_match.group(1).strip()
        
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
        if explanation_text.endswith("---"):
            explanation_text = explanation_text[:-3].strip()

    # 3. 「解法の核心」の抽出（問題文または解説文の中から探す）
    # パターン1: 同じ行にある場合 (例: ## 【解法の核心】 3:4:5の相似連鎖)
    core_match = re.search(
        r'^(?:[#\s-]*)?【?\s*(?:解法コア|コア戦略|解法の核心・企み|解法の核心)\s*】?\s*[:：\s]+\s*(.+)$', 
        md_text, 
        re.MULTILINE
    )
    
    # パターン2: 次の行にある場合
    if not core_match:
        core_match = re.search(
            r'^(?:[#\s-]*)?【?\s*(?:解法コア|コア戦略|解法の核心・企み|解法の核心)\s*】?\s*[:：\s]*\n\s*(.+)$', 
            md_text, 
            re.MULTILINE
        )
        
    if core_match:
        data["strategy_summary"] = core_match.group(1).strip()
        
        # 抽出した「解法の核心」を問題文・解説文から削除する
        # 同じ行のパターンを削除
        pattern_same = r'^(?:[#\s-]*)?【?\s*(?:解法コア|コア戦略|解法の核心・企み|解法の核心)\s*】?\s*[:：\s]+\s*.+$'
        problem_text = re.sub(pattern_same, "", problem_text, flags=re.MULTILINE)
        explanation_text = re.sub(pattern_same, "", explanation_text, flags=re.MULTILINE)
        
        # 改行をまたぐパターンを削除
        pattern_next = r'^(?:[#\s-]*)?【?\s*(?:解法コア|コア戦略|解法の核心・企み|解法の核心)\s*】?\s*[:：\s]*\n\s*.+$'
        problem_text = re.sub(pattern_next, "", problem_text, flags=re.MULTILINE)
        explanation_text = re.sub(pattern_next, "", explanation_text, flags=re.MULTILINE)

    data["problem_markdown"] = problem_text.strip()
    data["explanation_markdown"] = explanation_text.strip()
    
    return data

# === HTML Pages ====================================================
@app.route("/")
def index():
    return render_template("editor.html")

# === API Endpoints ==================================================

@app.route("/api/problems/next-id", methods=["GET"])
def api_next_id():
    """指定された単元の次の自動発番IDを取得"""
    unit = request.args.get("unit", "").strip()
    if not unit:
        return jsonify({"error": "Unit is required"}), 400
    next_id = get_next_display_id(unit)
    return jsonify({"next_id": next_id})

@app.route("/api/problems", methods=["GET"])
def list_problems():
    """問題の一覧取得(単元・キーワード検索・フィルタ)"""
    db = get_db()
    
    unit = request.args.get("unit", "").strip()
    search_query = request.args.get("q", "").strip()
    
    q = db.client.table("math_problems").select("*")
    
    if unit:
        q = q.eq("unit", unit)
        
    if search_query:
        q = q.or_(
            f"display_id.ilike.%{search_query}%,"
            f"source_book.ilike.%{search_query}%,"
            f"chapter.ilike.%{search_query}%,"
            f"problem_markdown.ilike.%{search_query}%,"
            f"strategy_summary.ilike.%{search_query}%"
        )
        
    q = q.order("created_at", desc=True)
    rows = q.execute().data or []
    return jsonify(rows)

@app.route("/api/problems/<id>", methods=["GET"])
def get_problem(id):
    """特定の問題を1つ取得 (id または display_id で検索)"""
    db = get_db()
    res = db.client.table("math_problems").select("*").eq("id", id).execute()
    if res.data:
        return jsonify(res.data[0])
    
    res = db.client.table("math_problems").select("*").eq("display_id", id).execute()
    if res.data:
        return jsonify(res.data[0])
        
    return jsonify({"error": "Problem not found"}), 404

@app.route("/api/problems", methods=["POST"])
def save_problem():
    """新規問題の登録（SupabaseとGoogle Driveに二重書き込み）"""
    data = request.json
    db = get_db()
    drive = get_drive()
    
    required = ["source_book", "problem_markdown", "explanation_markdown"]
    for field in required:
        if not data.get(field):
            return jsonify({"error": f"Missing required field: {field}"}), 400

    unit = data.get("unit", "").strip()
    if not unit:
        unit = "未設定"

    display_id = data.get("display_id")
    if not display_id or display_id.strip() == "":
        display_id = str(uuid.uuid4())

    # 1. Supabaseへインサート
    problem_record = {
        "display_id": display_id,
        "source_book": data["source_book"],
        "chapter": data.get("chapter"),
        "unit": unit,
        "problem_markdown": data["problem_markdown"],
        "explanation_markdown": data["explanation_markdown"],
        "strategy_summary": data.get("strategy_summary"),
        "grading_status": data.get("grading_status") or {},
        "owner_id": os.getenv("DEFAULT_OWNER_ID", "d1b18b1c-a4dc-4b2e-97af-5153a85e685c")
    }
    
    try:
        db.client.table("math_problems").insert(problem_record).execute()
        logger.info(f"Successfully inserted problem '{display_id}' into Supabase")
    except Exception as e:
        logger.error(f"Failed to insert into Supabase: {e}")
        return jsonify({"error": f"Supabase insert failed: {str(e)}"}), 500

    # 2. Google Driveへアペンド（追記）
    chapter_str = f" {data['chapter']}" if data.get("chapter") else ""
    
    append_md = f"""# [{display_id}] {data['source_book']}{chapter_str}
- 単元: {unit}
- ページ数: {data.get('strategy_summary', 'なし')}
 
 ## 問題
 {data['problem_markdown']}
 
 ## 解説
 {data['explanation_markdown']}"""

    drive_success = drive.append_problem_markdown(data["unit"], append_md)
    if not drive_success:
        logger.warning(f"Failed to append problem '{display_id}' to Google Drive, but Supabase record was saved.")

    return jsonify({
        "status": "success", 
        "message": "Problem saved successfully",
        "display_id": display_id,
        "drive_synced": drive_success
    }), 201

@app.route("/api/problems/<id>", methods=["PUT"])
def update_problem(id):
    """問題の更新"""
    data = request.json
    db = get_db()
    
    existing = db.client.table("math_problems").select("id").eq("id", id).execute().data
    if not existing:
        return jsonify({"error": "Problem not found"}), 404

    update_record = {}
    allowed_fields = ["display_id", "source_book", "chapter", "unit", "problem_markdown", "explanation_markdown", "strategy_summary", "grading_status"]
    for field in allowed_fields:
        if field in data:
            if field == "unit":
                val = (data[field] or "").strip()
                update_record[field] = val if val else "未設定"
            else:
                update_record[field] = data[field]

    try:
        db.client.table("math_problems").update(update_record).eq("id", id).execute()
        return jsonify({"status": "success", "message": "Problem updated successfully"})
    except Exception as e:
        return jsonify({"error": f"Supabase update failed: {str(e)}"}), 500

@app.route("/api/problems/<id>", methods=["DELETE"])
def delete_problem(id):
    """問題の削除"""
    db = get_db()
    try:
        db.client.table("math_problems").delete().eq("id", id).execute()
        return jsonify({"status": "success", "message": "Problem deleted successfully"})
    except Exception as e:
        return jsonify({"error": f"Supabase delete failed: {str(e)}"}), 500

@app.route("/api/problems/parse", methods=["POST"])
def parse_markdown():
    """送信されたMarkdownテキスト、またはアップロードされたファイルをパースして構造化データにして返す"""
    md_text = ""
    
    if 'file' in request.files:
        file = request.files['file']
        if file.filename != '':
            md_text = file.read().decode('utf-8')
            
    elif request.json and 'markdown' in request.json:
        md_text = request.json['markdown']
        
    if not md_text.strip():
        return jsonify({"error": "No content provided"}), 400
        
    try:
        parsed_data = parse_markdown_problem(md_text)
        return jsonify(parsed_data)
    except Exception as e:
        logger.error(f"Failed to parse markdown: {e}")
        return jsonify({"error": f"Parsing failed: {str(e)}"}), 500

@app.route("/api/problems/<id>/generate-variant", methods=["POST"])
def generate_variant(id):
    """指定された問題の類題をGemini API (Code Execution 有効) で生成。
    リクエストボディの 'model' で使用モデルを選択可能:
      - "gemini-3.1-flash-lite" (デフォルト、高速・低コスト)
      - "gemini-3.5-flash" (高品質)
    """
    db = get_db()
    
    # モデル選択
    data = request.get_json(silent=True) or {}
    ALLOWED_MODELS = {
        "gemini-3.1-flash-lite": "Gemini 3.1 Flash-Lite",
        "gemini-3.5-flash": "Gemini 3.5 Flash",
    }
    model_name = data.get("model", "gemini-3.1-flash-lite")
    if model_name not in ALLOWED_MODELS:
        model_name = "gemini-3.1-flash-lite"
    
    res = db.client.table("math_problems").select("*").eq("id", id).execute()
    problem = res.data[0] if res.data else None
    if not problem:
        return jsonify({"error": "Problem not found"}), 404
    
    # grading_status から正解率を算出
    grading_info = ""
    gs = problem.get("grading_status") or {}
    if gs:
        total = len(gs)
        correct = sum(1 for v in gs.values() if v == "correct")
        incorrect = sum(1 for v in gs.values() if v == "incorrect")
        if total > 0:
            rate = round(correct / total * 100)
            grading_info = f"\n正解率：{correct}/{total} ({rate}%)  不正解：{incorrect}/{total}"
            if rate < 50:
                grading_info += "\n※ 正解率が低いため、同じ解法パターンを繰り返し練習できる類題が望ましい。"
        
    prompt = f"""あなたはプロの中学受験算数講師、および厳密な数学チェックプログラムです。

以下の【元問題】の解法ロジック（コア戦略）を完全にトレースした【類題（1問）】を作成してください。

【元問題】
教材名：{problem['source_book']}{f" {problem['chapter']}" if problem.get('chapter') else ""}
単元：{problem['unit']}
コア戦略：{problem.get('strategy_summary') or 'なし'}{grading_info}

問題文：
{problem['problem_markdown']}

解説：
{problem['explanation_markdown']}

【類題作成の絶対条件】
1. 【解法ロジックの完全トレース】
   元問題で使われている「図形の性質（例：3:4:5の相似連鎖、切断公式）」は100%維持してください。
2. 【数値の完全な変更と整合性】
   すべての寸法（長さ、角度、比率）を元問題から変更してください。
   ただし、図形として成立しない数値（例：折り返した辺が元の長方形を飛び出す、途中の長さがマイナスになる等）は絶対に禁止します。
3. 【計算の簡潔さ】
   小学生が手計算で解くため、最終的な答えおよび途中の計算結果は、綺麗な「整数」または「単純な分数」になるように数値を調整してください。
4. 【分量】
   問題文はA4用紙1ページ（約800文字以内）に収まる分量にしてください。

【実行手順】
必ず最初に出力する前に、バックグラウンドのPythonコード実行環境を使って、あなたが設定した新しい数値で図形が数学的に成立するか、および答えが綺麗になるかを実際にシミュレーション（連立方程式の計算等）して検証してください。検証が成功するまで数値を再調整してください。

【出力フォーマット】
検証完了後、以下のフォーマット（Markdown）のみを出力してください。他の前置きや挨拶、検証用コードは出力に含めないでください。
### 類題：問題
...
### 類題：解説
..."""

    try:
        model = genai.GenerativeModel(
            model_name=model_name,
            tools="code_execution"
        )
        
        logger.info(f"Generating variant for problem '{problem['display_id']}' using {ALLOWED_MODELS[model_name]} with Code Execution...")
        response = model.generate_content(
            prompt,
            generation_config={
                "temperature": 0.2,
            }
        )
        
        # トークン使用量の取得
        token_usage = {"input_tokens": 0, "output_tokens": 0}
        try:
            usage = response.usage_metadata
            token_usage["input_tokens"] = getattr(usage, "prompt_token_count", 0) or 0
            token_usage["output_tokens"] = getattr(usage, "candidates_token_count", 0) or 0
        except Exception:
            logger.warning("Could not extract token usage metadata")
        
        return jsonify({
            "status": "success",
            "variant": response.text,
            "token_usage": token_usage,
            "model_used": ALLOWED_MODELS[model_name]
        })
    except Exception as e:
        logger.error(f"Failed to generate variant: {e}")
        return jsonify({"error": f"Generation failed: {str(e)}"}), 500

@app.route("/api/problems/generate", methods=["POST"])
def api_generate_problem():
    """データベースにある同単元の問題や正解率（苦手問題）などを考慮し、
    Gemini API (Code Execution 有効) で新しい中学受験算数問題と解説を生成する。
    """
    data = request.get_json(silent=True) or {}
    model_name = data.get("model", "gemini-3.1-flash-lite")
    source_book = data.get("source_book", "").strip()
    chapter = data.get("chapter", "").strip()
    unit = data.get("unit", "").strip()
    reference_id = data.get("reference_id")
    
    ALLOWED_MODELS = {
        "gemini-3.1-flash-lite": "Gemini 3.1 Flash-Lite",
        "gemini-3.5-flash": "Gemini 3.5 Flash",
    }
    if model_name not in ALLOWED_MODELS:
        model_name = "gemini-3.1-flash-lite"
        
    db = get_db()
    
    # 1. リファレンス問題の取得（ID指定がある場合）
    primary_ref = None
    if reference_id:
        try:
            res = db.client.table("math_problems").select("*").eq("id", reference_id).execute()
            primary_ref = res.data[0] if res.data else None
            if primary_ref:
                if not source_book: source_book = primary_ref.get("source_book", "")
                if not chapter: chapter = primary_ref.get("chapter", "")
                if not unit: unit = primary_ref.get("unit", "")
        except Exception as e:
            logger.warning(f"Could not fetch primary reference problem: {e}")
            
    if not unit:
        return jsonify({"error": "Unit (単元) is required for generation"}), 400

    # 2. データベースから同単元・同教材の問題を収集し、苦手（正解率が低い・不正解がある）問題を特定
    weak_problems = []
    normal_problems = []
    
    try:
        q = db.client.table("math_problems").select("*").eq("unit", unit)
        if source_book:
            q = q.eq("source_book", source_book)
        all_problems = q.execute().data or []
        
        for prob in all_problems:
            if primary_ref and prob["id"] == primary_ref["id"]:
                continue
                
            gs = prob.get("grading_status") or {}
            total = len(gs)
            correct = sum(1 for v in gs.values() if v == "correct")
            incorrect = sum(1 for v in gs.values() if v == "incorrect")
            
            rate = 100
            if total > 0:
                rate = (correct / total) * 100
                
            prob_info = {
                "display_id": prob.get("display_id", "ID不明"),
                "source_book": prob.get("source_book", ""),
                "chapter": prob.get("chapter", ""),
                "unit": prob.get("unit", ""),
                "strategy": prob.get("strategy_summary") or "なし",
                "problem": prob.get("problem_markdown", ""),
                "explanation": prob.get("explanation_markdown", ""),
                "accuracy": f"{correct}/{total} ({round(rate)}%)" if total > 0 else "未挑戦",
                "incorrect_count": incorrect
            }
            
            if incorrect > 0 or (total > 0 and rate < 70):
                weak_problems.append(prob_info)
            else:
                normal_problems.append(prob_info)
    except Exception as e:
        logger.warning(f"Error querying database for reference problems: {e}")

    # 3. プロンプト用コンテキスト（参考情報）の構築
    context = ""
    if primary_ref:
        ref_book = primary_ref.get('source_book', '')
        ref_chapter = primary_ref.get('chapter', '')
        ref_chapter_str = f" {ref_chapter}" if ref_chapter else ""
        context += f"\n### 【ベースとする既存問題（この問題の解法ロジックを完全トレースした類題を作成してください）】\n"
        context += f"教材名：{ref_book}{ref_chapter_str}\n"
        context += f"単元：{primary_ref.get('unit', '')}\n"
        context += f"コア戦略：{primary_ref.get('strategy_summary', 'なし')}\n"
        context += f"問題文：\n{primary_ref.get('problem_markdown', '')}\n"
        context += f"解説：\n{primary_ref.get('explanation_markdown', '')}\n\n"
        
    if weak_problems:
        context += "### 【生徒が間違えた苦手問題（同様の解法や引っかかりやすい考え方を練習・強化できる問題にしてください）】\n"
        for p in weak_problems[:3]: # プロンプトサイズを考慮し最大3件
            p_chapter = p.get('chapter', '')
            p_chapter_str = f" {p_chapter}" if p_chapter else ""
            context += f"- 問題: {p['source_book']}{p_chapter_str} ({p['unit']} / 生徒の正解率: {p['accuracy']})\n"
            context += f"  核心戦略: {p['strategy']}\n"
            context += f"  問題文:\n{p['problem']}\n"
            context += f"  解説:\n{p['explanation']}\n\n"
            
    if normal_problems and len(weak_problems) < 2:
        context += "### 【同単元の参考問題】\n"
        for p in normal_problems[:2]:
            p_chapter = p.get('chapter', '')
            p_chapter_str = f" {p_chapter}" if p_chapter else ""
            context += f"- 問題: {p['source_book']}{p_chapter_str} ({p['unit']})\n"
            context += f"  核心戦略: {p['strategy']}\n"
            context += f"  問題文:\n{p['problem']}\n"
            context += f"  解説:\n{p['explanation']}\n\n"

    prompt = f"""あなたはプロの中学受験算数講師、および厳密な数学チェックプログラムです。

指定された条件（教材名：「{source_book} {chapter}」、単元：「{unit}」）およびデータベース内の問題履歴に基づいて、新しい【練習問題（1問）】とその【解答・解説】を作成してください。

【データベースから抽出された学習コンテキスト】
{context if context else f'(同単元の既存データがデータベースにありません。単元「{unit}」にふさわしい標準的な新規問題を作成してください。)'}

【問題作成の絶対条件】
1. 【解法・意図の踏襲と苦手克服】
   - ベース問題が提示されている場合：その解法ロジック（例：3:4:5の相似連鎖、特定の比の連鎖）を100%踏襲し、数値や設定（シナリオ）を完全に変更した類題を作成してください。
   - 苦手問題が提示されている場合：生徒が間違えやすい解法アプローチ（例：旅人算の出会いと追いつきの組み合わせ、面積比の連鎖）を取り入れ、苦手を克服できる練習問題にしてください。
2. 【数値の完全な整合性】
   すべての寸法、比率、割合が数学的に矛盾なく成立するように設計してください。途中の長さがマイナスになったり、図形として成立しなかったりする数値設計は厳禁です。
3. 【計算の簡潔さ】
   小学生が手計算で解くため、答えおよび途中の計算結果は、綺麗な「整数」または「単純な分数」になるように数値を調整してください。
4. 【分量】
   問題文はA4用紙1ページ（約800文字以内）に収まる分量にしてください。

【実行手順】
必ず最初に出力する前に、バックグラウンドのPythonコード実行環境を使って、設定した新しい数値で問題が数学的に成立するか、および答えが綺麗になるかを実際にシミュレーションして検証してください。検証が成功するまで数値を再調整してください。

【出力フォーマット】
検証完了後、以下のフォーマット（Markdown）のみを出力してください。他の前置きや挨拶、検証用コードは出力に含めないでください。
### 類題：問題
...
### 類題：解説
..."""

    try:
        model = genai.GenerativeModel(
            model_name=model_name,
            tools="code_execution"
        )
        
        logger.info(f"Generating custom problem for Unit: {unit}, Source: {source_book} {chapter} using {ALLOWED_MODELS[model_name]}...")
        response = model.generate_content(
            prompt,
            generation_config={
                "temperature": 0.2,
            }
        )
        
        # トークン使用量の取得
        token_usage = {"input_tokens": 0, "output_tokens": 0}
        try:
            usage = response.usage_metadata
            token_usage["input_tokens"] = getattr(usage, "prompt_token_count", 0) or 0
            token_usage["output_tokens"] = getattr(usage, "candidates_token_count", 0) or 0
        except Exception:
            logger.warning("Could not extract token usage metadata")
        
        return jsonify({
            "status": "success",
            "variant": response.text,
            "token_usage": token_usage,
            "model_used": ALLOWED_MODELS[model_name]
        })
    except Exception as e:
        logger.error(f"Failed to generate problem: {e}")
        return jsonify({"error": f"Generation failed: {str(e)}"}), 500

# === History Helper APIs =============================================

@app.route("/api/history/source-books", methods=["GET"])
def get_source_books_history():
    """登録済みの教材名（source_book）のユニークなリストを取得"""
    db = get_db()
    try:
        res = db.client.table("math_problems") \
            .select("source_book") \
            .not_.is_("source_book", "null") \
            .not_.eq("source_book", "") \
            .execute()
        rows = res.data or []
        # 重複を排除してソート
        books = sorted(list(set(row["source_book"] for row in rows if row.get("source_book"))))
        return jsonify(books)
    except Exception as e:
        logger.error(f"Failed to fetch source books history: {e}")
        return jsonify([])

@app.route("/api/history/chapters", methods=["GET"])
def get_chapters_history():
    """指定された教材名（source_book）に紐づく登録済みの章・回（chapter）のユニークなリストを取得"""
    source_book = request.args.get("source_book", "").strip()
    if not source_book:
        return jsonify([])
        
    db = get_db()
    try:
        res = db.client.table("math_problems") \
            .select("chapter") \
            .eq("source_book", source_book) \
            .not_.is_("chapter", "null") \
            .not_.eq("chapter", "") \
            .execute()
        rows = res.data or []
        # 重複を排除してソート
        chapters = sorted(list(set(row["chapter"] for row in rows if row.get("chapter"))))
        return jsonify(chapters)
    except Exception as e:
        logger.error(f"Failed to fetch chapters history for book '{source_book}': {e}")
        return jsonify([])

@app.route("/api/history/unit", methods=["GET"])
def get_unit_history():
    """指定された教材名（source_book）と章・回（chapter）に紐づく登録済みの単元（unit）を取得"""
    source_book = request.args.get("source_book", "").strip()
    chapter = request.args.get("chapter", "").strip()
    if not source_book or not chapter:
        return jsonify({"unit": ""})
        
    db = get_db()
    try:
        res = db.client.table("math_problems") \
            .select("unit") \
            .eq("source_book", source_book) \
            .eq("chapter", chapter) \
            .not_.is_("unit", "null") \
            .not_.eq("unit", "") \
            .limit(1) \
            .execute()
        rows = res.data or []
        if rows:
            return jsonify({"unit": rows[0]["unit"]})
        return jsonify({"unit": ""})
    except Exception as e:
        logger.error(f"Failed to fetch unit history for book '{source_book}' and chapter '{chapter}': {e}")
        return jsonify({"unit": ""})

# === OCR Reader / Import Routes ======================================

@app.route("/api/drive/files", methods=["GET"])
def drive_list_files():
    drive = get_drive()
    if not drive or not drive.drive:
        return jsonify({"error": "Google Drive connection not available"}), 500
        
    try:
        folder_id = drive._get_or_create_root_folder()
        query = f"'{folder_id}' in parents and (mimeType starts with 'image/' or mimeType = 'application/pdf') and trashed = false"
        
        results = drive.drive.service.files().list(
            q=query,
            spaces='drive',
            fields='files(id, name, mimeType, size, modifiedTime)',
            orderBy='modifiedTime desc',
            pageSize=50,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
            corpora='allDrives'
        ).execute()
        
        files = results.get('files', [])
        return jsonify(files)
    except Exception as e:
        logger.error(f"Failed to list Drive files: {e}")
        try:
            query = "(mimeType starts with 'image/' or mimeType = 'application/pdf') and trashed = false"
            results = drive.drive.service.files().list(
                q=query,
                spaces='drive',
                fields='files(id, name, mimeType, size, modifiedTime)',
                orderBy='modifiedTime desc',
                pageSize=50,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
                corpora='allDrives'
            ).execute()
            files = results.get('files', [])
            return jsonify(files)
        except Exception as e2:
            logger.error(f"Failed fallback Drive list: {e2}")
            return jsonify([])

def clean_ocr_text(text: str) -> str:
    if not text:
        return ""
    import re
    # Remove "document analysis result(s)" (case-insensitive, optionally followed by spaces, colons, or newlines)
    text = re.sub(r'(?i)document\s+analysis\s+results?[:\s\-\*]*', '', text)
    # Remove "JSONをコピー" (optionally wrapped in brackets/buttons)
    text = re.sub(r'\[?JSONをコピー(?:する)?\]?', '', text)
    # Remove "Copy JSON" (case-insensitive, optionally wrapped in brackets/buttons)
    text = re.sub(r'(?i)\[?copy\s+json\]?', '', text)
    return text.strip()

@app.route("/editor")
def editor_page():
    """ブロック登録エディタの画面を表示（ルートにリダイレクト）"""
    return redirect("/")

@app.route("/api/diagram/run", methods=["POST"])
def api_run_diagram():
    """ブロック用のPython/Matplotlibコードを実行し、一時ファイルを生成して返す"""
    data = request.json or {}
    code_str = data.get("code", "")
    session_id = data.get("session_id")
    suffix = data.get("suffix", "diagram")
    
    elev = data.get("elev")
    azim = data.get("azim")
    
    # 浮動小数点数に型変換
    if elev is not None:
        try: elev = float(elev)
        except ValueError: elev = None
    if azim is not None:
        try: azim = float(azim)
        except ValueError: azim = None
        
    if not session_id:
        session_id = str(uuid.uuid4())
        
    result = run_diagram_code(code_str, session_id, suffix, elev=elev, azim=azim)
    return jsonify(result)

@app.route("/api/problems/save-blocks", methods=["POST"])
def api_save_blocks():
    """ブロックから構築された問題データをコンパイルし、SupabaseとGoogle Driveに保存する"""
    data = request.json or {}
    session_id = data.get("session_id")
    source_book = data.get("source_book", "").strip()
    chapter = data.get("chapter", "").strip()
    unit = data.get("unit", "").strip()
    problem_markdown = data.get("problem_markdown", "").strip()
    explanation_markdown = data.get("explanation_markdown", "").strip()
    strategy_summary = data.get("strategy_summary", "").strip()
    problem_number = data.get("problem_number", "").strip()
    grading_status = data.get("grading_status") or {}
    
    if not source_book or (not problem_markdown and not explanation_markdown):
        return jsonify({"error": "教材名、および本文（問題または解説）は必須です。"}), 400
        
    if not unit or unit.strip() == "":
        unit = "未設定"
        
    display_id = data.get("display_id", "").strip()
    if not display_id or display_id == "auto":
        display_id = str(uuid.uuid4())
        
    static_gen_dir = _here / "static" / "generated"
    static_gen_dir.mkdir(exist_ok=True, parents=True)
    
    # セッションIDに紐づく一時画像を正式名称（ID＋ブロック）にリネームし、MarkdownのURLを置換
    # パターン: /static/generated/temp_{session_id}_{suffix}_{block_id}.png
    pattern = rf"/static/generated/temp_{session_id}_(problem|explanation)_([a-zA-Z0-9_-]+)\.png"
    
    def rename_and_replace(text, display_id):
        if not text:
            return ""
        # 正規表現パターンと一致する箇所を抽出
        matches = re.findall(pattern, text)
        for suffix, block_id in matches:
            temp_filename = f"temp_{session_id}_{suffix}_{block_id}.png"
            real_filename = f"{display_id}_{suffix}_{block_id}.png"
            temp_path = static_gen_dir / temp_filename
            real_path = static_gen_dir / real_filename
            
            temp_url = f"/static/generated/{temp_filename}"
            real_url = f"/static/generated/{real_filename}"
            
            if temp_path.exists():
                shutil.copy(temp_path, real_path)
                text = text.replace(temp_url, real_url)
                logger.info(f"Renamed diagram {temp_filename} to {real_filename}")
                
                # Google Driveへアップロード
                drive = get_drive()
                if drive and drive.drive:
                    try:
                        drive.upload_diagram(unit, str(real_path), real_filename)
                    except Exception as e:
                        logger.error(f"Failed to upload diagram {real_filename} to Drive: {e}")
        return text
        
    problem_markdown = rename_and_replace(problem_markdown, display_id)
    explanation_markdown = rename_and_replace(explanation_markdown, display_id)
    
    # Supabaseへ登録 (新規挿入または既存更新)
    db = get_db()
    problem_record = {
        "display_id": display_id,
        "source_book": source_book,
        "chapter": chapter or None,
        "unit": unit,
        "problem_markdown": problem_markdown,
        "explanation_markdown": explanation_markdown,
        "strategy_summary": strategy_summary,
        "problem_number": problem_number or None,
        "grading_status": grading_status,
        "owner_id": os.getenv("DEFAULT_OWNER_ID", "d1b18b1c-a4dc-4b2e-97af-5153a85e685c")
    }
    
    try:
        # すでに同じ display_id の問題が存在する場合は更新、なければ新規挿入
        existing = db.client.table("math_problems").select("id").eq("display_id", display_id).execute().data
        if existing:
            db.client.table("math_problems").update(problem_record).eq("display_id", display_id).execute()
            logger.info(f"Successfully updated problem '{display_id}' in Supabase")
        else:
            db.client.table("math_problems").insert(problem_record).execute()
            logger.info(f"Successfully inserted problem '{display_id}' into Supabase")
    except Exception as e:
        logger.error(f"Failed to save to Supabase: {e}")
        return jsonify({"error": f"Supabase保存エラー: {str(e)}"}), 500
        
    # Google DriveのMarkdownファイルへ追記
    drive_md_synced = False
    drive = get_drive()
    if drive and drive.drive:
        chapter_str = f" {chapter}" if chapter else ""
        append_md = f"""# [{display_id}] {source_book}{chapter_str}
- 単元: {unit}
- ページ数: {strategy_summary or 'なし'}
 
 ## 問題
{problem_markdown}

## 解説
{explanation_markdown}"""
        try:
            drive_md_synced = drive.append_problem_markdown(unit, append_md)
        except Exception as e:
            logger.error(f"Failed to append markdown to Google Drive: {e}")
            
    # セッション用の一時ファイルをクリーンアップ
    temp_files = list(static_gen_dir.glob(f"temp_{session_id}_*.png"))
    for p in temp_files:
        try:
            p.unlink()
        except Exception:
            pass
            
    return jsonify({
        "status": "success",
        "message": "Problem saved successfully",
        "display_id": display_id,
        "drive_synced": drive_md_synced
    })


# === OCR Reader / Import Routes ======================================

@app.route("/reader")
def reader():
    """OCR読み取りインポート画面を表示"""
    return render_template("reader.html")

@app.route("/api/ocr/read-problem", methods=["POST"])
def ocr_read_problem():
    """Gemini API (3.5 Flash or 3.1 Flash-Lite) を使って問題画像をOCRし、
    Matplotlibコードを生成してプレビュー画像を作る"""
    hint = request.form.get("hint", "").strip()
    model_name = request.form.get("model", "gemini-3.1-flash-lite")
    session_id = request.form.get("session_id", "").strip()
    if not session_id:
        session_id = str(uuid.uuid4())
        
    temp_file_path = None
    pil_images = []
    
    if "file" in request.files:
        file = request.files["file"]
        if file.filename != "":
            temp_dir = Path(tempfile.gettempdir())
            suffix = Path(file.filename).suffix
            temp_file_path = temp_dir / f"upload_{uuid.uuid4().hex}{suffix}"
            file.save(str(temp_file_path))
            
            if suffix.lower() == ".pdf":
                pil_images = convert_pdf_to_images(temp_file_path)
            else:
                try:
                    pil_images = [Image.open(temp_file_path)]
                except Exception as e:
                    logger.error(f"Failed to open image: {e}")
                    
    elif "file_id" in request.form:
        file_id = request.form.get("file_id")
        drive = get_drive()
        try:
            downloaded_path = drive.download_file(file_id)
            if downloaded_path:
                temp_file_path = Path(downloaded_path)
                if temp_file_path.suffix.lower() == ".pdf":
                    pil_images = convert_pdf_to_images(temp_file_path)
                else:
                    pil_images = [Image.open(temp_file_path)]
        except Exception as e:
            logger.error(f"Failed to download from Drive: {e}")
            
    if not pil_images:
        return jsonify({"error": "画像またはPDFの読み込みに失敗しました。"}), 400
        
    prompt = f"""あなたはプロの中学受験算数講師、および優秀なOCRプログラムです。
提供された画像（またはPDFの最初のページ）から、【問題文】と【図形（あれば）の構造】を正確に読み取ってください。

【ヒント】:
{hint if hint else "なし"}

【読み取り指示】:
1. 問題文のテキストを正確に抽出してください（数式は LaTeX 形式の $...$ または $$...$$ で記述してください）。
2. 画像の中に幾何学的な図やグラフなどの図形が含まれている場合、その図形を再現するための Python (Matplotlib) コードを生成してください。
   - 図形描画コードの絶対要件:
     - GUIウインドウ（plt.show()など）は開かず、最後に `plt.savefig("problem_diagram.png", dpi=150, bbox_inches="tight")` で保存するコードにしてください。
     - 図の中の文字や数値も、問題文と整合するように plt.text や plt.annotate で描画してください。
     - 図がつぶれないように、それなりの大きさ（例：figsize=(6, 5)など）で描画してください。
     - 余計な説明（「Matplotlibコードはこちらです」など）や ```python といったMarkdownのコードブロックタグは含めず、純粋なPythonスクリプトのみを出力内の `matplotlib_code` キーに格納してください。

【出力フォーマット】:
必ず以下の構造を持つJSON形式のみで出力してください（前置きの挨拶などは一切不要です）。
{{
  "problem_markdown": "問題文のマークダウン（LaTeX数式入り）\\nもし図形がある場合は、画像を表示したい場所に `![図](problem_diagram.png)` と記述してください。",
  "matplotlib_code": "図形を描画するためのPythonコード（図形がない場合は空文字）"
}}"""

    try:
        model = genai.GenerativeModel(model_name=model_name)
        img_to_send = pil_images[0]
        
        response = model.generate_content(
            [img_to_send, prompt],
            generation_config={
                "response_mime_type": "application/json",
                "temperature": 0.1
            }
        )
        
        res_data = json.loads(response.text.strip())
        problem_markdown = res_data.get("problem_markdown", "")
        matplotlib_code = res_data.get("matplotlib_code", "")
        
        token_usage = {"input_tokens": 0, "output_tokens": 0}
        try:
            usage = response.usage_metadata
            token_usage["input_tokens"] = getattr(usage, "prompt_token_count", 0) or 0
            token_usage["output_tokens"] = getattr(usage, "candidates_token_count", 0) or 0
        except Exception:
            pass
            
        image_url = ""
        error_msg = ""
        if matplotlib_code:
            run_res = run_diagram_code(matplotlib_code, session_id, "problem")
            if run_res.get("success"):
                image_url = run_res.get("image_url", "")
                problem_markdown = problem_markdown.replace("problem_diagram.png", image_url)
            else:
                error_msg = run_res.get("error", "")
                
        if temp_file_path and temp_file_path.exists():
            try: temp_file_path.unlink()
            except Exception: pass
            
        return jsonify({
            "problem_markdown": problem_markdown,
            "matplotlib_code": matplotlib_code,
            "image_url": image_url,
            "error": error_msg,
            "token_usage": token_usage
        })
        
    except Exception as e:
        logger.error(f"OCR problem error: {e}")
        return jsonify({"error": f"OCR処理に失敗しました: {str(e)}"}), 500

@app.route("/api/ocr/read-explanation", methods=["POST"])
def ocr_read_explanation():
    """Gemini APIを使って解説画像をOCRし、詳細化（代数は使わない）して
    Matplotlibコードを生成してプレビューを作る"""
    hint = request.form.get("hint", "").strip()
    problem_text = request.form.get("problem_text", "").strip()
    model_name = request.form.get("model", "gemini-3.1-flash-lite")
    session_id = request.form.get("session_id", "").strip()
    if not session_id:
        session_id = str(uuid.uuid4())
        
    temp_file_path = None
    pil_images = []
    
    if "file" in request.files:
        file = request.files["file"]
        if file.filename != "":
            temp_dir = Path(tempfile.gettempdir())
            suffix = Path(file.filename).suffix
            temp_file_path = temp_dir / f"upload_{uuid.uuid4().hex}{suffix}"
            file.save(str(temp_file_path))
            
            if suffix.lower() == ".pdf":
                pil_images = convert_pdf_to_images(temp_file_path)
            else:
                try:
                    pil_images = [Image.open(temp_file_path)]
                except Exception as e:
                    logger.error(f"Failed to open image: {e}")
                    
    elif "file_id" in request.form:
        file_id = request.form.get("file_id")
        drive = get_drive()
        try:
            downloaded_path = drive.download_file(file_id)
            if downloaded_path:
                temp_file_path = Path(downloaded_path)
                if temp_file_path.suffix.lower() == ".pdf":
                    pil_images = convert_pdf_to_images(temp_file_path)
                else:
                    pil_images = [Image.open(temp_file_path)]
        except Exception as e:
            logger.error(f"Failed to download from Drive: {e}")
            
    if not pil_images:
        return jsonify({"error": "画像またはPDFの読み込みに失敗しました。"}), 400
        
    prompt = f"""あなたはプロの中学受験算数講師、および優秀なOCR・解答詳細化プログラムです。
提供された解説画像（またはPDFの最初のページ）から、【解答解説文】と【図形（あれば）の構造】を正確に読み取り、詳細化してください。

【ベースとなる問題文】:
{problem_text}

【ヒント】:
{hint if hint else "なし"}

【解説の詳細化・OCRの絶対条件】:
1. 解説が簡潔すぎる場合は、中学受験算数を勉強している小学生が躓かないように、思考のプロセスを丁寧に書き足して詳細化（補強）してください。
2. 【代数不使用ルール】: 小学生向けのため、 $x$ や $y$ などの代数・変数は絶対に使用しないでください。
   - 代わりに「①」「④」などの丸数字（比の記号）や、「□（しかく）」などの算数の記号を用いて説明してください。
3. 【トーン】: 算数に真剣に向き合う姿勢を伝える、知的で成熟した落ち着いたトーン（Mature tone）で解説を作成してください。
4. 数式は LaTeX 形式の $...$ または $$...$$ で記述してください。
5. 解説画像の中に幾何学的な図や解説の補助となる図形が含まれている場合、その図形を再現するための Python (Matplotlib) コードを生成してください。
   - 図形描画コードの絶対要件:
     - 最後に `plt.savefig("explanation_diagram.png", dpi=150, bbox_inches="tight")` で保存するコードにしてください。
     - GUIウインドウは開かないでください。
6. この問題に最もふさわしい「解法の核心（コア戦略）」を20〜30文字程度で要約し、`strategy_summary` に格納してください。
7. この問題の単元や解法パターンに関連するタグ（例：面積比, 相似, 旅人算など）を3個程度抽出し、`tags` に格納してください。

【出力フォーマット】:
必ず以下の構造を持つJSON形式のみで出力してください（前置きの挨拶などは一切不要です）。
{{
  "explanation_markdown": "詳細化された解説のマークダウン（LaTeX数式入り）\\nもし図形がある場合は、画像を表示したい場所に `![解説図](explanation_diagram.png)` と記述してください。",
  "matplotlib_code": "解説図を描画するためのPythonコード（図形がない場合は空文字）",
  "strategy_summary": "解法の核心の要約文",
  "tags": ["タグ1", "タグ2", "タグ3"]
}}"""

    try:
        model = genai.GenerativeModel(model_name=model_name)
        img_to_send = pil_images[0]
        
        response = model.generate_content(
            [img_to_send, prompt],
            generation_config={
                "response_mime_type": "application/json",
                "temperature": 0.1
            }
        )
        
        res_data = json.loads(response.text.strip())
        explanation_markdown = res_data.get("explanation_markdown", "")
        matplotlib_code = res_data.get("matplotlib_code", "")
        strategy_summary = res_data.get("strategy_summary", "")
        tags = res_data.get("tags", [])
        
        token_usage = {"input_tokens": 0, "output_tokens": 0}
        try:
            usage = response.usage_metadata
            token_usage["input_tokens"] = getattr(usage, "prompt_token_count", 0) or 0
            token_usage["output_tokens"] = getattr(usage, "candidates_token_count", 0) or 0
        except Exception:
            pass
            
        image_url = ""
        error_msg = ""
        if matplotlib_code:
            run_res = run_diagram_code(matplotlib_code, session_id, "explanation")
            if run_res.get("success"):
                image_url = run_res.get("image_url", "")
                explanation_markdown = explanation_markdown.replace("explanation_diagram.png", image_url)
            else:
                error_msg = run_res.get("error", "")
                
        if temp_file_path and temp_file_path.exists():
            try: temp_file_path.unlink()
            except Exception: pass
            
        return jsonify({
            "explanation_markdown": explanation_markdown,
            "matplotlib_code": matplotlib_code,
            "image_url": image_url,
            "strategy_summary": strategy_summary,
            "tags": tags,
            "error": error_msg,
            "token_usage": token_usage
        })
        
    except Exception as e:
        logger.error(f"OCR explanation error: {e}")
        return jsonify({"error": f"OCR処理に失敗しました: {str(e)}"}), 500

@app.route("/api/problems/import", methods=["POST"])
def import_problem():
    """OCR読み取りされたデータをコンパイルし、SupabaseとGoogle Driveに保存する"""
    data = request.json or {}
    session_id = data.get("session_id")
    source_book = data.get("source_book", "").strip()
    chapter = data.get("chapter", "").strip()
    unit = data.get("unit", "").strip()
    problem_markdown = data.get("problem_markdown", "").strip()
    explanation_markdown = data.get("explanation_markdown", "").strip()
    strategy_summary = data.get("strategy_summary", "").strip()
    tags = data.get("tags") or []
    
    if not source_book or (not problem_markdown and not explanation_markdown):
        return jsonify({"error": "教材名、および本文（問題または解説）は必須です。"}), 400
        
    if not unit or unit.strip() == "":
        unit = "未設定"
        
    display_id = get_next_display_id(unit)
    
    static_gen_dir = _here / "static" / "generated"
    static_gen_dir.mkdir(exist_ok=True, parents=True)
    
    def rename_and_replace(text, display_id):
        if not text:
            return ""
        for suffix in ["problem", "explanation"]:
            temp_filename = f"temp_{session_id}_{suffix}.png"
            real_filename = f"{display_id}_{suffix}.png"
            temp_path = static_gen_dir / temp_filename
            real_path = static_gen_dir / real_filename
            
            temp_url = f"/static/generated/{temp_filename}"
            real_url = f"/static/generated/{real_filename}"
            
            if temp_path.exists():
                shutil.copy(temp_path, real_path)
                text = text.replace(temp_url, real_url)
                logger.info(f"OCR: Renamed diagram {temp_filename} to {real_filename}")
                
                drive = get_drive()
                if drive and drive.drive:
                    try:
                        drive.upload_diagram(unit, str(real_path), real_filename)
                    except Exception as e:
                        logger.error(f"OCR: Failed to upload diagram {real_filename} to Drive: {e}")
        return text
        
    problem_markdown = rename_and_replace(problem_markdown, display_id)
    explanation_markdown = rename_and_replace(explanation_markdown, display_id)
    
    db = get_db()
    grading_status = {"全体": "unanswered"}
    
    problem_record = {
        "display_id": display_id,
        "source_book": source_book,
        "chapter": chapter or None,
        "unit": unit,
        "problem_markdown": problem_markdown,
        "explanation_markdown": explanation_markdown,
        "strategy_summary": strategy_summary,
        "grading_status": grading_status,
        "owner_id": os.getenv("DEFAULT_OWNER_ID", "d1b18b1c-a4dc-4b2e-97af-5153a85e685c")
    }
    
    try:
        db.client.table("math_problems").insert(problem_record).execute()
        logger.info(f"OCR: Successfully inserted problem '{display_id}' into Supabase")
    except Exception as e:
        logger.error(f"OCR: Failed to save to Supabase: {e}")
        return jsonify({"error": f"Supabase保存エラー: {str(e)}"}), 500
        
    drive_md_synced = False
    drive = get_drive()
    if drive and drive.drive:
        chapter_str = f" {chapter}" if chapter else ""
        append_md = f"""# [{display_id}] {source_book}{chapter_str}
- 単元: {unit}
- ページ数: {strategy_summary or 'なし'}
 
 ## 問題
{problem_markdown}

## 解説
{explanation_markdown}"""
        try:
            drive_md_synced = drive.append_problem_markdown(unit, append_md)
        except Exception as e:
            logger.error(f"OCR: Failed to append markdown to Google Drive: {e}")
            
    for suffix in ["problem", "explanation"]:
        p = static_gen_dir / f"temp_{session_id}_{suffix}.png"
        try:
            if p.exists(): p.unlink()
        except Exception: pass
            
    return jsonify({
        "status": "success",
        "message": "Problem saved successfully",
        "display_id": display_id,
        "drive_synced": drive_md_synced
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5058))
    app.run(host="0.0.0.0", port=port, debug=True)
