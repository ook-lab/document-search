"""
Flask Web Application - Document Processing System
ドキュメント処理システムのWebインターフェース（処理専用）
"""
import os
import sys
from pathlib import Path

# プロジェクトルートをPythonパスに追加（ローカル実行時用）
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from datetime import datetime
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
from loguru import logger

app = Flask(__name__)
CORS(app)

# 処理進捗の管理
processing_status = {
    'is_processing': False,
    'current_index': 0,
    'total_count': 0,
    'current_file': '',
    'success_count': 0,
    'failed_count': 0,
    'logs': []
}


# loguruのカスタムハンドラー：ログをprocessing_statusに送信
def log_to_processing_status(message):
    """loguruのログをprocessing_statusに追加"""
    log_record = message.record
    level = log_record['level'].name
    msg = log_record['message']

    # ログレベルに応じてフィルタリング（INFOのみ表示）
    if level in ['INFO', 'WARNING', 'ERROR']:
        timestamp = datetime.now().strftime('%H:%M:%S')
        formatted_msg = f"[{timestamp}] {msg}"
        processing_status['logs'].append(formatted_msg)

        # ログは最大100件まで保持
        if len(processing_status['logs']) > 100:
            processing_status['logs'] = processing_status['logs'][-100:]


# loguruにカスタムハンドラーを追加
logger.add(log_to_processing_status, format="{message}")


@app.route('/')
def index():
    """メインページ - 処理画面にリダイレクト"""
    return render_template('processing.html')


@app.route('/processing')
def processing():
    """ドキュメント処理システムのメインページ"""
    return render_template('processing.html')


@app.route('/api/health', methods=['GET'])
def health_check():
    """ヘルスチェックエンドポイント"""
    return jsonify({
        'status': 'ok',
        'message': 'Document Processing System is running'
    })


