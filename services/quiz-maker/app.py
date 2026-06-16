# -*- coding: utf-8 -*-
import os
import re
import json
import base64
import random
import urllib.request
import uuid
import io
from io import BytesIO
from flask import Flask, request, jsonify, render_template, redirect, send_file, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv
import google.generativeai as genai
from supabase import create_client, Client

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

# ルートの.envをロード
load_dotenv()

# ユーザー設定
USER_NAME = "ikuya"
USER_DISPLAY_NAME = "(i)"
SUBJECTS_TABLE = "quiz_subjects"
HISTORY_TABLE = "quiz_history"
DEFAULT_FOLDER_ENV_VARS = ["IKUYA_SCHOOL_FOLDER_ID", "IKUYA_JUKU_FOLDER_ID", "IKUYA_EXAM_FOLDER_ID", "HOME_LIVING_FOLDER_ID"]

app = Flask(__name__)
CORS(app)

# APIキーの取得と厳密なチェック (フォールバックは全面禁止)
GEMINI_AI_API_KEY = os.environ.get("GEMINI_AI_API_KEY", "").strip()
if not GEMINI_AI_API_KEY:
    print("[WARNING] GEMINI_AI_API_KEY is not set in environment variables.")

# Supabaseクライアントの初期化
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_KEY = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_KEY") or "").strip()

supabase: Client = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as e:
        print(f"[ERROR] Failed to initialize Supabase client: {e}")
        import traceback
        traceback.print_exc()

# Gemini API の設定
if GEMINI_AI_API_KEY:
    genai.configure(api_key=GEMINI_AI_API_KEY)

# 4択クイズのJSONスキーマ定義
QUIZ_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "question": {"type": "STRING", "description": "問題文"},
            "correctAnswer": {"type": "STRING", "description": "正解の選択肢"},
            "wrongOption1": {"type": "STRING", "description": "ダミーの選択肢1"},
            "wrongOption2": {"type": "STRING", "description": "ダミーの選択肢2"},
            "wrongOption3": {"type": "STRING", "description": "ダミーの選択肢3"},
            "explanationShort": {"type": "STRING", "description": "正解した時のための短い解説（1〜2文）"},
            "explanationDetailed": {"type": "STRING", "description": "解説文。正解の理由だけでなく、残りのダミー選択肢3つ（誤答）すべてについても、それぞれ何故間違いなのか、その言葉の意味や内容（例：条約問題なら他の3つの条約がどういう条約か、統計数値問題なら他の選択肢が何位や何の数値なのか、大阪は3位だから間違い等）を解説の中に必ず短く含めてください。"},
            "sourceName": {"type": "STRING", "description": "この問題を作成するにあたり、中心的に引用・参照したファイル名（例: file1.pdf）。必ず送られたファイル名群の中から最も主たるもの1つを選択して正確に記載してください。もしファイル名がないテキスト入力の場合は 'テキスト' としてください。"}
        },
        "required": [
            "question", "correctAnswer", "wrongOption1", 
            "wrongOption2", "wrongOption3", "explanationShort", "explanationDetailed", "sourceName"
        ]
    }
}



def check_db_connection():
    """DB接続とテーブル存在チェック、無ければ警告ログを出力。またカラムの自動拡張を行う"""
    if not supabase:
        return False, "Supabase接続情報が設定されていません。"
    try:
        # テストクエリ
        supabase.table(SUBJECTS_TABLE).select("id").limit(1).execute()
        
        # source_nameカラムは既にDBに存在するためマイグレーション不要
        return True, None
    except Exception as e:
        import traceback
        traceback.print_exc()
        return False, (
            "DB接続またはテーブルが存在しません。Supabaseのマイグレーションが適用されているか確認してください。\n"
            f"エラー詳細: {e}"
        )

@app.route('/')
def index():
    return send_from_directory('templates', 'index.html')

