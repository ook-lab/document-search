FROM python:3.11-slim

WORKDIR /app

# requirements.txtのみを先にコピーしてpip installをキャッシュする
COPY services/quiz-analyzer/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 必要なファイルだけを個別にコピー
COPY services/quiz-analyzer/app.py .
COPY services/quiz-analyzer/templates/ ./templates/

ENV PORT=5001
EXPOSE 5001

CMD ["gunicorn", "--bind", "0.0.0.0:5001", "app:app"]
