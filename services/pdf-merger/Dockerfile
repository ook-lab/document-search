FROM python:3.11-slim

# システムライブラリのインストール ( fonts-noto-cjk など)
RUN apt-get update && apt-get install -y \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# cloudbuild のコンテキストはリポジトリルートのため、パスを明示する
COPY services/pdf-merger/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# 本サービスはルート shared/ を同梱 shared/ も参照しない（Flask + ローカルモジュールのみ）
COPY services/pdf-merger/ ./

ENV PYTHONPATH=/app
ENV PORT=8080

CMD ["gunicorn", "--bind", ":8080", "--workers", "1", "--threads", "8", "--timeout", "0", "app:app"]
