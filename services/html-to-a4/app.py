import io
import os
import logging

from flask import Flask, render_template, request, send_file, jsonify
from playwright.sync_api import sync_playwright

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/convert", methods=["POST"])
def convert():
    """
    POSTで受け取ったHTMLをPlaywrightでPDFに変換してダウンロードさせる。
    JSON形式: { "html": "...", "filename": "output" (optional) }
    KaTeX等のJavaScriptも正しくレンダリングされる。
    """
    try:
        data = request.get_json(force=True)
        html_content = data.get("html", "").strip()
        filename = data.get("filename", "output").strip() or "output"

        if not html_content:
            return jsonify({"error": "HTMLが空です"}), 400

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            # JavaScript（KaTeX等）の実行を待つ
            page.set_content(html_content, wait_until="networkidle")
            pdf_bytes = page.pdf(
                format="A4",
                print_background=True,
            )
            browser.close()

        safe_filename = "".join(
            c for c in filename if c.isalnum() or c in ("-", "_", ".")
        ) or "output"
        if not safe_filename.endswith(".pdf"):
            safe_filename += ".pdf"

        logger.info(f"PDF generated: {safe_filename} ({len(pdf_bytes):,} bytes)")

        return send_file(
            io.BytesIO(pdf_bytes),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=safe_filename,
        )

    except Exception as e:
        logger.error(f"PDF generation error: {e}", exc_info=True)
        return jsonify({"error": f"PDF変換に失敗しました: {str(e)}"}), 500


@app.route("/preview-html", methods=["POST"])
def preview_html():
    """
    プレビュー用: HTMLをそのまま返す（iframeのsrcdocと組み合わせて使用）
    """
    data = request.get_json(force=True)
    html_content = data.get("html", "")
    return html_content, 200, {"Content-Type": "text/html; charset=utf-8"}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5051))
    app.run(debug=True, host="0.0.0.0", port=port)
