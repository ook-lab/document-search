FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY services/kakeibo_view/requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

COPY services/kakeibo_view/app.py /app/
COPY services/kakeibo_view/config.py /app/
COPY services/kakeibo_view/db_client.py /app/
COPY services/kakeibo_view/templates/ /app/templates/

ENV PYTHONPATH=/app
ENV FLASK_APP=app.py
ENV PORT=8080

EXPOSE 8080

CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "4", "--timeout", "120", "app:app"]