@app.route('/api/process/progress', methods=['GET'])
def get_process_progress():
    """
    処理進捗とシステムリソースを取得
    """
    try:
        import psutil

        # CPU使用率
        cpu_percent = psutil.cpu_percent(interval=0.1)

        # メモリ使用率
        memory = psutil.virtual_memory()
        memory_percent = memory.percent
        memory_used_gb = memory.used / (1024 ** 3)
        memory_total_gb = memory.total / (1024 ** 3)

        return jsonify({
            'success': True,
            'processing': processing_status['is_processing'],
            'current_index': processing_status['current_index'],
            'total_count': processing_status['total_count'],
            'current_file': processing_status['current_file'],
            'success_count': processing_status['success_count'],
            'failed_count': processing_status['failed_count'],
            'logs': processing_status['logs'][-50:],  # 最新50件
            'system': {
                'cpu_percent': round(cpu_percent, 1),
                'memory_percent': round(memory_percent, 1),
                'memory_used_gb': round(memory_used_gb, 2),
                'memory_total_gb': round(memory_total_gb, 2)
            }
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/workspaces', methods=['GET'])
def get_workspaces():
    """
    ワークスペース一覧を取得
    """
    try:
        from A_common.database.client import DatabaseClient
        db = DatabaseClient()

        # ワークスペース一覧を取得
        query = db.client.table('Rawdata_FILE_AND_MAIL').select('workspace').execute()

        # ユニークなワークスペースを抽出
        workspaces = set()
        for row in query.data:
            workspace = row.get('workspace')
            if workspace:
                workspaces.add(workspace)

        # ソートしてリスト化
        workspace_list = sorted(list(workspaces))

        return jsonify({
            'success': True,
            'workspaces': workspace_list
        })

    except Exception as e:
        print(f"[ERROR] ワークスペース取得エラー: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/process/stats', methods=['GET'])
def get_process_stats():
    """
    処理キューの統計情報を取得
    """
    try:
        from A_common.database.client import DatabaseClient
        db = DatabaseClient()

        workspace = request.args.get('workspace', 'all')

        query = db.client.table('Rawdata_FILE_AND_MAIL').select('processing_status, workspace')

        if workspace != 'all':
            query = query.eq('workspace', workspace)

        response = query.execute()

        stats = {
            'pending': 0,
            'processing': 0,
            'completed': 0,
            'failed': 0,
            'null': 0
        }

        for doc in response.data:
            status = doc.get('processing_status')
            if status is None:
                stats['null'] += 1
            else:
                stats[status] = stats.get(status, 0) + 1

        stats['total'] = len(response.data)

        processed = stats['completed'] + stats['failed']
        if processed > 0:
            stats['success_rate'] = round(stats['completed'] / processed * 100, 1)
        else:
            stats['success_rate'] = 0.0

        return jsonify({
            'success': True,
            'stats': stats
        })

    except Exception as e:
        print(f"[ERROR] 統計取得エラー: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/process/start', methods=['POST'])
def start_processing():
    """
    ドキュメント処理を開始（バックグラウンド実行）
    """
    global processing_status

    # 既に処理中の場合はエラー
    if processing_status['is_processing']:
        return jsonify({
            'success': False,
            'error': '既に処理が実行中です'
        }), 400

    try:
        from process_queued_documents import DocumentProcessor
        import threading
        import asyncio

        data = request.get_json()
        workspace = data.get('workspace', 'all')
        limit = data.get('limit', 100)
        preserve_workspace = data.get('preserve_workspace', True)

        processor = DocumentProcessor()

        # pending ドキュメントを取得
        docs = processor.get_pending_documents(workspace, limit)

        if not docs:
            return jsonify({
                'success': True,
                'message': '処理対象のドキュメントがありません',
                'processed': 0
            })

        # 進捗状況を初期化
        processing_status['is_processing'] = True
        processing_status['current_index'] = 0
        processing_status['total_count'] = len(docs)
        processing_status['current_file'] = ''
        processing_status['success_count'] = 0
        processing_status['failed_count'] = 0
        processing_status['logs'] = [f"[{datetime.now().strftime('%H:%M:%S')}] 処理開始: {len(docs)}件"]

        # バックグラウンド処理関数
        def background_processing():
            global processing_status

            # スレッド内でloguruハンドラーを追加（スレッドセーフ）
            from loguru import logger as thread_logger
            handler_id = thread_logger.add(log_to_processing_status, format="{message}")

            async def process_all():
                for i, doc in enumerate(docs, 1):
                    # 停止フラグをチェック
                    if not processing_status['is_processing']:
                        processing_status['logs'].append(
                            f"[{datetime.now().strftime('%H:%M:%S')}] ⚠️ 処理が中断されました"
                        )
                        break

                    file_name = doc.get('file_name', 'unknown')
                    title = doc.get('title', '') or '(タイトル未生成)'

                    # 進捗を更新
                    processing_status['current_index'] = i
                    processing_status['current_file'] = title
                    processing_status['logs'].append(
                        f"[{datetime.now().strftime('%H:%M:%S')}] [{i}/{len(docs)}] 処理中: {title}"
                    )

                    # ログは最大100件まで保持
                    if len(processing_status['logs']) > 100:
                        processing_status['logs'] = processing_status['logs'][-100:]

                    success = await processor.process_document(doc, preserve_workspace)

                    if success:
                        processing_status['success_count'] += 1
                        processing_status['logs'].append(
                            f"[{datetime.now().strftime('%H:%M:%S')}] ✅ 成功: {title}"
                        )
                    else:
                        processing_status['failed_count'] += 1
                        processing_status['logs'].append(
                            f"[{datetime.now().strftime('%H:%M:%S')}] ❌ 失敗: {title}"
                        )

            try:
                asyncio.run(process_all())

                # 処理完了
                processing_status['is_processing'] = False
                processing_status['current_file'] = ''
                processing_status['logs'].append(
                    f"[{datetime.now().strftime('%H:%M:%S')}] 処理完了: 成功={processing_status['success_count']}, 失敗={processing_status['failed_count']}"
                )
            except Exception as e:
                processing_status['is_processing'] = False
                processing_status['logs'].append(
                    f"[{datetime.now().strftime('%H:%M:%S')}] ❌ エラー: {str(e)}"
                )
                print(f"[ERROR] バックグラウンド処理エラー: {e}")
            finally:
                # ハンドラーを削除
                thread_logger.remove(handler_id)

        # 別スレッドで処理を開始
        thread = threading.Thread(target=background_processing, daemon=True)
        thread.start()

        # すぐにレスポンスを返す
        return jsonify({
            'success': True,
            'message': '処理を開始しました',
            'total_count': len(docs)
        })

    except Exception as e:
        processing_status['is_processing'] = False
        print(f"[ERROR] 処理開始エラー: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/process/stop', methods=['POST'])
def stop_processing():
    """
    処理を停止
    """
    global processing_status

    if not processing_status['is_processing']:
        return jsonify({
            'success': False,
            'error': '実行中の処理がありません'
        }), 400

    # 停止フラグを立てる
    processing_status['is_processing'] = False
    processing_status['logs'].append(
        f"[{datetime.now().strftime('%H:%M:%S')}] ⚠️ ユーザーによって停止されました"
    )

    return jsonify({
        'success': True,
        'message': '処理を停止しました'
    })


if __name__ == '__main__':
    # 開発環境での実行
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
