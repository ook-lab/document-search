# ベースイメージを使用（依存関係インストール済み）
FROM asia-northeast1-docker.pkg.dev/consummate-yew-479020-u2/cloud-run-source-deploy/doc-processor-base:latest

WORKDIR /app

# ビルドコンテキストはリポジトリルート（cloudbuild の `.`）前提
COPY services/doc-processor/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# パイプライン共有コード（本番イメージにはワーカー用 scripts は含めない＝ローカル CLI のみ）
COPY shared/ ./shared/

# 目視レビュー UI（review/ の blueprints / services / templates のみ同梱。Gmail クリーナは gmail-service へ）
COPY services/doc-processor/review/blueprints ./services/doc_processor/bundled/review/blueprints
COPY services/doc-processor/review/services ./services/doc_processor/bundled/review/services
COPY services/doc-processor/review/templates ./services/doc_processor/bundled/review/templates

COPY services/doc-processor/app.py ./services/doc_processor/app.py
COPY services/doc-processor/hub_register.py ./services/doc_processor/hub_register.py
COPY services/doc-processor/wsgi.py ./services/doc_processor/wsgi.py
COPY services/doc-processor/pipeline_debug ./services/doc_processor/pipeline_debug/
COPY services/doc-processor/templates/ ./services/doc_processor/templates/
COPY services/doc-processor/static/ ./services/doc_processor/static/
COPY services/doc-processor/start.sh ./start.sh

# doc_processor のみパッケージ化（services/ には __init__.py を置かない）
# services/__init__.py を置くと review blueprints の "from services.auth_service"
# が /app/services/ を先に見て bundled/review/services/ に到達できなくなる
RUN touch ./services/doc_processor/__init__.py

ENV PORT=8080
ENV PYTHONPATH=/app

RUN chmod +x start.sh

CMD ["./start.sh"]
