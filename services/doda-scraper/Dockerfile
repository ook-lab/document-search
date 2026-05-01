FROM python:3.12-slim

WORKDIR /app

# 依存パッケージと Playwright 本体のインストールをまとめる
COPY services/doda-scraper/requirements.txt /app/services/doda-scraper/requirements.txt
RUN apt-get update && \
    pip install --no-cache-dir -r /app/services/doda-scraper/requirements.txt && \
    pip install --no-cache-dir gunicorn Flask Flask-CORS && \
    playwright install --with-deps chromium && \
    rm -rf /var/lib/apt/lists/*

COPY scripts/ /app/scripts/

# サービス本体
COPY services/doda-scraper/ /app/services/doda-scraper/

ENV PYTHONPATH=/app
ENV PORT=8080

# サーバーを起動
CMD exec gunicorn --bind :$PORT --workers 1 --threads 4 --timeout 600 --chdir services/doda-scraper app:app
