import os
import io
from flask import Blueprint, render_template, request, send_file, flash, redirect, url_for
from werkzeug.utils import secure_filename
from pypdf import PdfReader, PdfWriter

splitter_bp = Blueprint('splitter', __name__, template_folder='../templates')

def split_pdf_left_right(input_stream, order='ltor'):
    input_stream.seek(0)
    data = input_stream.read()
    reader_left = PdfReader(io.BytesIO(data))
    reader_right = PdfReader(io.BytesIO(data))
    writer = PdfWriter()
    for i in range(len(reader_left.pages)):
        page_l = reader_left.pages[i]
        page_r = reader_right.pages[i]
        width = float(page_l.mediabox.width)
        half_x = float(page_l.mediabox.left) + (width / 2.0)
        page_l.mediabox.right = half_x
        page_l.cropbox.right = half_x
        page_r.mediabox.left = half_x
        page_r.cropbox.left = half_x
        if order == 'ltor':
            writer.add_page(page_l)
            writer.add_page(page_r)
        else:
            writer.add_page(page_r)
            writer.add_page(page_l)
    out_stream = io.BytesIO()
    writer.write(out_stream)
    out_stream.seek(0)
    return out_stream

@splitter_bp.route('/')
def index():
    return render_template('pdf_splitter.html')

@splitter_bp.route('/split', methods=['POST'])
def split_pdf():
    if "pdf_file" not in request.files:
        flash("ファイルが見つかりません。")
        return redirect(url_for('splitter.index'))
    file = request.files["pdf_file"]
    if file.filename == "":
        flash("ファイルが選択されていません。")
        return redirect(url_for('splitter.index'))
    if not file.filename.lower().endswith(".pdf"):
        flash("PDFファイルのみ対応しています。")
        return redirect(url_for('splitter.index'))
    order = request.form.get("order", "ltor")
    try:
        out_stream = split_pdf_left_right(file.stream, order=order)
        original_base = os.path.splitext(file.filename)[0]
        new_filename = f"{original_base}_split.pdf"
        return send_file(out_stream, as_attachment=True, download_name=new_filename, mimetype="application/pdf")
    except Exception as e:
        print(f"Error processing PDF: {e}")
        flash("PDFの処理中にエラーが発生しました。")
        return redirect(url_for('splitter.index'))
