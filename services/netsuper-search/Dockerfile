# Python 3.12ベースイメージ
FROM python:3.12-slim

# 作業ディレクトリ
WORKDIR /app

# 依存パッケージをインストール
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# プロジェクトファイルをコピー
COPY app.py .

# ポート設定
ENV PORT=8080
EXPOSE 8080

# Streamlitを起動
CMD streamlit run app.py \
    --server.port=$PORT \
    --server.address=0.0.0.0 \
    --server.headless=true \
    --server.enableCORS=false \
    --server.enableXsrfProtection=false
