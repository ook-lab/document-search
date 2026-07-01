FROM python:3.12-slim

WORKDIR /app

# 依存パッケージのコピーとインストール
COPY services/order-detail-sender/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# アプリケーションソースのコピー
COPY services/order-detail-sender/ ./

ENV PYTHONPATH=/app
ENV PORT=8080

CMD ["gunicorn", "--bind", ":8080", "--workers", "1", "--threads", "8", "--timeout", "0", "app:app"]
