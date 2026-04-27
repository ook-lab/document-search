import os
import re
import logging
import uuid
import fitz  # PyMuPDF
from flask import Blueprint, render_template, request, jsonify, send_file, current_app
from werkzeug.utils import secure_filename
from google import genai
from google.genai import types

embedder_bp = Blueprint('embedder', __name__, template_folder='../templates')

# Gemini Client (lazy init)
_client = None

def get_client():
    global _client
    if _client is None:
        _client = genai.Client(vertexai=True, location=os.environ.get("VERTEX_AI_REGION", "us-central1"))
    return _client

MARKER_START = "<<<MD_SANDWICH_START>>>"
MARKER_END = "<<<MD_SANDWICH_END>>>"

@embedder_bp.route('/')
def index():
    return render_template('md_embedder.html')

@embedder_bp.route('/upload', methods=['POST'])
def upload_pdf():
    if 'pdf_file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    
    file = request.files['pdf_file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400

    if file and file.filename.lower().endswith('.pdf'):
        filename = secure_filename(file.filename)
        file_id = str(uuid.uuid4())[:8]
        safe_filename = f"{file_id}_{filename}"
        upload_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], 'embedder')
        os.makedirs(upload_dir, exist_ok=True)
        input_filepath = os.path.join(upload_dir, safe_filename)
        file.save(input_filepath)

        try:
            doc = fitz.open(input_filepath)
            if len(doc) == 0:
                return jsonify({'error': 'PDF is empty'}), 400

            pages_info = []

            for i in range(len(doc)):
                page = doc[i]
                
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                img_name = f"{file_id}_page_{i}.png"
                img_path = os.path.join(upload_dir, img_name)
                pix.save(img_path)
                
                text = page.get_text()
                existing_data = None
                
                if MARKER_START in text and MARKER_END in text:
                    try:
                        start_idx = text.find(MARKER_START) + len(MARKER_START)
                        end_idx = text.find(MARKER_END)
                        md_str = text[start_idx:end_idx]
                        existing_data = {"markdown": md_str.strip()}
                    except Exception as e:
                        logging.warning(f"Found markers on page {i} but failed to parse MD: {e}")
                else:
                    try:
                        old_start = "<<<JSON_SANDWICH_START>>>"
                        old_end = "<<<JSON_SANDWICH_END>>>"
                        if old_start in text and old_end in text:
                            s_idx = text.find(old_start) + len(old_start)
                            e_idx = text.find(old_end)
                            existing_data = {"markdown": "```json\n" + text[s_idx:e_idx] + "\n```"}
                    except:
                        pass
                
                pages_info.append({
                    'page_index': i,
                    'image_url': f"/embedder/static_uploads/{file_id}_page_{i}.png",
                    'existing_data': existing_data
                })
            
            doc.close()

            return jsonify({
                'success': True,
                'file_id': file_id,
                'filename': filename,
                'safe_filename': safe_filename,
                'pages': pages_info
            })

        except Exception as e:
            logging.error(f"Error processing upload: {e}")
            return jsonify({'error': str(e)}), 500

    return jsonify({'error': 'Invalid file type'}), 400

@embedder_bp.route('/static_uploads/<filename>')
def serve_upload(filename):
    upload_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], 'embedder')
    return send_file(os.path.join(upload_dir, filename))

@embedder_bp.route('/extract_page/<file_id>/<int:page_index>', methods=['POST'])
def extract_page(file_id, page_index):
    try:
        data = request.json or {}
        custom_instructions = data.get('custom_prompt', '').strip()

        upload_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], 'embedder')
        img_name = f"{file_id}_page_{page_index}.png"
        img_path = os.path.join(upload_dir, img_name)
        
        if not os.path.exists(img_path):
            return jsonify({'error': 'Image for this page not found on server'}), 404

        client = get_client()
        uploaded_file = client.files.upload(file=img_path)

        prompt = """
        You are an expert OCR and data extraction system.
        Extract data from this scanned document (e.g. invoice, estimate, receipt).
        Return the extracted data as clean, well-structured Markdown.
        For key-value pairs (like metadata, dates, addresses), use bullet points or bold text.
        For tabular data, use Markdown tables.
        CRITICAL: Do not translate any Japanese text to English. Keep the original text intact.
        CRITICAL: If Japanese words have wide kerning/spaces between characters (e.g., "摘    要"), you MUST join them together as a single word (e.g., "摘要").
        """
        
        if custom_instructions:
            prompt += f"\n\nADDITIONAL USER INSTRUCTIONS (CRITICAL PRIORITY):\n{custom_instructions}\nEnsure you strictly follow the above additional instructions regarding formatting or layout."

        response = client.models.generate_content(
            model='gemini-2.5-flash-lite',
            contents=[uploaded_file, prompt],
            config=types.GenerateContentConfig(
                response_mime_type="text/plain",
            ),
        )
        
        text_result = response.text
        client.files.delete(name=uploaded_file.name)
        
        match = re.search(r'```(?:markdown|md)?\n(.*?)```', text_result, re.DOTALL)
        if match:
            text_result = match.group(1).strip()
        else:
            text_result = text_result.strip()

        return jsonify({
            'success': True,
            'json_extracted': {"markdown": text_result}
        })

    except Exception as e:
        logging.error(f"Error extracting page AI: {e}")
        return jsonify({'error': str(e)}), 500

