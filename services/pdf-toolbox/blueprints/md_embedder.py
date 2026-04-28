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
        # Use Vertex AI as per user's migration goal
        _client = genai.Client(
            vertexai=True, 
            project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
            location=os.environ.get("VERTEX_AI_REGION", "us-central1")
        )
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
        with open(img_path, "rb") as f:
            img_bytes = f.read()
        
        image_part = types.Part.from_bytes(data=img_bytes, mime_type="image/png")

        prompt = """
        あなたは高度なOCRおよびデータ抽出システムです。
        提供された画像（時間割、請求書、見積書、領収書など）からすべての情報を抽出し、クリーンで構造化されたMarkdown形式で返してください。

        【抽出ルール】
        1. **表データ (Tabular Data)**: 文書内に表がある場合は、必ずMarkdownのテーブル形式（|---|）を使用して抽出してください。
        2. **結合セルの処理 (Merging Cells)**: 
           - 縦（rowspan）や横（colspan）に結合されたセルがある場合、その内容を省略せず、対応するすべての論理的なセルに繰り返し入力するか、構造が明確になるように分割して配置してください。
           - 特に、左端の項目（学年や校舎など）が複数の行にまたがっている場合、すべての行にその項目名を補完して、各行が独立したデータとして成立するようにしてください。
        3. **構造の維持**: 表の列数が途中で変わらないように、各行の「|」の数を統一してください。空のセルがある場合は「 」（半角スペース）を入れてください。
        4. **改行の扱い**: セル内で改行が必要な場合は `<br>` タグを使用してください。
        5. **項目 (Key-Value)**: 表以外の項目（タイトル、日付、住所、社名など）は、箇条書きや太字を使用して整理してください。
        6. **言語**: 日本語を英語に翻訳しないでください。元の言語のまま抽出してください。
        7. **文字の修正**: 「摘    要」のような不自然な空白は除き、「摘要」として抽出してください。
        8. **出力形式**: 余計な説明（「はい、抽出しました」など）は一切不要です。Markdownデータのみを出力してください。
        """
        
        if custom_instructions:
            prompt += f"\n\n【追加のユーザー指示】\n{custom_instructions}\n上記指示に従ってフォーマットを調整してください。"

        response = client.models.generate_content(
            model=os.environ.get("STAGE1_MODEL", "gemini-2.5-flash-lite"),
            contents=[image_part, prompt],
            config=types.GenerateContentConfig(
                response_mime_type="text/plain",
            ),
        )
        
        text_result = response.text
        
        # More robust extraction for multiple or varying code blocks
        matches = re.findall(r'```(?:markdown|md)?\s*\n?(.*?)```', text_result, re.DOTALL)
        if matches:
            text_result = "\n\n".join(m.strip() for m in matches)
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
            model=os.environ.get("STAGE1_MODEL", "gemini-2.5-flash-lite"),
            contents=prompt
        )
        filename = response.text.strip().replace(' ', '_').replace('/', '').replace('\\', '')
        
        return jsonify({"filename": filename})
    except Exception as e:
        logging.error(f"Error generating filename: {e}")
        return jsonify({"error": str(e)}), 500
