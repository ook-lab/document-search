FROM python:3.12-slim

WORKDIR /app

COPY services/reading-context-editor/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/reading-context-editor/app.py .
COPY services/reading-context-editor/config.py .
COPY services/reading-context-editor/payload_builder.py .
COPY services/reading-context-editor/templates/ ./templates/

ENV PORT=8080
CMD exec gunicorn --bind :$PORT --workers 1 --threads 4 --timeout 120 app:app
