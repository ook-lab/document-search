# ベースイメージを使用（依存関係インストール済み）
FROM asia-northeast1-docker.pkg.dev/consummate-yew-479020-u2/cloud-run-source-deploy/doc-processor-base:latest

WORKDIR /app

# 依存関係
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 共有モジュールとスクリプト（ディレクトリ内にコピー済み）
COPY shared/ ./shared/
COPY scripts/ ./scripts/

# doc-processorサービス本体
COPY app.py ./services/doc_processor/
COPY templates/ ./services/doc_processor/templates/
COPY start.sh .

# Pythonパッケージとして認識させるための __init__.py
RUN touch ./services/__init__.py ./services/doc_processor/__init__.py

ENV PORT=8080
ENV PYTHONPATH=/app

# 起動スクリプトに実行権限を付与
RUN chmod +x start.sh

# サーバーを起動
CMD ["./start.sh"]
