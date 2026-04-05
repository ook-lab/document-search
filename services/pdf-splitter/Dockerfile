FROM python:3.12-slim

WORKDIR /app

# .pycファイル等を生成しないように設定
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# 依存関係をインストール
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# アプリのコードをコピー
COPY . .

# Cloud Run等でポートを環境変数から取得するための設定
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 app:app
