import os
import json
import logging
import uuid
import re
from flask import Flask, render_template, request, send_file, jsonify
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
import fitz  # PyMuPDF
import vertexai
from google import genai
from google.genai import types

root_env = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), '.env')
load_dotenv(root_env)

app = Flask(__name__, static_folder='static')
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['OUTPUT_FOLDER'] = 'outputs'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['OUTPUT_FOLDER'], exist_ok=True)

logging.basicConfig(level=logging.INFO)

client = genai.Client(vertexai=True, location="asia-northeast1")

MARKER_START = "<<<JSON_SANDWICH_START>>>"
MARKER_END = "<<<JSON_SANDWICH_END>>>"

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
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
        input_filepath = os.path.join(app.config['UPLOAD_FOLDER'], safe_filename)
        file.save(input_filepath)

        try:
            doc = fitz.open(input_filepath)
            if len(doc) == 0:
                return jsonify({'error': 'PDF is empty'}), 400

            pages_info = []

            for i in range(len(doc)):
                page = doc[i]
                
                # Render to PNG
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                img_name = f"{file_id}_page_{i}.png"
                img_path = os.path.join(app.config['UPLOAD_FOLDER'], img_name)
                pix.save(img_path)
                
                # Check for existing embedded JSON
                text = page.get_text()
                existing_json = None
                
                if MARKER_START in text and MARKER_END in text:
                    try:
                        start_idx = text.find(MARKER_START) + len(MARKER_START)
                        end_idx = text.find(MARKER_END)
                        json_str = text[start_idx:end_idx]
                        existing_json = json.loads(json_str)
                    except Exception as e:
                        logging.warning(f"Found markers on page {i} but failed to parse JSON: {e}")
                else:
                    # Fallback check for old format (without markers) if needed
                    # Look for our known structure {"metadata": [...], "table": {...}}
                    try:
                        match = re.search(r'\{"metadata":.*?\}', text, re.DOTALL)
                        if match:
                            parsed = json.loads(match.group(0))
                            if 'metadata' in parsed and 'table' in parsed:
                                existing_json = parsed
                    except:
                        pass
                
                pages_info.append({
                    'page_index': i,
                    'image_url': f"/static/uploads/{img_name}",
                    'existing_data': existing_json
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

@app.route('/extract_page/<file_id>/<int:page_index>', methods=['POST'])
def extract_page(file_id, page_index):
    try:
        data = request.json or {}
        custom_instructions = data.get('custom_prompt', '').strip()

        img_name = f"{file_id}_page_{page_index}.png"
        img_path = os.path.join(app.config['UPLOAD_FOLDER'], img_name)
        
        if not os.path.exists(img_path):
            return jsonify({'error': 'Image for this page not found on server'}), 404

        uploaded_file = client.files.upload(file=img_path)

        prompt = """
        You are an expert OCR and data extraction system.
        Extract data from this scanned document (e.g. invoice, estimate, receipt).
        Return strictly a valid JSON object with the following structure:
        {
           "metadata": [
               // Put any non-tabular fields here. 
               // 'label' MUST be a logical, short Japanese name inferred for this field (e.g., "宛名", "住所", "発行日", "電話番号"). Do NOT duplicate the value into the label.
               // 'tag' MUST be an English identifier (e.g., "address", "issue_date").
               // 'value' MUST be the exact printed text. **SPECIAL RULE**: If the value contains a Japanese era date (e.g., 令和, 平成), you MUST calculate and append the Gregorian year in parentheses, like this: "令和3年10月6日(2021年10月6日)".
           ],
           "table": {
               "headers": [
                   // Define the table columns EXACTLY in the order they appear from left to right on the document.
                   // 'label' must be the exact Japanese text from the document header. 'tag' is an English identifier.
               ],
               "rows": [
                   // Put the tabular data here. Keys must exactly match the 'tag' strings defined in headers.
                   // Values must be exactly as written on the document.
               ]
           }
        }
        Do not include Markdown markup (like ```json), just the raw JSON text.
        If there is no table, table.headers and table.rows can be empty lists.
        CRITICAL: 'label' and 'value' fields MUST NOT be translated to English. Keep the original Japanese text intact.
        CRITICAL: If Japanese words have wide kerning/spaces between characters (e.g., "摘    要", "単    価", "金    額"), you MUST join them together as a single word (e.g., "摘要", "単価", "金額"). Do NOT split them into separate columns or fields.
        """
        
        if custom_instructions:
            prompt += f"\n\nADDITIONAL USER INSTRUCTIONS (CRITICAL PRIORITY):\n{custom_instructions}\nEnsure you strictly follow the above additional instructions regarding column definitions or layout."

        response = client.models.generate_content(
            model='gemini-2.5-flash-lite',
            contents=[uploaded_file, prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            ),
        )
        
        json_result = response.text
        client.files.delete(name=uploaded_file.name)

        try:
            parsed_json = json.loads(json_result)
        except Exception as e:
            try:
                import re
                match = re.search(r'```(?:json)?\n(.*?)\n```', json_result, re.DOTALL)
                if match:
                    parsed_json = json.loads(match.group(1))
                else:
                    raise e
            except Exception as ex:
                return jsonify({'error': 'Gemini returned invalid JSON', 'raw': json_result}), 500
                
        return jsonify({
            'success': True,
            'json_extracted': parsed_json
        })

    except Exception as e:
        logging.error(f"Error extracting page AI: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/save_pdf', methods=['POST'])
def save_pdf():
    try:
        data = request.json
        file_id = data.get('file_id')
        safe_filename = data.get('safe_filename')
        user_filename = data.get('filename')
        pages_data = data.get('pages_data', {}) # Dictionary mapping string(page_index) -> json_data

        input_filepath = os.path.join(app.config['UPLOAD_FOLDER'], safe_filename)
        
        if user_filename:
            if not user_filename.lower().endswith('.pdf'):
                user_filename += '.pdf'
            output_filename = secure_filename(user_filename)
        else:
            output_filename = f"embedded_{safe_filename}"
            
        output_filepath = os.path.join(app.config['OUTPUT_FOLDER'], output_filename)

        if not os.path.exists(input_filepath):
            return jsonify({'error': 'Original PDF not found on server'}), 404

        doc = fitz.open(input_filepath)

        for str_idx, page_json in pages_data.items():
            page_idx = int(str_idx)
            if page_idx < len(doc):
                page = doc[page_idx]
                
                # Format the json and wrap with markers
                formatted_json_str = json.dumps(page_json, ensure_ascii=False)
                payload = f"{MARKER_START}{formatted_json_str}{MARKER_END}"
                
                # For simplicity, we just append it as invisible text at the top left.
                rect = fitz.Rect(0, 0, page.rect.width, page.rect.height) 
                page.insert_textbox(rect, payload, fontsize=6, fontname="cjk", render_mode=3)

        doc.save(output_filepath)
        doc.close()

        return jsonify({
            'success': True,
            'download_url': f'/download/{output_filename}',
            'filename': output_filename
        })

    except Exception as e:
        logging.error(f"Error saving PDF: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/download/<filename>')
def download_file(filename):
    filepath = os.path.join(app.config['OUTPUT_FOLDER'], filename)
    if os.path.exists(filepath):
        return send_file(filepath, as_attachment=True)
    return "File not found", 404

@app.route('/generate_filename', methods=['POST'])
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
        
        response = client.models.generate_content(
            model='gemini-2.5-flash-lite',
            contents=prompt
        )
        filename = response.text.strip().replace(' ', '_').replace('/', '').replace('\\', '')
        
        return jsonify({"filename": filename})
    except Exception as e:
        logging.error(f"Error generating filename: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, host='127.0.0.1', port=5016)
