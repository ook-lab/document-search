# -*- coding: utf-8 -*-
import os
import re
import json
import base64
import random
import uuid
import logging
from flask import Flask, request, jsonify, render_template, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv
import google.generativeai as genai
from supabase import create_client, Client
import psycopg2

# .envファイルをロード
load_dotenv()

app = Flask(__name__)
CORS(app)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# APIキーとDB設定
GEMINI_AI_API_KEY = os.environ.get("GEMINI_AI_API_KEY", "").strip()
if GEMINI_AI_API_KEY:
    genai.configure(api_key=GEMINI_AI_API_KEY)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_KEY = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_KEY") or "").strip()

supabase: Client = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as e:
        logger.error(f"Failed to initialize Supabase client: {e}")

# デフォルトの小5漢字語彙リスト (Mode 1 プリセット用)
DEFAULT_GRADE_5_WORDS = [
    "誠心誠意", "対応", "主役", "聖火リレー", "中華料理", "宣伝", "筆舌", "盛大", "貴族", "豆腐",
    "一寸", "推進", "宣言", "寸法", "貿易", "聖歌", "専念", "垂直", "確信", "墓地",
    "危機", "背景", "規則", "経費", "編集", "賛成", "判断", "構造", "余計", "制限",
    "効率", "余地", "混雑", "豊富", "清潔", "絶妙", "退屈", "複雑", "状態",
    "能率", "困難", "容易", "綿密", "適度", "系統", "表情", "詳細", "解説", "成果"
]

# 漢字テスト問題のJSONスキーマ定義
KANJI_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "word": {"type": "STRING", "description": "テスト対象の漢字単語（例：誠心誠意）"},
            "hiragana": {"type": "STRING", "description": "漢字単語のカタカナ読み（例：セイシンセイイ）"},
            "sentence": {"type": "STRING", "description": "書き取りテスト用の短文。対象の漢字単語部分のみをカタカナで表記し、そこに下線を引くか【】で囲んだもの（例：セイシンセイイの対応をする。または 【セイシンセイイ】の対応をする。）"}
        },
        "required": ["word", "hiragana", "sentence"]
    }
}

@app.route('/')
def index():
    return send_from_directory('templates', 'index.html')

