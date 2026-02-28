FROM python:3.12-slim

WORKDIR /app

COPY services/calendar-register/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/calendar-register/app.py .
COPY services/calendar-register/templates/ ./templates/

ENV PORT=8080

CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 60 app:app