@embedder_bp.route('/save_pdf', methods=['POST'])
def save_pdf():
    try:
        data = request.json
        file_id = data.get('file_id')
        safe_filename = data.get('safe_filename')
        user_filename = data.get('filename')
        pages_data = data.get('pages_data', {})

        upload_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], 'embedder')
        input_filepath = os.path.join(upload_dir, safe_filename)
        
        if user_filename:
            if not user_filename.lower().endswith('.pdf'):
                user_filename += '.pdf'
            output_filename = secure_filename(user_filename)
        else:
            output_filename = f"embedded_{safe_filename}"
            
        output_filepath = os.path.join(current_app.config['OUTPUT_FOLDER'], output_filename)

        if not os.path.exists(input_filepath):
            return jsonify({'error': 'Original PDF not found on server'}), 404

        doc = fitz.open(input_filepath)

        for str_idx, page_data in pages_data.items():
            page_idx = int(str_idx)
            if page_idx < len(doc):
                page = doc[page_idx]
                
                md_content = page_data.get('markdown', '')
                payload = f"{MARKER_START}\n{md_content}\n{MARKER_END}"
                
                rect = fitz.Rect(0, 0, page.rect.width, page.rect.height) 
                page.insert_textbox(rect, payload, fontsize=6, fontname="cjk", render_mode=3)

        doc.save(output_filepath)
        doc.close()

        # Additionally save an MD file
        md_filename = output_filename.replace('.pdf', '.md')
        md_filepath = os.path.join(current_app.config['OUTPUT_FOLDER'], md_filename)
        with open(md_filepath, 'w', encoding='utf-8') as f:
            for str_idx, page_data in sorted(pages_data.items(), key=lambda x: int(x[0])):
                f.write(f"## Page {int(str_idx) + 1}\n\n")
                f.write(page_data.get('markdown', '') + "\n\n")

        return jsonify({
            'success': True,
            'download_url': f'/embedder/download/{output_filename}',
            'download_md_url': f'/embedder/download/{md_filename}',
            'filename': output_filename,
            'md_filename': md_filename
        })

    except Exception as e:
        logging.error(f"Error saving PDF: {e}")
        return jsonify({'error': str(e)}), 500

@embedder_bp.route('/download/<filename>')
def download_file(filename):
    filepath = os.path.join(current_app.config['OUTPUT_FOLDER'], filename)
    if os.path.exists(filepath):
        return send_file(filepath, as_attachment=True)
    return "File not found", 404

@embedder_bp.route('/generate_filename', methods=['POST'])
def generate_filename():
    data = request.json
    text = data.get('text', '')
    
    if not text:
        return jsonify({"error": "No text provided"}), 400
        
    try:
        prompt = (
            "以下のデータから、この文書を表す簡潔なファイル名を生成してください。\n"
            "形式の指定: 「文書の内容（社名など）_日付」の形式で生成してください。（例: 見積書_株式会社ABC_20231005）\n"
            "日付が見つからない場合は、日付部分は省略可能です。\n"
            "拡張子(.pdf)は含めないでください。\n"
            "余計な説明は一切せず、ファイル名となる文字列のみを出力してください。\n\n"
            f"データ:\n{text[:3000]}"
        )
        
        client = get_client()
        response = client.models.generate_content(
            model='gemini-2.5-flash-lite',
            contents=prompt
        )
        filename = response.text.strip().replace(' ', '_').replace('/', '').replace('\\', '')
        
        return jsonify({"filename": filename})
    except Exception as e:
        logging.error(f"Error generating filename: {e}")
        return jsonify({"error": str(e)}), 500
