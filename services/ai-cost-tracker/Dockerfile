FROM python:3.12-slim

WORKDIR /app

COPY services/ai-cost-tracker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/ai-cost-tracker/app.py .
COPY services/ai-cost-tracker/templates/ ./templates/

COPY shared/__init__.py ./shared/__init__.py
COPY shared/common/ ./shared/common/

ENV PORT=8080

CMD exec gunicorn --bind :$PORT --workers 1 --threads 4 --timeout 0 app:app
