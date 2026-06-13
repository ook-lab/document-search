FROM python:3.12-slim

WORKDIR /app

COPY services/kanji-tester/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/kanji-tester/app.py .
COPY services/kanji-tester/templates/ ./templates/

ENV PORT=8080

CMD exec gunicorn --bind :$PORT --workers 1 --threads 4 --timeout 0 app:app
