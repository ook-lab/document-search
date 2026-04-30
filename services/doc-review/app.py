"""
Document Review Service - Flask Application
ドキュメントレビュー + メール受信トレイの統合アプリ

前提:
- 利用者は1人のみ
- Cloud Run (max-instances=1, min-instances=0)
- Redis/外部セッションストアなし
"""
import os
import sys
from pathlib import Path

# .envファイルから環境変数を読み込む（プロジェクトルート）
from dotenv import load_dotenv
project_root_env = Path(__file__).parent.parent.parent / '.env'
load_dotenv(project_root_env)

# プロジェクトルートをパスに追加
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from flask import Flask, session, g, jsonify
from flask_cors import CORS
from flask_wtf.csrf import CSRFProtect, generate_csrf
from loguru import logger

# Blueprintをインポート
from blueprints.api import api_bp
from blueprints.documents import documents_bp
from blueprints.emails import emails_bp
try:
    from blueprints.gmail_cleaner import gmail_cleaner_bp
except Exception as e:
    gmail_cleaner_bp = None
    logger.warning(f"gmail_cleaner blueprint disabled at startup: {e}")


def _secret_key() -> str:
    """本番で FLASK_SECRET_KEY 未設定だとプロセスごとに urandom になりセッション/CSRF が不安定になるため、
    Cloud Run の K_REVISION があればロールアウト単位で安定したキーを生成する。"""
    explicit = (os.environ.get("FLASK_SECRET_KEY") or "").strip()
    if explicit:
        return explicit
    rev = (os.environ.get("K_REVISION") or "").strip()
    svc = (os.environ.get("K_SERVICE") or "doc-review").strip()
    if rev:
        import hashlib

        return hashlib.sha256(f"{svc}:{rev}".encode("utf-8")).hexdigest()
    return os.urandom(32).hex()


def create_app():
    """Flask アプリケーションファクトリ"""
    app = Flask(__name__)

    # 設定
    app.config["SECRET_KEY"] = _secret_key()
    app.config['SESSION_COOKIE_SECURE'] = os.environ.get('FLASK_ENV') == 'production'
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['WTF_CSRF_TIME_LIMIT'] = None  # CSRFトークンの有効期限なし（セッション中有効）
    app.config['WTF_CSRF_CHECK_DEFAULT'] = False  # デフォルトのCSRFチェックを無効化（APIで個別対応）

    # CORS設定
    allowed_origins = os.environ.get('CORS_ORIGINS', '*').split(',')
    has_wildcard = any(o.strip() == '*' for o in allowed_origins)
    # Flask-CORS does not allow supports_credentials=True with wildcard origins.
    CORS(
        app,
        resources={r"/api/*": {"origins": allowed_origins}},
        supports_credentials=not has_wildcard,
    )

    # CSRF保護
    csrf = CSRFProtect(app)

    # Blueprint登録
    app.register_blueprint(api_bp, url_prefix='/api')
    app.register_blueprint(documents_bp, url_prefix='/documents')
    app.register_blueprint(emails_bp, url_prefix='/emails')
    if gmail_cleaner_bp is not None:
        app.register_blueprint(gmail_cleaner_bp)

    # ヘルスチェックは CSRF 対象外（blueprint 登録後の view_functions を明示指定）
    _health_view = app.view_functions.get("api.health")
    if _health_view is not None:
        csrf.exempt(_health_view)
    else:
        logger.error("api.health view not found; CSRF exempt not applied")

    # ルートリダイレクト
    @app.route('/')
    def index():
        from flask import redirect, url_for
        return redirect(url_for('documents.review_page'))

    # CSRFトークンをテンプレートで利用可能にする
    @app.context_processor
    def inject_csrf_token():
        return {'csrf_token': generate_csrf}

    # リクエスト前後の処理
    @app.before_request
    def before_request():
        """リクエスト前処理"""
        # 壊れたクッキー／SECRET_KEY 変更後のセッションで全体が 500 になるのを防ぐ
        try:
            g.user_email = session.get("user_email")
            g.access_token = session.get("access_token")
            g.is_authenticated = g.access_token is not None
        except Exception as e:
            logger.warning(f"session read failed (treating as logged out): {e}")
            g.user_email = None
            g.access_token = None
            g.is_authenticated = False

    @app.after_request
    def after_request(response):
        """レスポンス後処理"""
        # セキュリティヘッダー追加
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        return response

    # エラーハンドラー
    @app.errorhandler(400)
    def bad_request(e):
        return jsonify({
            'error_code': 'BAD_REQUEST',
            'message': str(e.description) if hasattr(e, 'description') else 'Bad request',
            'details': {}
        }), 400

    @app.errorhandler(401)
    def unauthorized(e):
        return jsonify({
            'error_code': 'UNAUTHORIZED',
            'message': 'Authentication required',
            'details': {}
        }), 401

    @app.errorhandler(403)
    def forbidden(e):
        return jsonify({
            'error_code': 'FORBIDDEN',
            'message': 'Access denied',
            'details': {}
        }), 403

    @app.errorhandler(404)
    def not_found(e):
        return jsonify({
            'error_code': 'NOT_FOUND',
            'message': 'Resource not found',
            'details': {}
        }), 404

    @app.errorhandler(500)
    def internal_error(e):
        logger.error(f"Internal error: {e}")
        return jsonify({
            'error_code': 'INTERNAL_ERROR',
            'message': 'Internal server error',
            'details': {}
        }), 500

    logger.info("Document Review Service initialized")
    return app


# アプリケーションインスタンス
app = create_app()


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5002))
    debug = os.environ.get('FLASK_ENV') != 'production'
    app.run(host='0.0.0.0', port=port, debug=debug)
