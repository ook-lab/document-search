#!/bin/bash

# バックグラウンドで process_queued_documents.py を起動
python process_queued_documents.py &

# Flask アプリを起動
exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 app:app
