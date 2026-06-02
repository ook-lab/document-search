FROM python:3.12-slim

WORKDIR /app

COPY services/quiz-maker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/quiz-maker/app.py .
COPY services/quiz-maker/templates/ ./templates/

ENV PORT=8080

CMD exec gunicorn --bind :$PORT --workers 1 --threads 4 --timeout 0 app:app