@app.route('/api/subjects', methods=['GET'])
def get_subjects():
    """科目一覧の取得"""
    if not supabase:
        return jsonify({"error": "Supabase client is not initialized"}), 500
    try:
        # システム設定を除く科目一覧を取得
        response = supabase.table("quiz_subjects").select("*").neq("id", "00000000-0000-0000-0000-000000000000").order("sort_order").order("name").execute()
        return jsonify(response.data)
    except Exception as e:
        logger.error(f"Failed to fetch subjects: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/generate-kanji-test', methods=['POST'])
def generate_kanji_test():
    """漢字テスト問題の選出と文生成"""
    if not GEMINI_AI_API_KEY:
        return jsonify({"error": "GEMINI_AI_API_KEY is not configured."}), 500

    data = request.json or {}
    mode = data.get("mode", "list") # "list" または "quiz_history"
    subject_id = data.get("subject_id")
    custom_words_str = data.get("custom_words", "")
    question_count = int(data.get("question_count", 10))

    candidates = []

    # 1. 漢字候補単語の選定
    if mode == "list":
        # 漢字リストモード: 入力されたカスタム単語を使用
        if custom_words_str.strip():
            # 改行、カンマなどで分割
            raw_words = re.split(r'[\n,\s]+', custom_words_str.strip())
            candidates = list(dict.fromkeys([w.strip() for w in raw_words if w.strip()]))
        else:
            candidates = DEFAULT_GRADE_5_WORDS.copy()
    else:
        # クイズ履歴モード: 対象科目の正解から漢字を含む言葉を抽出
        if not supabase:
            return jsonify({"error": "Supabase client is not initialized"}), 500
        if not subject_id:
            return jsonify({"error": "subject_id is required for quiz_history mode"}), 400

        try:
            # quiz_history から正解リストを取得
            history_res = supabase.table("quiz_history") \
                .select("correct_answer") \
                .eq("subject_id", subject_id) \
                .execute()

            if history_res.data:
                unique_answers = list(set([row["correct_answer"] for row in history_res.data if row.get("correct_answer")]))
                # 漢字を含む連続する文字列を抽出（単語を途中で分割しない）
                kanji_pattern = re.compile(r'[\u4e00-\u9fff]+')
                for ans in unique_answers:
                    found = kanji_pattern.findall(ans)
                    for word in found:
                        if len(word) >= 2 or word in ["舌", "泉", "劇", "豆", "筆", "盛", "歌", "線", "寸"]: # 1文字は主要語彙のみ
                            candidates.append(word)
                candidates = list(dict.fromkeys(candidates))
            
            if not candidates:
                # 履歴から漢字が抽出できない場合は小5漢字リストをフォールバックとして使用
                logger.warning("No kanji words found in quiz history, falling back to default list.")
                candidates = DEFAULT_GRADE_5_WORDS.copy()
        except Exception as e:
            logger.error(f"Error fetching quiz history: {e}")
            candidates = DEFAULT_GRADE_5_WORDS.copy()

    # 2. 過去の漢字テスト履歴を読み込み、反復学習アルゴリズムを適用
    history_records = []
    if supabase:
        try:
            # 履歴の取得
            query = supabase.table("kanji_history").select("kanji_word, is_correct, ignored")
            if subject_id:
                query = query.eq("subject_id", subject_id)
            hist_res = query.execute()
            history_records = hist_res.data or []
        except Exception as e:
            logger.error(f"Error fetching kanji history: {e}")

    # 無視された（今後使わない）単語を除外
    ignored_words = set([row["kanji_word"] for row in history_records if row.get("ignored")])
    candidates = [w for w in candidates if w not in ignored_words]

    if len(candidates) == 0:
        return jsonify({"error": "利用可能な漢字の候補がありません。リストを追加するか別の科目を選択してください。"}), 400

    # 各単語の正誤カウント
    word_stats = {}
    for row in history_records:
        w = row["kanji_word"]
        if w not in word_stats:
            word_stats[w] = {"total": 0, "incorrect": 0}
        word_stats[w]["total"] += 1
        if not row["is_correct"]:
            word_stats[w]["incorrect"] += 1

    # 誤答率の算出
    for w, stat in word_stats.items():
        stat["error_rate"] = stat["incorrect"] / stat["total"] if stat["total"] > 0 else 0.0

    # 候補単語を「既出（テスト済み）」と「新規」に分類
    tested_candidates = [w for w in candidates if w in word_stats]
    new_candidates = [w for w in candidates if w not in word_stats]

    # 出題履歴の総件数
    total_history_count = len(history_records)

    # 動的な選出比率（新規優先 -> 誤答の反復）
    # 履歴が少ないときは新規優先、増えてきたら誤答率が高い問題を混ぜていく
    selected_words = []
    if total_history_count < 20:
        # 新規 100%
        target_new = question_count
    elif total_history_count < 50:
        # 新規 7割 / 既出（誤答優先） 3割
        target_new = int(question_count * 0.7)
    else:
        # 新規 4割 / 既出（誤答優先） 6割
        target_new = int(question_count * 0.4)

    # 1. 既出（テスト済み）から誤答率の高い順にソートして選出
    tested_candidates.sort(key=lambda w: (word_stats[w]["error_rate"], word_stats[w]["incorrect"], random.random()), reverse=True)
    
    target_tested = question_count - target_new
    selected_tested = tested_candidates[:target_tested]
    selected_words.extend(selected_tested)

    # 2. 残りを新規単語から選出 (シャッフルしてランダム)
    random.shuffle(new_candidates)
    needed_new = question_count - len(selected_words)
    selected_new = new_candidates[:needed_new]
    selected_words.extend(selected_new)

    # 単語数が足りない場合は、既出単語の残りで埋める
    if len(selected_words) < question_count:
        remaining_tested = [w for w in tested_candidates if w not in selected_words]
        needed_more = question_count - len(selected_words)
        selected_words.extend(remaining_tested[:needed_more])

    # それでも足りない場合は候補のシャッフルで埋める (極端に候補が少ない場合)
    if len(selected_words) < question_count:
        random.shuffle(candidates)
        for w in candidates:
            if w not in selected_words:
                selected_words.append(w)
            if len(selected_words) == question_count:
                break

    # 最終出題順をランダムにシャッフル
    random.shuffle(selected_words)
    selected_words = selected_words[:question_count]

    # 3. Geminiによる短文（漢字テストの書き取り文）の生成
    try:
        system_instruction = (
            "あなたは小学校・学習塾で出題する高品質な漢字書き取り小テストの作成者です。\n"
            "与えられた漢字単語について、児童が正しい漢字を書くためのテスト文（または言葉）を生成してください。\n\n"
            "【文の作成ルール】\n"
            "1. テスト対象の漢字単語部分のみをカタカナ（送り仮名が必要な箇所はひらがな等）にし、カタカナ部分は必ず【】で囲んでください。それ以外の部分は小学校5年生が読めるレベルの普通の漢字・ひらがな表記にしてください。\n"
            "2. 長い言葉や、その読み（音）だけで漢字が一意に特定できる言葉（例：違憲立法審査権、郵政民営化、下関条約など）については、無理に文章を長く捏造（作成）する必要はありません。無駄な文字あふれを防ぐため、カタカナの言葉単体、またはごく短い文脈のみを返してください。\n"
            "   - 例：「違憲立法審査権」➔ 「【イケンリッポウシンサケン】」\n"
            "   - 例：「郵政民営化」➔ 「【ユウセイミンエイカ】について。」\n"
            "3. 同音異義語があり文脈がないと特定できない短い言葉（例：改憲と会見、聖火と成果）についてのみ、識別可能な短い文章（一文）を作成して区別させてください。\n"
            "   - 例：「聖火」➔ 「【セイカ】ランナーが走る。」\n"
            "   - 例：「成果」➔ 「日ごろの練習の【セイカ】。」\n"
            "4. 送り仮名がある場合は、カタカナ部分とひらがな部分を正確に分けてください。\n"
            "   - 例：「垂らす」➔ 「【タ】らす」"
        )
        
        model = genai.GenerativeModel(
            model_name="gemini-3.5-flash",
            system_instruction=system_instruction
        )
        
        prompt = (
            f"以下の{len(selected_words)}個の漢字単語を使用して、それぞれに対応する漢字テスト用の書き取り文を正確に作成し、JSON形式で返却してください。\n\n"
            f"単語リスト: {', '.join(selected_words)}"
        )
        
        response = model.generate_content(
            contents=[prompt],
            generation_config={
                "response_mime_type": "application/json",
                "response_schema": KANJI_SCHEMA,
                "temperature": 0.2
            }
        )
        
        response_text = response.text.strip()
        logger.info(f"Gemini raw response: {response_text}")
        questions = json.loads(response_text)
        
        # 稀にGeminiが出力単語（word）を勝手に変えた場合、元に戻すか補正する
        for i, q in enumerate(questions):
            if i < len(selected_words):
                # 出力順序を元の単語と整合させる
                q["word"] = selected_words[i]
                # 送り仮名調整などのための簡単な確認
                # 文の中にカタカナで読みが含まれているか確認し、含まれていなければ調整
                # 例: セイシンセイイ など

        return jsonify({
            "questions": questions,
            "mode": mode,
            "subject_id": subject_id
        })

    except Exception as e:
        logger.error(f"Gemini API or Parse Error: {e}", exc_info=True)
        # フォールバック: AIエラー時はカタカナ読みとシンプルなプレースホルダー文を生成
        fallback_questions = []
        for w in selected_words:
            # 簡易カタカナ化 (実際はAI生成が望ましいが、クラッシュ防止用)
            fallback_questions.append({
                "word": w,
                "hiragana": w,
                "sentence": f"【{w}】をていねいに書く。"
            })
        return jsonify({
            "questions": fallback_questions,
            "mode": mode,
            "subject_id": subject_id,
            "warning": f"AI生成に一部失敗したため、簡易プレースホルダー文を出力しました: {e}"
        })

@app.route('/api/save-kanji-history', methods=['POST'])
def save_kanji_history():
    """採点結果の履歴保存"""
    if not supabase:
        return jsonify({"error": "Supabase client is not initialized"}), 500

    data = request.json or {}
    subject_id = data.get("subject_id")
    results = data.get("results", []) # [{"word": "太陽", "sentence": "...", "isCorrect": true, "ignored": false}]

    if not results:
        return jsonify({"error": "Results list is required."}), 400

    # null/None に直す（科目なしのリストモード時はUUID NULL）
    db_subject_id = subject_id if subject_id else None

    try:
        payloads = []
        for row in results:
            payloads.append({
                "subject_id": db_subject_id,
                "kanji_word": row.get("word"),
                "sentence": row.get("sentence"),
                "is_correct": row.get("isCorrect"),
                "ignored": row.get("ignored", False)
            })

        supabase.table("kanji_history").insert(payloads).execute()
        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Failed to save kanji history: {e}")
        return jsonify({"error": f"Failed to save history: {e}"}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, debug=True)
