FROM python:3.11-slim

WORKDIR /app

# 依存関係
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 共有ライブラリとソースコード
COPY shared ./shared
COPY . .

ENV PYTHONPATH=/app
ENV PORT=8080

CMD ["python", "app.py"]
