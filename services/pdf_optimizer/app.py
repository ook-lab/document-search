import os
import uuid
import logging
from flask import Flask, render_template, request, jsonify, send_file
import fitz  # PyMuPDF

app = Flask(__name__)
app.logger.setLevel(logging.INFO)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
OUTPUT_FOLDER = os.path.join(BASE_DIR, 'output')

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload():
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
    
    file_id = str(uuid.uuid4())
    filepath = os.path.join(UPLOAD_FOLDER, f"{file_id}.pdf")
    file.save(filepath)
    
    return jsonify({'file_id': file_id})

@app.route('/files/<file_id>')
def serve_file(file_id):
    filepath = os.path.join(UPLOAD_FOLDER, f"{file_id}.pdf")
    if os.path.exists(filepath):
        return send_file(filepath)
    return "Not found", 404

@app.route('/analyze/<file_id>')
def analyze_pdf(file_id):
    filepath = os.path.join(UPLOAD_FOLDER, f"{file_id}.pdf")
    if not os.path.exists(filepath):
        return jsonify({'error': 'File not found'}), 404
        
    try:
        doc = fitz.open(filepath)
        meta = doc.metadata
        
        pages_data = {}
        for page_num in range(len(doc)):
            page = doc[page_num]
            # Get text from the page
            blocks = page.get_text("dict").get("blocks", [])
            for block in blocks:
                if 'lines' not in block:
                    continue
                for line in block['lines']:
                    for span in line['spans']:
                        bbox = span['bbox']
                        # Check if text is located at the top-left area (y0 < 50, x0 < 60)
                        if bbox[0] < 60 and bbox[1] < 50:
                            text = span['text'].strip()
                            if text:
                                pages_data[str(page_num)] = text
                                break # Assume the first matching span is our label
                    else:
                        continue
                    break # Break out of lines loop if label found
                    
        doc.close()
        
        return jsonify({
            'metadata': meta,
            'pages': pages_data
        })
    except Exception as e:
        app.logger.error(f"Error analyzing PDF: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/process/<file_id>', methods=['POST'])
def process_pdf(file_id):
    data = request.json
    filepath = os.path.join(UPLOAD_FOLDER, f"{file_id}.pdf")
    if not os.path.exists(filepath):
        return jsonify({'error': 'File not found'}), 404
        
    out_filepath = os.path.join(OUTPUT_FOLDER, f"{file_id}_optimized.pdf")
    
    try:
        doc = fitz.open(filepath)
        
        # 1. Update Metadata
        meta = doc.metadata
        if 'metadata' in data:
            new_meta = data['metadata']
            if new_meta.get('title'): meta['title'] = new_meta['title']
            if new_meta.get('author'): meta['author'] = new_meta['author']
            if new_meta.get('subject'): meta['subject'] = new_meta['subject']
            doc.set_metadata(meta)
            
        # 2. Insert Invisible Labels
        pages_data = data.get('pages', {}) # e.g. {"0": "Math_1", "1": "Math_2"}
        cjk_font = fitz.Font('cjk')
        
        for page_num_str, label_text in pages_data.items():
            page_num = int(page_num_str)
            if 0 <= page_num < len(doc):
                page = doc[page_num]
                
                page.insert_font(fontname="cjk", fontbuffer=cjk_font.buffer)
                
                # By default we want the text to appear at the visual top-left area.
                # However, if the page is rotated (e.g. 180 degrees), we must map 
                # these visual coordinates back into the unrotated physical space.
                visual_rect = fitz.Rect(10, 10, 500, 40)
                physical_rect = visual_rect * page.derotation_matrix
                
                # Insert transparent text (render_mode=3)
                # By specifying 'rotate=page.rotation', PyMuPDF correctly aligns the text
                # upright from the perspective of someone viewing the rotated page.
                page.insert_textbox(physical_rect, label_text, 
                                    fontsize=8, 
                                    fontname="cjk",
                                    color=(1, 1, 1), 
                                    render_mode=3, 
                                    rotate=page.rotation,
                                    align=fitz.TEXT_ALIGN_LEFT)
                                    
        doc.save(out_filepath)
        doc.close()
        
        return jsonify({'download_url': f"/download/{file_id}"})
    except Exception as e:
        app.logger.error(f"Error processing PDF: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/download/<file_id>')
def download(file_id):
    out_filepath = os.path.join(OUTPUT_FOLDER, f"{file_id}_optimized.pdf")
    if os.path.exists(out_filepath):
        return send_file(out_filepath, as_attachment=True, download_name="optimized.pdf")
    return "Not found", 404

if __name__ == '__main__':
    app.run(debug=True, port=5000)
