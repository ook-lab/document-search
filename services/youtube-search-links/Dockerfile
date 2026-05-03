FROM python:3.12-slim

WORKDIR /app

COPY services/youtube-search-links/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/youtube-search-links/app.py .
COPY services/youtube-search-links/fetch_urls.py .
COPY services/youtube-search-links/fetch_web.py .
COPY services/youtube-search-links/templates/ ./templates/

ENV PORT=8080
CMD exec gunicorn --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 300 app:app
