import os
import sys
from flask import Flask, render_template, request, jsonify

# 同ディレクトリ内の flattener をインポートできるようにパスを通す
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from flattener import scan_directory, execute_flatten

app = Flask(
    __name__,
    template_folder="templates",
    static_folder="static",
    static_url_path="/static"
)

# デフォルトでローカルからのリクエストのみを受け付ける（ローカルツールとしてのセキュリティ対策）
# ただし、Cloud Run などにデプロイされることも考慮し、起動時の host 指定に従う

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/scan", methods=["POST"])
def api_scan():
    data = request.get_json() or {}
    root_dir = data.get("root_dir", "").strip().strip('"').strip("'")
    app.logger.info(f"Scan request received for path: {root_dir}")

    if not root_dir:
        return jsonify({"success": False, "error": "フォルダパスを指定してください。"}), 400

    if not os.path.exists(root_dir):
        app.logger.error(f"Path does not exist: {root_dir}")
        return jsonify({"success": False, "error": f"指定されたパスが存在しません。入力されたパス: {root_dir}"}), 400

    if not os.path.isdir(root_dir):
        app.logger.error(f"Path is not a directory: {root_dir}")
        return jsonify({"success": False, "error": "指定されたパスはフォルダではありません。"}), 400

    try:
        mappings = scan_directory(root_dir)
        return jsonify({
            "success": True,
            "root_dir": root_dir,
            "files": mappings
        })
    except Exception as e:
        return jsonify({"success": False, "error": f"スキャン中にエラーが発生しました: {str(e)}"}), 500

@app.route("/api/flatten", methods=["POST"])
def api_flatten():
    data = request.get_json() or {}
    root_dir = data.get("root_dir", "").strip().strip('"').strip("'")
    app.logger.info(f"Flatten request received for path: {root_dir}")
    delete_empty_dirs = data.get("delete_empty_dirs", True)

    if not root_dir:
        return jsonify({"success": False, "error": "フォルダパスを指定してください。"}), 400

    if not os.path.exists(root_dir):
        app.logger.error(f"Path does not exist: {root_dir}")
        return jsonify({"success": False, "error": f"指定されたパスが存在しません。入力されたパス: {root_dir}"}), 400

    if not os.path.isdir(root_dir):
        app.logger.error(f"Path is not a directory: {root_dir}")
        return jsonify({"success": False, "error": "指定されたパスはフォルダではありません。"}), 400

    try:
        # 実行直前にもう一度スキャンを行い、最新の状態でマッピングを構築
        mappings = scan_directory(root_dir)
        
        # 実際にフラット化を実行
        result = execute_flatten(root_dir, mappings, delete_empty_dirs=delete_empty_dirs)
        
        return jsonify({
            "success": True,
            "root_dir": root_dir,
            "success_count": result["success_count"],
            "moved_files": result["moved_files"],
            "deleted_dirs": result["deleted_dirs"],
            "errors": result["errors"]
        })
    except Exception as e:
        return jsonify({"success": False, "error": f"実行中にエラーが発生しました: {str(e)}"}), 500

if __name__ == "__main__":
    # ローカルツールとして、ブラウザが自動的に立ち上がるなどの親切設計にしたいが、
    # シンプルにポート5005で起動する。
    # debug=True だと自動リロードが走るが、本番環境でも起動しやすいようにする。
    port = int(os.environ.get("PORT", 5005))
    app.run(host="127.0.0.1", port=port, debug=True)
