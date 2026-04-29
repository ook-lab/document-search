FROM python:3.11-slim

# システムライブラリのインストール (PyMuPDF等に必要)
RUN apt-get update && apt-get install -y \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 依存関係
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 共有ライブラリとソースコード
COPY shared ./shared
COPY . .

ENV PYTHONPATH=/app
ENV PORT=8080

CMD ["gunicorn", "--bind", ":8080", "--workers", "1", "--threads", "8", "--timeout", "0", "app:app"]
