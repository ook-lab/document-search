FROM python:3.11-slim

WORKDIR /app

# 1. 必要なツールをインストール
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# 2. ライブラリ定義をコピー
COPY services/kakeibo/requirements.txt /app/

# 3. ライブラリをインストール
RUN pip install --no-cache-dir -r requirements.txt

# 4. ソースコードをコピー（shared/ 不要 — kakeibo は完全独立）
COPY services/kakeibo/app.py /app/
COPY services/kakeibo/config.py /app/
COPY services/kakeibo/db_client.py /app/
COPY services/kakeibo/drive_client.py /app/
COPY services/kakeibo/gemini_client.py /app/
COPY services/kakeibo/transaction_processor.py /app/
COPY services/kakeibo/templates/ /app/templates/

# 5. Pythonパス設定
ENV PYTHONPATH=/app
ENV FLASK_APP=app.py
ENV PORT=8080
ENV PYTHONUNBUFFERED=1

# 6. ポート設定
EXPOSE 8080

# 7. 起動コマンド
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "4", "--timeout", "3600", "app:app"]
