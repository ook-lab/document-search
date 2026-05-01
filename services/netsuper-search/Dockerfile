# Build context: repository root
FROM python:3.12-slim

WORKDIR /app

COPY services/netsuper-search/requirements.txt /app/services/netsuper-search/requirements.txt
RUN apt-get update && \
    pip install --no-cache-dir -r /app/services/netsuper-search/requirements.txt && \
    playwright install --with-deps chromium && \
    rm -rf /var/lib/apt/lists/*

COPY shared/ /app/shared/
COPY scripts/ /app/scripts/
COPY services/netsuper-search/ /app/services/netsuper-search/

ENV PYTHONPATH=/app:/app/services/netsuper-search
ENV PORT=8501

WORKDIR /app/services/netsuper-search

EXPOSE 8501

CMD streamlit run app.py \
    --server.port=${PORT:-8501} \
    --server.address=0.0.0.0 \
    --server.headless=true \
    --server.enableCORS=false \
    --server.enableXsrfProtection=false
