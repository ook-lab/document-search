FROM python:3.12-slim

WORKDIR /app

COPY services/doda-dashboard/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

COPY services/doda-dashboard/app.py .
COPY services/doda-dashboard/templates/ ./templates/

ENV PORT=8080

CMD exec gunicorn --bind :$PORT --workers 1 --threads 4 --timeout 120 app:app
