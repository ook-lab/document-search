import os
import json
import base64
import random
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from dotenv import load_dotenv
import google.generativeai as genai
from supabase import create_client, Client

# ルートの.envをロード
load_dotenv()

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
            "explanationDetailed": {"type": "STRING", "description": "不正解だった時のための詳しい解説（なぜそれが正解なのか、他の選択肢がなぜ違うのかなど）"}
        },
        "required": [
            "question", "correctAnswer", "wrongOption1", 
            "wrongOption2", "wrongOption3", "explanationShort", "explanationDetailed"
        ]
    }
}

def check_db_connection():
    """DB接続とテーブル存在チェック、無ければ警告ログを出力"""
    if not supabase:
        return False, "Supabase接続情報が設定されていません。"
    try:
        # テストクエリ
        supabase.table("quiz_subjects").select("id").limit(1).execute()
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
    return render_template('index.html')

@app.route('/api/subjects', methods=['GET'])
def get_subjects():
    """科目一覧の取得"""
    if not supabase:
        return jsonify({"error": "Supabase client is not initialized"}), 500
    
    try:
        response = supabase.table("quiz_subjects").select("*").order("sort_order").order("name").execute()
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
                res = supabase.table("quiz_subjects").update({"sort_order": sort_order}).eq("id", subject_id).execute()
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
            response = supabase.table("quiz_subjects").upsert(payload).execute()
        else:
            response = supabase.table("quiz_subjects").insert(payload).execute()
        return jsonify(response.data[0])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/subjects/<subject_id>', methods=['DELETE'])
def delete_subject(subject_id):
    """科目の削除"""
    if not supabase:
        return jsonify({"error": "Supabase client is not initialized"}), 500
        
    try:
        supabase.table("quiz_subjects").delete().eq("id", subject_id).execute()
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
    pdf_file = request.files.get("pdf")
    
    if not subject_id:
        return jsonify({"error": "subject_id is required"}), 400
        
    # 1. 科目プロンプトの取得
    if not supabase:
        return jsonify({"error": "Supabase client is not initialized"}), 500
        
    try:
        subject_res = supabase.table("quiz_subjects").select("*").eq("id", subject_id).execute()
        if not subject_res.data:
            return jsonify({"error": "Subject not found"}), 404
        subject_data = subject_res.data[0]
        system_instruction = subject_data["prompt"]
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
            history_res = supabase.table("quiz_history") \
                .select("question") \
                .eq("subject_id", subject_id) \
                .eq("is_correct", False) \
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
            
    # 3. Gemini APIへのインプット組み立て
    parts = []
    
    if pdf_file:
        try:
            pdf_bytes = pdf_file.read()
            pdf_base64 = base64.b64encode(pdf_bytes).decode("utf-8")
            
            parts = [
                {
                    "text": (
                        f"添付されたPDFドキュメントの内容を読み取り、重要な用語や概念から"
                        f"学習用の4択形式の問題を正確に{question_count}問作成してください。{history_context}"
                    )
                },
                {
                    "inline_data": {
                        "mime_type": "application/pdf",
                        "data": pdf_base64
                    }
                }
            ]
        except Exception as e:
            return jsonify({"error": f"Failed to process PDF file: {e}"}), 400
    else:
        if not source_text.strip() or len(source_text.strip()) < 40:
            return jsonify({"error": "テキストが短すぎます。詳細を入力するか、ファイルを指定してください。"}), 400
            
        parts = [
            {
                "text": (
                    f"以下のテキストの内容に基づいて、学習用の4択形式の問題を正確に{question_count}問作成してください。\n\n"
                    f"【テキスト】\n{source_text}{history_context}"
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
                "explanationDetailed": q["explanationDetailed"]
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
    history_records = data.get("history", [])
    
    if not subject_id or not history_records:
        return jsonify({"error": "subject_id and history list are required"}), 400
        
    try:
        payloads = []
        for record in history_records:
            payloads.append({
                "subject_id": subject_id,
                "question": record.get("question"),
                "correct_answer": record.get("correctAnswer"),
                "user_answer": record.get("userAnswer"),
                "is_correct": record.get("isCorrect")
            })
            
        # 一括保存
        supabase.table("quiz_history").insert(payloads).execute()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": f"Failed to save history: {e}"}), 500

if __name__ == '__main__':
    # 接続テスト
    db_ok, db_err = check_db_connection()
    if not db_ok:
        print(f"[DB WARNING] {db_err}")
    else:
        print("[DB SUCCESS] Connected to Supabase tables successfully.")
        
    app.run(host='0.0.0.0', port=5000, debug=True)
