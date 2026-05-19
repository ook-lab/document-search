"""
pipeline-lab: 対話用。1 ジョブあたり常に 1 本の PDF だけをパイプラインに渡す単一 Flask アプリ（キュー・別ワーカーなし）。

起動（どの cwd でも可。``PYTHONPATH`` は app がリポジトリルートを自動付与）::

    python services/pipeline-lab/app.py

    または ``services/pipeline-lab`` で ``python app.py``。

起動後、既定ブラウザで ``http://127.0.0.1:<PORT>/pipeline-lab/`` を開きます（``PORT`` 未設定時 5055）。
ブラウザを開かないときは ``PIPELINE_LAB_NO_BROWSER=1``。

LLM 用: ``GOOGLE_AI_API_KEY``（.env またはデプロイ基盤が注入）。
"""

import os
import sys
import threading
import webbrowser
from pathlib import Path

_lab_dir = Path(__file__).resolve().parent
_repo_root = _lab_dir.parent.parent
_lab_str = str(_lab_dir)
_repo_str = str(_repo_root)
if _lab_str not in sys.path:
    sys.path.insert(0, _lab_str)
if _repo_str not in sys.path:
    sys.path.insert(0, _repo_str)
os.environ.setdefault('PROJECT_ROOT', _repo_str)

from flask import Flask, redirect, url_for
from dotenv import load_dotenv

load_dotenv(_repo_root / '.env')
load_dotenv(_lab_dir / '.env')

from blueprints.lab import lab_bp


def create_app():
    # リポジトリルートから `python services/pipeline-lab/app.py` でも template/static を確実に解決する
    app = Flask(
        __name__,
        root_path=str(_lab_dir),
        static_folder='static',
        template_folder='templates',
    )
    app.secret_key = os.environ.get('SECRET_KEY', 'pipeline-lab-dev')
    app.config['MAX_CONTENT_LENGTH'] = 48 * 1024 * 1024

    upload_root = _lab_dir / 'uploads'
    upload_root.mkdir(parents=True, exist_ok=True)
    app.config['UPLOAD_FOLDER'] = str(upload_root)

    app.register_blueprint(lab_bp, url_prefix='/pipeline-lab')

    @app.route('/pipeline-lab')
    def _pipeline_lab_trailing_slash():
        """末尾スラッシュなしでも開けるようにする（相対パス fetch の事故防止にもなる）"""
        return redirect(url_for('pipeline_lab.index'))

    @app.route('/')
    def _root_redirect():
        """単体起動時に http://host:port/ へ来ても 404 にしない"""
        return redirect(url_for('pipeline_lab.index'))

    return app


app = create_app()


def _open_default_browser_later(url: str, delay_s: float = 0.4) -> None:
    """Flask が bind するまで短く待ってから開く（Failed to fetch の体感を減らす）。"""

    def _run() -> None:
        import time

        time.sleep(delay_s)
        webbrowser.open(url)

    threading.Thread(target=_run, daemon=True).start()


if __name__ == '__main__':
    port = int(os.environ.get('PORT', '5055'))
    url = f'http://127.0.0.1:{port}/pipeline-lab/'
    no_browser = os.environ.get('PIPELINE_LAB_NO_BROWSER', '').strip().lower() in ('1', 'true', 'yes')
    # use_reloader=True: .py 変更を検出して自動再起動。
    # WERKZEUG_RUN_MAIN が未設定（親プロセス）のときだけブラウザを開く。
    if not no_browser and not os.environ.get('WERKZEUG_RUN_MAIN'):
        _open_default_browser_later(url)
    print(f'pipeline-lab → {url}', flush=True)
    app.run(debug=True, host='0.0.0.0', port=port, use_reloader=True)
