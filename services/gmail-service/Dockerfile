FROM python:3.11-slim-bookworm

WORKDIR /app

COPY services/gmail-service/requirements.txt /app/services/gmail-service/requirements.txt
RUN apt-get update && \
    pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r /app/services/gmail-service/requirements.txt && \
    playwright install --with-deps chromium && \
    rm -rf /var/lib/apt/lists/*

COPY shared/ /app/shared/
COPY services/gmail-service/ /app/services/gmail-service/

ENV PYTHONPATH=/app:/app/services/gmail-service
ENV PORT=5010

CMD exec gunicorn --bind :$PORT --workers 1 --threads 4 --timeout 300 --chdir services/gmail-service app:app
