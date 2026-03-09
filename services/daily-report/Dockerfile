FROM python:3.12-slim

WORKDIR /app

COPY services/daily-report/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# shared/ をコピー（DatabaseClient・LLMClient・settings が必要）
COPY shared/ ./shared/

COPY services/daily-report/app.py .
COPY services/daily-report/report_generator.py .
COPY services/daily-report/templates/ ./templates/

ENV PORT=8080

CMD exec gunicorn --bind :$PORT --workers 1 --threads 4 --timeout 120 app:app
