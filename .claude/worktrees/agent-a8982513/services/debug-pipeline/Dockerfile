FROM asia-northeast1-docker.pkg.dev/consummate-yew-479020-u2/cloud-run-source-deploy/doc-processor-base:latest

WORKDIR /app

# 共通モジュール
COPY shared/ ./shared/

# Webサービス本体
COPY services/debug-pipeline/ ./services/debug-pipeline/

# run_debug_pipeline.py（debug_web.py から import）
COPY scripts/debug/run_debug_pipeline.py ./services/debug-pipeline/run_debug_pipeline.py

ENV PORT=8080
ENV PYTHONPATH=/app
ENV DEBUG_OUTPUT_DIR=/tmp/debug_output

CMD exec gunicorn --bind :$PORT --workers 1 --threads 4 --timeout 3600 --chdir /app/services/debug-pipeline debug_web:app
