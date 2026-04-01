FROM python:3.12-slim

WORKDIR /app

COPY services/html-to-a4/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# `html-to-a4` のファイルをコピー
COPY services/html-to-a4/app.py .
COPY services/html-to-a4/templates/ ./templates/

# ポート設定
ENV PORT=5051

# Gunicornでアプリを起動
CMD exec gunicorn --bind 0.0.0.0:$PORT --workers 1 --threads 8 --timeout 0 app:app
