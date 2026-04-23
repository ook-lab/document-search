import io
import os
import logging

from flask import Flask, render_template, request, send_file, jsonify
from weasyprint import HTML, CSS
from weasyprint.text.fonts import FontConfiguration

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# WeasyPrint が外部フォント（Google Fonts等）を取得できるようにするための設定
font_config = FontConfiguration()

# WeasyPrint 用の補完CSS:
# ユーザーHTMLの @page / print メディアクエリをそのままにしつつ、
# 何も書いていない場合だけ適切なデフォルトを提供する。
WEASYPRINT_BASE_CSS = """
@page {
    size: A4 portrait;
    margin: 15mm 20mm;
}
body {
    margin: 0;
    padding: 0;
    font-family: 'Noto Sans JP', 'Helvetica Neue', Arial, sans-serif;
}
/* 印刷時に非表示にするべき要素 */
.no-print, .toolbar, button, .a4-floating-print-btn {
    display: none !important;
}
"""


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/convert", methods=["POST"])
def convert():
    """
    POSTで受け取ったHTMLをWeasyPrintでPDFに変換してダウンロードさせる。
    JSON形式: { "html": "...", "filename": "output" (optional) }
    """
    try:
        data = request.get_json(force=True)
        html_content = data.get("html", "").strip()
        filename = data.get("filename", "output").strip() or "output"

        if not html_content:
            return jsonify({"error": "HTMLが空です"}), 400

        # HTMLの前処理: <head>がなければ包む
        if "<html" not in html_content.lower():
            html_content = f"""<!DOCTYPE html>
<html lang="ja">
<head><meta charset="UTF-8"></head>
<body>{html_content}</body>
</html>"""

        # WeasyPrint でPDF生成
        # base_url を設定することで、相対パスや外部リソース（Google Fonts等）が取得できる
        pdf_bytes = HTML(
            string=html_content,
            base_url="https://fonts.googleapis.com",  # フォント解決のベースURL
        ).write_pdf(
            stylesheets=[CSS(string=WEASYPRINT_BASE_CSS, font_config=font_config)],
            font_config=font_config,
            # ユーザーの @page / @media print をそのまま優先させる
            # (WEASYPRINT_BASE_CSS は最低限のデフォルトのみ提供)
            presentational_hints=True,
        )

        pdf_io = io.BytesIO(pdf_bytes)
        pdf_io.seek(0)

        safe_filename = "".join(
            c for c in filename if c.isalnum() or c in ("-", "_", ".")
        ) or "output"
        if not safe_filename.endswith(".pdf"):
            safe_filename += ".pdf"

        logger.info(f"PDF generated: {safe_filename} ({len(pdf_bytes):,} bytes)")

        return send_file(
            pdf_io,
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
