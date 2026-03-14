# ベースイメージを使用（依存関係インストール済み）
FROM asia-northeast1-docker.pkg.dev/consummate-yew-479020-u2/cloud-run-source-deploy/doc-processor-base:latest

WORKDIR /app

# ディレクトリ構造を維持してコピー
# PYTHONPATH=/app により、shared, scripts はそのまま import 可能

# 1. 共通モジュール（shared パッケージ）
COPY shared/ ./shared/

# 2. スクリプト（CLIツール用、オプション）
COPY scripts/ ./scripts/

# 3. doc-processorサービス本体
COPY services/doc-processor/app.py ./services/doc_processor/
COPY services/doc-processor/templates/ ./services/doc_processor/templates/
COPY services/doc-processor/start.sh .

# Pythonパッケージとして認識させるための __init__.py
RUN touch ./services/__init__.py ./services/doc_processor/__init__.py

# 環境変数を設定
ENV PORT=8080
ENV PYTHONPATH=/app

# API Keys - 本番環境では Cloud Run の環境変数で上書き推奨
ARG GOOGLE_AI_API_KEY
ARG ANTHROPIC_API_KEY
ARG OPENAI_API_KEY
ARG SUPABASE_URL
ARG SUPABASE_KEY
ARG SUPABASE_SERVICE_ROLE_KEY

ENV GOOGLE_AI_API_KEY=${GOOGLE_AI_API_KEY}
ENV ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
ENV OPENAI_API_KEY=${OPENAI_API_KEY}
ENV SUPABASE_URL=${SUPABASE_URL}
ENV SUPABASE_KEY=${SUPABASE_KEY}
ENV SUPABASE_SERVICE_ROLE_KEY=${SUPABASE_SERVICE_ROLE_KEY}

# 起動スクリプトに実行権限を付与
RUN chmod +x start.sh

# サーバーを起動
CMD ["./start.sh"]
