FROM python:3.11-slim

# サービス配下を実行ルートに固定して、他アプリのファイル混入を防ぐ
WORKDIR /app/services/rag-prepare

# 依存関係（rag-prepare 専用）
COPY services/rag-prepare/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# サービスソース（monorepo の shared/ は不要）
COPY services/rag-prepare /app/services/rag-prepare

ENV PYTHONPATH=/app
ENV PORT=8080

CMD ["python", "app.py"]
