# ベースイメージを使用（依存関係インストール済み）
FROM asia-northeast1-docker.pkg.dev/consummate-yew-479020-u2/cloud-run-source-deploy/doc-processor-base:latest

WORKDIR /app

# アプリケーションファイルをコピー（プロジェクトルートからビルドする場合）
COPY services/doc-processor/app.py .
COPY services/doc-processor/templates/ ./templates/
COPY services/doc-processor/start.sh .

# プロジェクトルートからの共通モジュール
COPY shared/__init__.py ./shared/
COPY shared/common/ ./shared/common/
COPY shared/ai/ ./shared/ai/
COPY shared/pipeline/ ./shared/pipeline/
COPY shared/kakeibo/ ./shared/kakeibo/

# ドキュメント処理用のファイル
COPY scripts/processing/ ./scripts/processing/
# app.py から直接インポートできるように /app/ 直下にもコピー
COPY scripts/processing/process_queued_documents.py .

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