@app.route('/api/subjects', methods=['GET'])
def get_subjects():
    """科目一覧 of 取得"""
    if not supabase:
        return jsonify({"error": "Supabase client is not initialized"}), 500
    
    try:
        response = supabase.table(SUBJECTS_TABLE).select("*").neq("id", "00000000-0000-0000-0000-000000000000").order("sort_order").order("name").execute()
        return jsonify(response.data)
    except Exception as e:
        db_ok, db_err = check_db_connection()
        error_msg = db_err if not db_ok else str(e)
        return jsonify({"error": error_msg}), 500

@app.route('/api/subjects/reorder', methods=['POST'])
def reorder_subjects():
    """科目の表示順序を一括更新"""
    if not supabase:
        return jsonify({"error": "Supabase client is not initialized"}), 500
    
    data = request.json  # リスト: [{"id": "uuid", "sort_order": int}, ...]
    if not isinstance(data, list):
        return jsonify({"error": "Request body must be a list of subjects with sort_order"}), 400
        
    try:
        updated_records = []
        for item in data:
            subject_id = item.get("id")
            sort_order = item.get("sort_order")
            if subject_id is not None and sort_order is not None:
                res = supabase.table(SUBJECTS_TABLE).update({"sort_order": sort_order}).eq("id", subject_id).execute()
                if res.data:
                    updated_records.append(res.data[0])
        return jsonify({"success": True, "updated": updated_records})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/subjects', methods=['POST'])
def save_subject():
    """科目の追加またはプロンプトの更新"""
    if not supabase:
        return jsonify({"error": "Supabase client is not initialized"}), 500
    
    data = request.json
    subject_id = data.get("id")
    name = data.get("name")
    prompt = data.get("prompt")
    
    if not name or not prompt:
        return jsonify({"error": "name and prompt are required"}), 400
        
    try:
        payload = {"name": name, "prompt": prompt}
        if subject_id:
            payload["id"] = subject_id
            response = supabase.table(SUBJECTS_TABLE).upsert(payload).execute()
        else:
            response = supabase.table(SUBJECTS_TABLE).insert(payload).execute()
        return jsonify(response.data[0])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/subjects/<subject_id>', methods=['DELETE'])
def delete_subject(subject_id):
    """科目の削除"""
    if not supabase:
        return jsonify({"error": "Supabase client is not initialized"}), 500
        
    try:
        supabase.table(SUBJECTS_TABLE).delete().eq("id", subject_id).execute()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/optimize-prompt', methods=['POST'])
def optimize_prompt():
    """AIによるプロンプトの自動最適化"""
    if not GEMINI_AI_API_KEY:
        return jsonify({"error": "GEMINI_AI_API_KEY is not configured on the server."}), 500
        
    data = request.json
    current_prompt = data.get("prompt", "")
    subject = data.get("subject", "特定の科目")
    
    if not current_prompt.strip():
        return jsonify({"error": "Prompt cannot be empty"}), 400
        
    try:
        system_instruction = (
            "あなたはプロンプトエンジニアリングの極めて優秀な専門家です。ユーザーが指定した4択クイズ生成用のプロンプト（指示文）を、"
            "AI（特にGeminiなど）が解釈しやすく、より高品質で正確な問題・選択肢・解説を出力できるように、"
            "構造化され、明確な制約事項を含んだプロンプトに最適化・ブラッシュアップしてください。\n"
            "出力は最適化されたプロンプト本文のみとし、前置きの挨拶、解説、およびマークダウン of コードブロック囲み（```）などは一切含めないでください。最適化されたプロンプトテキスト単体を出力してください。"
        )

        # AI Studioの無料枠に適した最新のgemini-3.5-flashを使用
        model = genai.GenerativeModel(
            model_name="gemini-3.5-flash",
            system_instruction=system_instruction
        )
        
        prompt_input = (
            f"対象科目: {subject}\n"
            f"現在のプロンプト:\n{current_prompt}"
        )
        
        response = model.generate_content(
            contents=[prompt_input],
            generation_config={"temperature": 0.2}
        )
        
        optimized_text = response.text.strip()
        # 万が一マークダウンのコードブロックで囲まれて返ってきた場合のクリーンアップ
        if optimized_text.startswith("```"):
            lines = optimized_text.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines[-1].startswith("```"):
                lines = lines[:-1]
            optimized_text = "\n".join(lines).strip()
            
        return jsonify({"optimized_prompt": optimized_text})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/generate-questions', methods=['POST'])
