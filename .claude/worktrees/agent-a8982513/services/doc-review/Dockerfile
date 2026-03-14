FROM python:3.11-slim

WORKDIR /app

# システムの基本ツールを入れる
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# 先にライブラリリストだけコピー（キャッシュ活用のため）
COPY services/doc-review/requirements.txt /app/services/doc-review/
COPY requirements.txt /app/

# ライブラリをインストール
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir -r services/doc-review/requirements.txt

# 必要なコードだけをコピー（ここを修正しました）
COPY shared/ /app/shared/
COPY services/doc-review/ /app/services/doc-review/
# frontendフォルダのコピーは削除しました

WORKDIR /app/services/doc-review

# 環境変数の設定
ENV PYTHONUNBUFFERED=1
ENV FLASK_ENV=production

# ポート設定
EXPOSE 8080

# 起動コマンド
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "4", "--timeout", "120", "app:app"]
