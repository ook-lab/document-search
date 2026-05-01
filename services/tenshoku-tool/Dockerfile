FROM python:3.12-slim

# WeasyPrint の依存システムライブラリ
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libcairo2 \
    libgdk-pixbuf-2.0-0 \
    libffi-dev \
    shared-mime-info \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY services/tenshoku-tool/requirements.txt /app/services/tenshoku-tool/requirements.txt
RUN pip install --no-cache-dir -r /app/services/tenshoku-tool/requirements.txt

COPY scripts/ /app/scripts/

# スクレイパー本体（subprocess から呼び出し）
COPY services/doda-scraper/ /app/services/doda-scraper/

# サービス本体
COPY services/tenshoku-tool/ /app/services/tenshoku-tool/

ENV PYTHONPATH=/app
ENV PORT=8080

CMD exec gunicorn --bind :$PORT --workers 1 --threads 4 --timeout 600 --chdir services/tenshoku-tool app:app
