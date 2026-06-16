FROM python:3.12-slim

# Set work directory
WORKDIR /app

# Copy requirements and install
COPY services/pdf-splitter/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY services/pdf-splitter/app.py .
COPY services/pdf-splitter/templates/ ./templates/

# Expose standard port
ENV PORT=8080

# Run with gunicorn
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 app:app
