#!/bin/bash

# Flask アプリのみを起動（処理はAPIリクエストで開始）
# --pythonpath で doc_processor を直接指定し services パッケージを経由しない
exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 \
  --pythonpath /app/services/doc_processor wsgi:application
