FROM python:3.12-slim-bookworm

WORKDIR /app

RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-jpn \
    libtesseract-dev \
    poppler-utils \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY services/pipeline-lab/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r /app/requirements.txt

COPY services/pipeline-lab/ /app/services/pipeline-lab/

ENV PYTHONPATH=/app/services/pipeline-lab
ENV PORT=8080
ENV PIPELINE_LAB_NO_BROWSER=1

CMD exec gunicorn --bind :$PORT --workers 1 --threads 4 --timeout 600 --chdir /app/services/pipeline-lab app:app
