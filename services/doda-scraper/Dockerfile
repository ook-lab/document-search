# Python 3.12ベースイメージ
FROM python:3.12-slim

# Playwright 用に依存パッケージをインストール
RUN apt-get update && apt-get install -y \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    librandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 依存パッケージをインストール
COPY services/doda-scraper/requirements.txt /app/services/doda-scraper/requirements.txt
RUN pip install --no-cache-dir -r /app/services/doda-scraper/requirements.txt
# Flask/gunicornを追加インストール
RUN pip install --no-cache-dir gunicorn Flask Flask-CORS

# Playwright 実行用ブラウザのインストール（Cloud Run上で新規起動する場合に必要）
RUN playwright install chromium

# 共通モジュール & スクリプト
COPY shared/ /app/shared/
COPY scripts/ /app/scripts/

# サービス本体（パッケージ名として有効なようにアンダースコアに変換）
COPY services/doda-scraper/ /app/services/doda_scraper/

# Python パッケージ認識用
RUN touch services/__init__.py

ENV PYTHONPATH=/app
ENV PORT=8080

# サーバーを起動
CMD exec gunicorn --bind :$PORT --workers 1 --threads 4 --timeout 600 services.doda_scraper.app:app
