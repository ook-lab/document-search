#!/bin/bash

# Flask アプリのみを起動（処理はAPIリクエストで開始）
# PYTHONPATH=/app により、services.doc_processor.app からインポート可能
exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 services.doc_processor.app:app
