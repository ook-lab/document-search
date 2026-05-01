FROM python:3.12-slim

WORKDIR /app

# 依存パッケージと Playwright 本体のインストールをまとめる
# apt-get update してから playwright install --with-deps することで
# システム環境に必要なライブラリを自動的に導入、最後に chromium を取得します
COPY services/data-ingestion/requirements.txt /app/services/data-ingestion/requirements.txt
RUN apt-get update && \
    pip install --no-cache-dir -r /app/services/data-ingestion/requirements.txt && \
    playwright install --with-deps chromium && \
    rm -rf /var/lib/apt/lists/*

# スクリプト (scripts パッケージ) をコピー
COPY scripts/ /app/scripts/

# data-ingestion サービス本体をコピー
COPY services/data-ingestion/ /app/services/data-ingestion/

ENV PYTHONPATH=/app/services/data-ingestion:/app
ENV PORT=8080

# サーバーを起動
CMD exec gunicorn --bind :$PORT --workers 1 --threads 4 --timeout 300 --chdir services/data-ingestion app:app
