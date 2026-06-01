FROM python:3.11-slim

WORKDIR /app

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

COPY services/sansu-base/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir gunicorn

COPY services/sansu-base/app.py .
COPY services/sansu-base/google_drive_handler.py .
COPY services/sansu-base/dms/ ./dms/
COPY services/sansu-base/static/ ./static/
COPY services/sansu-base/templates/ ./templates/

ENV PORT=8080
ENV PYTHONPATH=/app
ENV FLASK_APP=app.py
ENV PYTHONUNBUFFERED=1

EXPOSE 8080

CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 3600 app:app
