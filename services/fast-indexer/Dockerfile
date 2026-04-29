FROM python:3.11-slim

# サービス配下を実行ルートに固定して、他アプリのファイル混入を防ぐ
WORKDIR /app/services/fast-indexer

# 依存関係（fast-indexer専用）
COPY services/fast-indexer/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# 共有ライブラリとサービスソース
COPY shared /app/shared
COPY services/fast-indexer /app/services/fast-indexer

ENV PYTHONPATH=/app
ENV PORT=8080

CMD ["python", "app.py"]
