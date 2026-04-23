import io
import os
import sys
import logging
import traceback
from flask import Flask, render_template, request, send_file, jsonify

if sys.platform == 'win32':
    GTK_PATHS = [r"C:\Program Files\GTK3-Runtime-Win64\bin", r"C:\Program Files (x86)\GTK3-Runtime-Win64\bin"]
    for path in GTK_PATHS:
        if os.path.exists(path):
            os.add_dll_directory(path)
            os.environ['PATH'] = path + os.pathsep + os.environ['PATH']
            break

try:
    from weasyprint import HTML, CSS
    from weasyprint.text.fonts import FontConfiguration
except ImportError:
    HTML = None

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# [究極の調律：プロフェッショナル・フィッティング]
# 単なる均等配分ではなく、要素間の「呼吸」を制御してページを満たします。
PROFESSIONAL_FIT_CSS = """
@page {
    size: A4;
    margin: 0;
}

@media print {
    html, body {
        margin: 0 !important;
        padding: 0 !important;
        background: #fff !important;
        display: block !important;
    }
    .page {
        display: flex !important;
        flex-direction: column !important;
        /* justify-content: space-between は「散らばる」ので廃止 */
        justify-content: flex-start !important; 
        
        height: 297mm !important;
        width: 210mm !important;
        overflow: hidden !important; 
        
        margin: 0 !important;
        padding: 15mm 20mm !important;
        background: #fff !important;
        break-after: page !important;
        box-sizing: border-box !important;
    }

    /* 各セクション（見出しなど）の上に、伸びる余白を挿入 */
    .page > .cv-section-title, 
    .page > table,
    .page > .achievement-block {
        margin-top: auto !important; /* これにより、要素がページ全体に「プロの比率」で分散されます */
    }

    /* 最初の要素の上には余白を入れない */
    .page > *:first-child {
        margin-top: 0 !important;
    }

    /* 職務要約などのテキストエリアを、ページに合わせてわずかに広げる */
    .cv-text {
        line-height: 1.6 !important;
        margin-bottom: 0 !important;
    }

    /* 最後のページ（情報が少ないページ）は無理に広げず、自然に配置 */
    .page:last-child {
        justify-content: flex-start !important;
    }
    .page:last-child > * {
        margin-top: 15px !important;
    }
    .page:last-child > *:first-child {
        margin-top: 0 !important;
    }

    /* 写真枠の絶対死守 */
    .photo-box {
        width: 30mm !important;
        height: 40mm !important;
        min-width: 30mm !important;
        min-height: 40mm !important;
    }
}
"""

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/convert", methods=["POST"])
def convert():
    try:
        if HTML is None:
            return jsonify({"error": "Engine missing"}), 500

        data = request.get_json(force=True)
        html_content = data.get("html", "").strip()
        
        if not html_content:
            return jsonify({"error": "Content empty"}), 400

        font_config = FontConfiguration()
        html = HTML(string=html_content, base_url=request.url_root, encoding="utf-8")
        
        pdf_bytes = html.write_pdf(
            stylesheets=[CSS(string=PROFESSIONAL_FIT_CSS)],
            font_config=font_config,
            presentational_hints=True
        )

        pdf_io = io.BytesIO(pdf_bytes)
        pdf_io.seek(0)
        return send_file(pdf_io, mimetype="application/pdf", as_attachment=True, download_name="resume_professional_fit.pdf")

    except Exception as e:
        logger.error(f"Error:\n{traceback.format_exc()}")
        return jsonify({"error": "Conversion Failed", "details": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5055)
