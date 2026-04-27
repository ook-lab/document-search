import os
import uuid
import logging
import fitz
from flask import Blueprint, render_template, request, jsonify, send_file, current_app

optimizer_bp = Blueprint('optimizer', __name__, template_folder='../templates')

@optimizer_bp.route('/')
def index():
    return render_template('pdf_optimizer.html')

@optimizer_bp.route('/upload', methods=['POST'])
def upload():
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
    file_id = str(uuid.uuid4())
    filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], f"{file_id}.pdf")
    file.save(filepath)
    return jsonify({'file_id': file_id})

@optimizer_bp.route('/files/<file_id>')
def serve_file(file_id):
    filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], f"{file_id}.pdf")
    if os.path.exists(filepath):
        return send_file(filepath)
    return "Not found", 404

@optimizer_bp.route('/analyze/<file_id>')
def analyze_pdf(file_id):
    filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], f"{file_id}.pdf")
    if not os.path.exists(filepath):
        return jsonify({'error': 'File not found'}), 404
    try:
        doc = fitz.open(filepath)
        meta = doc.metadata
        pages_data = {}
        for page_num in range(len(doc)):
            page = doc[page_num]
            blocks = page.get_text("dict").get("blocks", [])
            for block in blocks:
                if 'lines' not in block:
                    continue
                for line in block['lines']:
                    for span in line['spans']:
                        bbox = span['bbox']
                        if bbox[0] < 60 and bbox[1] < 50:
                            text = span['text'].strip()
                            if text:
                                pages_data[str(page_num)] = text
                                break
                    else:
                        continue
                    break
        doc.close()
        return jsonify({'metadata': meta, 'pages': pages_data})
    except Exception as e:
        current_app.logger.error(f"Error analyzing PDF: {e}")
        return jsonify({'error': str(e)}), 500

@optimizer_bp.route('/process/<file_id>', methods=['POST'])
def process_pdf(file_id):
    data = request.json
    filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], f"{file_id}.pdf")
    if not os.path.exists(filepath):
        return jsonify({'error': 'File not found'}), 404
    out_filepath = os.path.join(current_app.config['OUTPUT_FOLDER'], f"{file_id}_optimized.pdf")
    try:
        doc = fitz.open(filepath)
        meta = doc.metadata
        if 'metadata' in data:
            new_meta = data['metadata']
            if new_meta.get('title'): meta['title'] = new_meta['title']
            if new_meta.get('author'): meta['author'] = new_meta['author']
            if new_meta.get('subject'): meta['subject'] = new_meta['subject']
            doc.set_metadata(meta)
        pages_data = data.get('pages', {})
        cjk_font = fitz.Font('cjk')
        for page_num_str, label_text in pages_data.items():
            page_num = int(page_num_str)
            if 0 <= page_num < len(doc):
                page = doc[page_num]
                page.insert_font(fontname="cjk", fontbuffer=cjk_font.buffer)
                visual_rect = fitz.Rect(10, 10, 500, 40)
                physical_rect = visual_rect * page.derotation_matrix
                page.insert_textbox(physical_rect, label_text, fontsize=8, fontname="cjk",
                                    color=(1, 1, 1), render_mode=3, rotate=page.rotation,
                                    align=fitz.TEXT_ALIGN_LEFT)
        doc.save(out_filepath)
        doc.close()
        return jsonify({'download_url': f"/optimizer/download/{file_id}"})
    except Exception as e:
        current_app.logger.error(f"Error processing PDF: {e}")
        return jsonify({'error': str(e)}), 500

@optimizer_bp.route('/download/<file_id>')
def download(file_id):
    out_filepath = os.path.join(current_app.config['OUTPUT_FOLDER'], f"{file_id}_optimized.pdf")
    if os.path.exists(out_filepath):
        return send_file(out_filepath, as_attachment=True, download_name="optimized.pdf")
    return "Not found", 404
