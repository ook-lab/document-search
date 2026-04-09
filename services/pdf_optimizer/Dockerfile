# 公式の軽量なPythonイメージを使用
FROM python:3.12-slim

# 作業ディレクトリの設定
WORKDIR /app

# システム依存関係のインストール（PyMuPDF等で共有ライブラリが不足するのを防ぐ）
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 依存関係をコピーしてインストール
COPY requirements.txt .

# 本番環境向けのWSGIサーバーとしてgunicornを追加インストール
RUN pip install --no-cache-dir -r requirements.txt gunicorn

# アプリケーションコードをコンテナ内にコピー
COPY . .

# 一時フォルダなどが無い場合は作成しておく
RUN mkdir -p uploads output

# Cloud Run が期待するポートを設定
ENV PORT 8080

# コンテナ起動時に Gunicorn を使って Flask アプリを立ち上げる
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 app:app
