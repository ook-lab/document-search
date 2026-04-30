# ベースイメージを使用（依存関係インストール済み）
FROM asia-northeast1-docker.pkg.dev/consummate-yew-479020-u2/cloud-run-source-deploy/doc-processor-base:latest

WORKDIR /app

# ビルドコンテキストはリポジトリルート（cloudbuild の `.`）前提
COPY services/doc-processor/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY shared/ ./shared/
COPY scripts/ ./scripts/

# rag-prepare は services 配下のモジュールとして doc-processor から参照（shared には置かない）
COPY services/rag-prepare/ ./services/rag-prepare/

COPY services/doc-processor/app.py ./services/doc_processor/app.py
COPY services/doc-processor/templates/ ./services/doc_processor/templates/
COPY services/doc-processor/start.sh ./start.sh

# Pythonパッケージとして認識させるための __init__.py
RUN touch ./services/__init__.py ./services/doc_processor/__init__.py

ENV PORT=8080
ENV PYTHONPATH=/app

# 起動スクリプトに実行権限を付与
RUN chmod +x start.sh

# サーバーを起動
CMD ["./start.sh"]
