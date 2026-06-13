import os
import json
from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

app = Flask(__name__)
CORS(app)

# Supabaseクライアントの初期化
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_KEY = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_KEY") or "").strip()

supabase: Client = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as e:
        print(f"[ERROR] Failed to initialize Supabase client: {e}")

@app.route('/')
def index():
    return send_from_directory('templates', 'index.html')

@app.route('/api/stats', methods=['GET'])
def get_stats():
    if not supabase:
        return jsonify({"error": "Supabase client is not initialized"}), 500
        
    from flask import request
    user = request.args.get("user", "ikuya").strip().lower()
    
    if user == "ema":
        subjects_table = "ema_quiz_subjects"
        history_table = "ema_quiz_history"
    else:
        subjects_table = "quiz_subjects"
        history_table = "quiz_history"
        
    try:
        # DBから回答履歴と科目を全件取得
        history_res = supabase.table(history_table).select("*").execute()
        subjects_res = supabase.table(subjects_table).select("id, name").neq("id", "00000000-0000-0000-0000-000000000000").execute()
        
        subject_map = {s["id"]: s["name"] for s in subjects_res.data}
        history_data = history_res.data
        
        # 1. セッション（同じ日に同じ科目・ファイル名で、ほぼ同時刻に回答した一連のログ）のグループ化
        sessions = {}
        for item in history_data:
            sub_id = item["subject_id"]
            sub_name = subject_map.get(sub_id, "不明な科目")
            
            # システム設定レコードは除外
            if sub_id == "00000000-0000-0000-0000-000000000000":
                continue
                
            source_name = item.get("source_name") or "テキスト入力"
            created_at = item["created_at"]
            
            # 1分以内の誤差を同一セッションと見なす（created_atはISOフォーマット: '2026-06-03T04:32:00.123+00:00'）
            session_time = created_at[:16] # "YYYY-MM-DDTHH:MM"
            
            session_key = (session_time, sub_id, source_name)
            if session_key not in sessions:
                sessions[session_key] = {
                    "datetime": created_at,
                    "date": created_at[:10], # "YYYY-MM-DD"
                    "subject_name": sub_name,
                    "source_name": source_name,
                    "total_questions": 0,
                    "correct_count": 0,
                    "questions": []
                }
            
            sessions[session_key]["total_questions"] += 1
            if item["is_correct"]:
                sessions[session_key]["correct_count"] += 1
            
            sessions[session_key]["questions"].append({
                "question": item["question"],
                "correct_answer": item["correct_answer"],
                "user_answer": item["user_answer"],
                "is_correct": item["is_correct"]
            })

        # セッションリストの作成と新しい順でのソート
        session_list = []
        for key, s in sessions.items():
            s["accuracy"] = round((s["correct_count"] / s["total_questions"]) * 100, 1) if s["total_questions"] > 0 else 0
            session_list.append(s)
        session_list.sort(key=lambda x: x["datetime"], reverse=True)
        
        # 2. 総合統計の算出
        total_sessions = len(session_list)
        total_questions = sum(s["total_questions"] for s in session_list)
        total_correct = sum(s["correct_count"] for s in session_list)
        overall_accuracy = round((total_correct / total_questions) * 100, 1) if total_questions > 0 else 0
        unique_dates = len(set(s["date"] for s in session_list))
        
        # 3. 日別集計
        daily_stats = {}
        for s in session_list:
            d = s["date"]
            if d not in daily_stats:
                daily_stats[d] = {"date": d, "total_questions": 0, "correct_count": 0, "session_count": 0}
            daily_stats[d]["total_questions"] += s["total_questions"]
            daily_stats[d]["correct_count"] += s["correct_count"]
            daily_stats[d]["session_count"] += 1
            
        daily_stats_list = []
        for d, stats in daily_stats.items():
            stats["accuracy"] = round((stats["correct_count"] / stats["total_questions"]) * 100, 1)
            daily_stats_list.append(stats)
        daily_stats_list.sort(key=lambda x: x["date"]) # 日付の古い順（時系列グラフ用）
        
        # 4. 科目別集計
        subject_stats = {}
        for s in session_list:
            name = s["subject_name"]
            if name not in subject_stats:
                subject_stats[name] = {"subject_name": name, "total_questions": 0, "correct_count": 0, "session_count": 0}
            subject_stats[name]["total_questions"] += s["total_questions"]
            subject_stats[name]["correct_count"] += s["correct_count"]
            subject_stats[name]["session_count"] += 1
            
        subject_stats_list = []
        for name, stats in subject_stats.items():
            stats["accuracy"] = round((stats["correct_count"] / stats["total_questions"]) * 100, 1)
            subject_stats_list.append(stats)
            
        # 5. ファイル別（プリント別）集計
        file_stats = {}
        for s in session_list:
            fname = s["source_name"]
            if fname not in file_stats:
                file_stats[fname] = {
                    "source_name": fname, 
                    "subject_name": s["subject_name"],
                    "total_questions": 0, 
                    "correct_count": 0, 
                    "session_count": 0,
                    "last_accuracy": s["accuracy"],
                    "last_date": s["date"]
                }
            # 常に最新セッションの正解率を保持（新しい順に処理されているため、最初に見たものが最新）
            file_stats[fname]["total_questions"] += s["total_questions"]
            file_stats[fname]["correct_count"] += s["correct_count"]
            file_stats[fname]["session_count"] += 1
            
        file_stats_list = []
        for fname, stats in file_stats.items():
            stats["accuracy"] = round((stats["correct_count"] / stats["total_questions"]) * 100, 1)
            file_stats_list.append(stats)
        # 苦手順（正解率が低い順）にソート
        file_stats_list.sort(key=lambda x: x["accuracy"])
        
        return jsonify({
            "summary": {
                "total_sessions": total_sessions,
                "total_questions": total_questions,
                "overall_accuracy": overall_accuracy,
                "study_days": unique_dates
            },
            "daily_stats": daily_stats_list,
            "subject_stats": subject_stats_list,
            "file_stats": file_stats_list,
            "sessions": session_list
        })
    except Exception as e:
        print(f"[ERROR] Failed to calculate statistics: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=True)