def generate_questions():
    """クイズ問題の生成"""
    if not GEMINI_AI_API_KEY:
        return jsonify({"error": "GEMINI_AI_API_KEY is not configured on the server. Please check environment variables."}), 500
        
    subject_id = request.form.get("subject_id")
    question_count = int(request.form.get("question_count", 10))
    source_text = request.form.get("source_text", "")
    history_analysis_level = request.form.get("history_analysis_level", "none")
    quiz_mode = request.form.get("quiz_mode", "quiz")
    pdf_files = request.files.getlist("pdf")
    
    if not subject_id:
        return jsonify({"error": "subject_id is required"}), 400
        
    # 1. 科目プロンプトの取得
    if not supabase:
        return jsonify({"error": "Supabase client is not initialized"}), 500
        
    try:
        subject_res = supabase.table(SUBJECTS_TABLE).select("*").eq("id", subject_id).execute()
        if not subject_res.data:
            return jsonify({"error": "Subject not found"}), 404
        subject_data = subject_res.data[0]
        system_instruction = subject_data["prompt"] + (
            "\n\n【解説作成の最重要ルール】\n"
            "問題の解説（explanationDetailed）を作成する際は、単に正解の理由を説明するだけでなく、"
            "選択肢にある残りの誤答3つ（ダミー選択肢）すべてについても、それぞれ何故間違いなのか、"
            "その言葉や数値の意味（例：条約なら他の3つの条約の内容、順位や割合なら他の選択肢が何位や何の数値なのか、大阪は3位だから間違い等）を"
            "解説の中に必ず短く含めてください。"
        )
    except Exception as e:
        return jsonify({"error": f"Failed to get subject: {e}"}), 500
        
    # 2. 過去履歴分析（苦手分野の反映）
    history_context = ""
    if history_analysis_level in ["strong", "medium", "weak"]:
        ratio_map = {
            "strong": "最優先で、生成するクイズ全体の【約8割（80%）以上】",
            "medium": "積極的に、生成するクイズ全体の【約5割（50%）程度】",
            "weak": "補助的に、生成するクイズ全体の【約2割（20%）程度】"
        }
        target_ratio = ratio_map.get(history_analysis_level, "約5割（50%）程度")
        
        try:
            # 同じ科目の誤答履歴を直近100件取得（全体の傾向分析用）
            history_res = supabase.table(HISTORY_TABLE) \
                .select("question") \
                .eq("subject_id", subject_id) \
                .eq("is_correct", False) \
                .eq("quiz_mode", quiz_mode) \
                .order("created_at", desc=True) \
                .limit(100) \
                .execute()
                
            if history_res.data:
                wrong_questions = [row["question"] for row in history_res.data]
                recent_wrong = "\n・" + "\n・".join(wrong_questions)
                
                history_context = (
                    f"\n\n【学習者の苦手傾向の分析と出題割合の調整（最重要指示）】\n"
                    f"学習者は同じ科目において過去に以下の問題を間違えています。\n"
                    f"これらの問題全体の傾向を分析し、学習者がどのような概念や知識（例: 特定の時代、特定の実験、特定の文法規則など）でつまずいているかを抽出してください。\n"
                    f"そして、抽出された苦手分野・概念に関連する問題を、今回生成するクイズ全体の【{target_ratio}】を占めるように作成してください。\n\n"
                    f"＜これまでに間違えた問題のリスト＞\n{recent_wrong}\n\n"
                    f"【出題のバリエーションと切り口に関する制約】\n"
                    f"・間違えた問題リストにある知識や概念を再度出題する場合、リスト内の文章とまったく同じ聞き方（問題文）は【絶対に避けてください】。\n"
                    f"・角度を変えた切り口（例: 定義を問うのではなく具体例から選ばせる、原因から結果を聞くのではなく結果から原因を聞く、別の文脈や類似の事例を提示するなど）で出題し、聞き方が変わっても理解できているかを試すバリエーション豊かな問題にしてください。"
                )
        except Exception as e:
            print(f"[WARNING] Failed to fetch quiz history for analysis: {e}")
            
    # 2-1. ファイルに関連する過去の回答履歴を取得（重複排除と未出題の網羅用）
    avoid_context = ""
    file_names = []
    if pdf_files:
        file_names = [f.filename for f in pdf_files if f and f.filename]
    if not file_names and source_text:
        file_names = ["テキスト"]
        
    if file_names:
        try:
            history_questions = []
            # 各ファイル名について部分一致で過去の問題を取得 (created_at DESC で新しい順に)
            # トークン制限を考慮し最大3000件
            for fname in file_names:
                if not fname:
                    continue
                res = supabase.table(HISTORY_TABLE) \
                    .select("question, correct_answer, is_correct") \
                    .ilike("source_name", f"%{fname}%") \
                    .eq("quiz_mode", quiz_mode) \
                    .order("created_at", desc=True) \
                    .limit(3000) \
                    .execute()
                
                if res.data:
                    for row in res.data:
                        q_text = row["question"]
                        ans_text = row["correct_answer"]
                        res_status = "正解" if row["is_correct"] else "不正解"
                        history_questions.append(f"Q: {q_text} / A: {ans_text} (結果: {res_status})")
            
            # 重複を排除してユニークにする
            history_questions = list(dict.fromkeys(history_questions))
            
            if history_questions:
                # 最大3000件に制限
                history_questions = history_questions[:3000]
                q_list = "\n・" + "\n・".join(history_questions)
                avoid_context = (
                    f"\n\n【出題済み問題の重複回避と復習の指示（最優先事項）】\n"
                    f"学習者はこの教材に関連して過去に以下の問題に回答しています（新しい順、最大3000件）。\n"
                    f"＜過去の回答履歴一覧＞{q_list}\n\n"
                    f"以下のルールに従って問題を作成してください：\n"
                    f"1. 過去の履歴の中で結果が【正解】となっている問題については、重複を完全に避け、教材（添付ファイル）のまだ出題されていない別のセクションや、コラム、図表の注記、補足解説などの細かい記述（メイン文章以外）から新しい問題を作成してください。教材全体を満遍なく網羅することを重視してください。\n"
                    f"2. 過去の履歴の中で結果が【不正解】となっている問題については、学習者の苦手分野であるため、同じトピックや知識を問う問題を、出題角度や聞き方（例: 順序の入れ替え、具体例から定義を問うなど）を工夫して再度出題（復習問題）してください。\n"
                )
        except Exception as e:
            print(f"[WARNING] Failed to fetch history for source files: {e}")

    # 3. Gemini APIへのインプット組み立て
    parts = []
    
    # 複数PDFの処理
    pdf_datas = []
    if pdf_files:
        for pdf_file in pdf_files:
            try:
                pdf_bytes = pdf_file.read()
                if pdf_bytes and len(pdf_bytes) > 0:
                    pdf_base64 = base64.b64encode(pdf_bytes).decode("utf-8")
                    pdf_datas.append({
                        "filename": pdf_file.filename,
                        "base64": pdf_base64
                    })
            except Exception as e:
                return jsonify({"error": f"PDFファイルの読み込みに失敗しました ({pdf_file.filename}): {e}"}), 400

    if pdf_datas:
        pdf_names_str = ", ".join([d["filename"] for d in pdf_datas])
        text_instruction = (
            f"添付された複数のPDFドキュメント（ファイル名: {pdf_names_str}）のすべての内容を正確に読み取り、"
            f"重要な用語や概念から学習用の4択形式の問題を正確に{question_count}問作成してください。\n"
            f"異なるファイルの内容を比較させたり、知識を組み合わせたりする横断問題も積極的に作成してください。\n"
            f"{avoid_context}{history_context}"
        )
        parts.append({"text": text_instruction})
        
        # すべての PDF データを inline_data として parts に追加
        for d in pdf_datas:
            parts.append({
                "inline_data": {
                    "mime_type": "application/pdf",
                    "data": d["base64"]
                }
            })
    else:
        if not source_text.strip() or len(source_text.strip()) < 40:
            return jsonify({"error": "テキストが短すぎます。詳細を入力するか、ファイルを指定してください。"}), 400
            
        parts = [
            {
                "text": (
                    f"以下のテキストの内容に基づいて、学習用の4択形式の問題を正確に{question_count}問作成してください。\n\n"
                    f"【テキスト】\n{source_text}{avoid_context}{history_context}"
                )
            }
        ]
        
    # 4. Gemini API 呼び出し
    try:
        model = genai.GenerativeModel(
            model_name="gemini-3.5-flash",
            system_instruction=system_instruction
        )
        
        # 構造化出力設定
        generation_config = {
            "response_mime_type": "application/json",
            "response_schema": QUIZ_SCHEMA,
            "temperature": 0.3
        }
        
        response = model.generate_content(
            contents=parts,
            generation_config=generation_config
        )
        
        # トークン情報の取得
        usage = response.usage_metadata
        tokens = {
            "prompt_tokens": usage.prompt_token_count,
            "candidates_tokens": usage.candidates_token_count,
            "total_tokens": usage.total_token_count
        }
        
        # レスポンスJSONのパース
        response_text = response.text.strip()
        parsed_questions = json.loads(response_text)
        
        if not isinstance(parsed_questions, list):
            raise ValueError("API returned non-array JSON structure.")
            
        # 選択肢をバックエンド側でシャッフル
        formatted_questions = []
        for q in parsed_questions:
            options = [
                q["correctAnswer"], 
                q["wrongOption1"], 
                q["wrongOption2"], 
                q["wrongOption3"]
            ]
            # シャッフル
            random.shuffle(options)
            
            formatted_questions.append({
                "question": q["question"],
                "options": options,
                "correctAnswer": q["correctAnswer"],
                "explanationShort": q["explanationShort"],
                "explanationDetailed": q["explanationDetailed"],
                "sourceName": q.get("sourceName", "不明なソース")
            })
            
        return jsonify({
            "questions": formatted_questions,
            "tokens": tokens
        })
        
    except json.JSONDecodeError as je:
        return jsonify({"error": f"Failed to parse AI output JSON: {je}. Raw output was: {response.text}"}), 500
    except Exception as e:
        return jsonify({"error": f"Failed to generate questions: {str(e)}"}), 500

