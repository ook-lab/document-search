import os
import logging
from flask import Flask
from dotenv import load_dotenv

# Load environment variables（サービス直下 → リポジトリルートの順）
_service_dir = os.path.dirname(os.path.abspath(__file__))
_repo_root = os.path.dirname(os.path.dirname(_service_dir))
load_dotenv(os.path.join(_service_dir, ".env"))
load_dotenv(os.path.join(_repo_root, ".env"))

logging.basicConfig(level=logging.INFO)

def create_app():
    app = Flask(__name__, static_folder='static', template_folder='templates')
    app.secret_key = os.environ.get("SECRET_KEY", "pdf-toolbox-secret-key")
    app.config['MAX_CONTENT_LENGTH'] = 32 * 1024 * 1024  # 32MB max upload

    # Shared upload/output folders
    base_dir = os.path.dirname(os.path.abspath(__file__))
    app.config['UPLOAD_FOLDER'] = os.path.join(base_dir, 'uploads')
    app.config['OUTPUT_FOLDER'] = os.path.join(base_dir, 'outputs')
    app.config['STATIC_FOLDER'] = os.path.join(base_dir, 'static')

    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    os.makedirs(app.config['OUTPUT_FOLDER'], exist_ok=True)

    # Register Blueprints
    from blueprints.ocr_editor import ocr_bp
    from blueprints.md_embedder import embedder_bp
    from blueprints.pdf_splitter import splitter_bp
    from blueprints.pdf_optimizer import optimizer_bp
    from blueprints.shell import shell_bp

    app.register_blueprint(shell_bp)
    app.register_blueprint(ocr_bp, url_prefix='/ocr')
    app.register_blueprint(embedder_bp, url_prefix='/embedder')
    app.register_blueprint(splitter_bp, url_prefix='/splitter')
    app.register_blueprint(optimizer_bp, url_prefix='/optimizer')

    return app

app = create_app()

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5050))
    app.run(debug=True, host='0.0.0.0', port=port)
