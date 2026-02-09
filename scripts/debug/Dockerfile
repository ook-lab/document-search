# ベースイメージを使用（Tesseract, Poppler, OpenCV, PaddleOCR 等インストール済み）
FROM asia-northeast1-docker.pkg.dev/consummate-yew-479020-u2/cloud-run-source-deploy/doc-processor-base:latest

WORKDIR /app

# 1. 共通モジュール（shared パッケージ）
COPY shared/ ./shared/

# 2. デバッグスクリプト
COPY scripts/debug/ ./scripts/debug/

# 環境変数を設定
ENV PORT=8080
ENV PYTHONPATH=/app
ENV DEBUG_OUTPUT_DIR=/tmp/debug_output

# gunicorn で SSE を正しく配信
# workers=1: SSE + ジョブ管理の dict を共有するため
# threads=4: SSE ストリーミング + API リクエストを並列処理
# timeout=3600: パイプライン実行が長時間になる場合に対応
CMD exec gunicorn --bind :$PORT --workers 1 --threads 4 --timeout 3600 --chdir /app/scripts/debug debug_web:app