@app.route('/api/save-history', methods=['POST'])
def save_history():
    """回答履歴のSupabase保存"""
    if not supabase:
        return jsonify({"error": "Supabase client is not initialized"}), 500
        
    data = request.json
    subject_id = data.get("subject_id")
    source_name = data.get("source_name", "不明なソース").strip()
    quiz_mode = data.get("quiz_mode", "quiz")
    history_records = data.get("history", [])

    if not subject_id or not history_records:
        return jsonify({"error": "subject_id and history list are required"}), 400

    try:
        payloads = []
        for record in history_records:
            payloads.append({
                "subject_id": subject_id,
                "quiz_mode": quiz_mode,
                "question": record.get("question"),
                "correct_answer": record.get("correctAnswer"),
                "user_answer": record.get("userAnswer"),
                "is_correct": record.get("isCorrect"),
                "source_name": record.get("source_name") or source_name
            })
            
        # 一括保存（source_nameカラムが存在しない場合はフォールバック）
        try:
            supabase.table(HISTORY_TABLE).insert(payloads).execute()
        except Exception as col_err:
            if 'source_name' in str(col_err):
                print(f"[WARNING] source_name column missing, retrying without it: {col_err}")
                payloads_no_src = [{k: v for k, v in p.items() if k != 'source_name'} for p in payloads]
                supabase.table(HISTORY_TABLE).insert(payloads_no_src).execute()
            else:
                raise
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": f"Failed to save history: {e}"}), 500

