#!/bin/bash

# Flask アプリのみを起動（処理はAPIリクエストで開始）
exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 app:app