# 一時共有ファイル用オンメモリ・ストア
shared_pdfs = {}

@app.route('/manifest.json')
def manifest():
    """PWAのマニフェストを返す"""
    manifest_data = {
        "name": f"AI 4択クイズメーカー {USER_DISPLAY_NAME}",
        "short_name": f"クイズ {USER_DISPLAY_NAME}",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#f8fafc",
        "theme_color": "#6366f1",
        "icons": [
            {
                "src": "/static/icon-192.png",
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "any maskable"
            },
            {
                "src": "/static/icon-512.png",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "any maskable"
            }
        ],
        "share_target": {
            "action": "/api/share-target",
            "method": "POST",
            "enctype": "multipart/form-data",
            "params": {
                "files": [
                    {
                        "name": "pdf",
                        "accept": ["application/pdf"]
                    }
                ]
            }
        }
    }
    return jsonify(manifest_data)

@app.route('/sw.js')
def service_worker():
    """PWAのサービスワーカーを無効化・登録解除する"""
    sw_code = """
    self.addEventListener('install', (e) => {
        self.skipWaiting();
    });
    self.addEventListener('activate', (e) => {
        self.registration.unregister()
            .then(() => self.clients.matchAll())
            .then((clients) => {
                clients.forEach(client => client.navigate(client.url));
            });
    });
    """
    headers = {
        'Content-Type': 'application/javascript',
        'Cache-Control': 'no-cache, no-store, must-revalidate',
        'Pragma': 'no-cache',
        'Expires': '0'
    }
    return sw_code, 200, headers

@app.route('/api/share-target', methods=['POST'])
def share_target():
    """他アプリからのファイル共有受信"""
    pdf_file = request.files.get("pdf")
    if not pdf_file:
        return redirect('/')
        
    try:
        pdf_bytes = pdf_file.read()
        if len(pdf_bytes) == 0:
            return redirect('/?error=empty_file')
            
        temp_id = str(uuid.uuid4())
        shared_pdfs[temp_id] = {
            "data": pdf_bytes,
            "name": pdf_file.filename or "shared_document.pdf"
        }
        return redirect(f"/?shared_id={temp_id}")
    except Exception as e:
        print(f"[ERROR] Failed to process shared file: {e}")
        return redirect('/?error=share_failed')

@app.route('/api/get-shared-pdf', methods=['GET'])
def get_shared_pdf():
    """共有されたPDFデータの引き渡し（フロントエンド用）"""
    shared_id = request.args.get("id")
    if not shared_id or shared_id not in shared_pdfs:
        return jsonify({"error": "File not found or expired"}), 404
        
    try:
        file_info = shared_pdfs.pop(shared_id)
        return send_file(
            BytesIO(file_info["data"]),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=file_info["name"]
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def get_drive_service():
    scopes = ["https://www.googleapis.com/auth/drive.readonly"]
    # 1. ADC (Cloud Run環境用)
    try:
        import google.auth
        creds, _ = google.auth.default(scopes=scopes)
        return build("drive", "v3", credentials=creds)
    except Exception:
        pass

    # 2. サービスアカウントファイル (ローカル用)
    cred_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if cred_path and os.path.exists(cred_path):
        creds = service_account.Credentials.from_service_account_file(
            cred_path, scopes=scopes
        )
        return build("drive", "v3", credentials=creds)
    
    raise RuntimeError("Google Drive credentials not found.")

@app.route('/api/settings', methods=['GET'])
def get_settings():
    if not supabase:
        return jsonify({"error": "Supabase client is not initialized"}), 500
    try:
        res = supabase.table(SUBJECTS_TABLE).select("*").eq("id", "00000000-0000-0000-0000-000000000000").execute()
        if res.data:
            settings_data = json.loads(res.data[0]["prompt"])
            if "gdrive_folders" in settings_data:
                return jsonify(settings_data)
        
        # If no folders exist yet (database record missing), pre-populate from environment variables
        folders = []
        service = None
        try:
            service = get_drive_service()
        except Exception as e:
            print(f"[WARNING] Could not get Google Drive service: {e}")

        for env_var in DEFAULT_FOLDER_ENV_VARS:
            folder_id = os.environ.get(env_var, "").strip()
            if not folder_id:
                continue
            folder_name = env_var.replace("_FOLDER_ID", "").replace("_", " ")
            if service:
                try:
                    folder_info = service.files().get(fileId=folder_id, fields="name", supportsAllDrives=True).execute()
                    folder_name = folder_info.get("name", folder_name)
                except Exception as ex:
                    print(f"[WARNING] Failed to fetch name for folder {folder_id}: {ex}")
            folders.append({"id": folder_id, "name": folder_name})
            
        if folders:
            # Save initialized settings back to database
            payload = {
                "id": "00000000-0000-0000-0000-000000000000",
                "name": "SYSTEM_SETTINGS",
                "prompt": json.dumps({"gdrive_folders": folders}),
                "sort_order": -999
            }
            supabase.table(SUBJECTS_TABLE).upsert(payload).execute()
            return jsonify({"gdrive_folders": folders})
            
        return jsonify({"gdrive_folders": []})
    except Exception as e:
        print(f"[WARNING] Failed to load settings: {e}")
        return jsonify({"gdrive_folders": []})

@app.route('/api/settings', methods=['POST'])
def save_settings():
    if not supabase:
        return jsonify({"error": "Supabase client is not initialized"}), 500
    data = request.json
    folders = data.get("gdrive_folders", [])
    
    try:
        payload = {
            "id": "00000000-0000-0000-0000-000000000000",
            "name": "SYSTEM_SETTINGS",
            "prompt": json.dumps({"gdrive_folders": folders}),
            "sort_order": -999
        }
        supabase.table(SUBJECTS_TABLE).upsert(payload).execute()
        return jsonify({"success": True, "gdrive_folders": folders})
    except Exception as e:
        print(f"[ERROR] Failed to save settings: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/drive/folders', methods=['GET'])
def api_get_drive_folders():
    if not supabase:
        return jsonify([])
    try:
        res = supabase.table(SUBJECTS_TABLE).select("*").eq("id", "00000000-0000-0000-0000-000000000000").execute()
        if res.data:
            settings_data = json.loads(res.data[0]["prompt"])
            folders = settings_data.get("gdrive_folders", [])
            return jsonify(folders)
            
        # If database settings are empty (missing record), resolve from environment variables dynamically
        folders = []
        service = None
        try:
            service = get_drive_service()
        except Exception as e:
            print(f"[WARNING] Could not get Google Drive service: {e}")

        for env_var in DEFAULT_FOLDER_ENV_VARS:
            folder_id = os.environ.get(env_var, "").strip()
            if not folder_id:
                continue
            folder_name = env_var.replace("_FOLDER_ID", "").replace("_", " ")
            if service:
                try:
                    folder_info = service.files().get(fileId=folder_id, fields="name", supportsAllDrives=True).execute()
                    folder_name = folder_info.get("name", folder_name)
                except Exception as ex:
                    print(f"[WARNING] Failed to fetch name for folder {folder_id}: {ex}")
            folders.append({"id": folder_id, "name": folder_name})
        
        if folders:
            # Save to database
            payload = {
                "id": "00000000-0000-0000-0000-000000000000",
                "name": "SYSTEM_SETTINGS",
                "prompt": json.dumps({"gdrive_folders": folders}),
                "sort_order": -999
            }
            supabase.table(SUBJECTS_TABLE).upsert(payload).execute()
            
        return jsonify(folders)
    except Exception as e:
        print(f"[ERROR] Failed to get folders: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/drive/add-folder', methods=['POST'])
def api_add_folder():
    if not supabase:
        return jsonify({"error": "Supabase client is not initialized"}), 500
    data = request.json
    folder_id = data.get("gdrive_folder_id", "").strip()
    if not folder_id:
        return jsonify({"error": "フォルダIDが必要です。"}), 400
        
    try:
        # 既存フォルダリストの取得
        res = supabase.table(SUBJECTS_TABLE).select("*").eq("id", "00000000-0000-0000-0000-000000000000").execute()
        folders = []
        if res.data:
            settings_data = json.loads(res.data[0]["prompt"])
            folders = settings_data.get("gdrive_folders", [])
            
        if any(f["id"] == folder_id for f in folders):
            return jsonify({"error": "このフォルダはすでに登録されています。"}), 400
            
        # Google Drive API からフォルダ名を取得
        try:
            service = get_drive_service()
            folder_info = service.files().get(fileId=folder_id, fields="name", supportsAllDrives=True).execute()
            folder_name = folder_info.get("name", "指定されたフォルダ")
        except Exception as ex:
            print(f"[WARNING] Failed to fetch folder name: {ex}")
            return jsonify({"error": "フォルダ情報の取得に失敗しました。サービスアカウントに共有されているか確認してください。"}), 400
            
        folders.append({"id": folder_id, "name": folder_name})
        
        # 保存
        payload = {
            "id": "00000000-0000-0000-0000-000000000000",
            "name": "SYSTEM_SETTINGS",
            "prompt": json.dumps({"gdrive_folders": folders}),
            "sort_order": -999
        }
        supabase.table(SUBJECTS_TABLE).upsert(payload).execute()
        
        return jsonify({"success": True, "folders": folders})
    except Exception as e:
        print(f"[ERROR] Failed to add folder: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/drive/files', methods=['GET'])
def api_list_drive_files():
    folder_id = request.args.get("folder_id")
    if not folder_id:
        return jsonify({"error": "folder_id is required"}), 400
    
    try:
        service = get_drive_service()
        query = (
            f"'{folder_id}' in parents "
            "and trashed=false "
            "and mimeType = 'application/pdf'"
        )
        
        result = service.files().list(
            q=query,
            spaces="drive",
            fields="files(id, name, mimeType, size, modifiedTime)",
            pageSize=100,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
            corpora="allDrives"
        ).execute()
        
        files = result.get("files", [])
        files.sort(key=lambda x: x.get("modifiedTime", ""), reverse=True)
        return jsonify(files)
    except Exception as e:
        print(f"[ERROR] Failed to list files in folder {folder_id}: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/drive/select-file', methods=['POST'])
def api_select_drive_file():
    data = request.json
    file_id = data.get("file_id")
    filename = data.get("filename", "drive_document.pdf")
    
    if not file_id:
        return jsonify({"error": "file_id is required"}), 400
        
    try:
        service = get_drive_service()
        request_media = service.files().get_media(fileId=file_id, supportsAllDrives=True)
        
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request_media)
        done = False
        while not done:
            _, done = downloader.next_chunk()
            
        pdf_bytes = fh.getvalue()
        
        if len(pdf_bytes) == 0:
            return jsonify({"error": "ダウンロードされたファイルが空です。"}), 400
            
        temp_id = str(uuid.uuid4())
        shared_pdfs[temp_id] = {
            "data": pdf_bytes,
            "name": filename
        }
        
        return jsonify({"shared_id": temp_id})
    except Exception as e:
        print(f"[ERROR] Failed to download file from Drive: {e}")
        return jsonify({"error": f"Googleドライブからのダウンロードに失敗しました: {e}"}), 500

if __name__ == '__main__':

    # 接続テスト
    db_ok, db_err = check_db_connection()
    if not db_ok:
        print(f"[DB WARNING] {db_err}")
    else:
        print("[DB SUCCESS] Connected to Supabase tables successfully.")
        
    app.run(host='0.0.0.0', port=5000, debug=True)
