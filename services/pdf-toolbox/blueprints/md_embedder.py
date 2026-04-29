import os
import re
import uuid
import logging
import base64
import requests
from datetime import timezone, datetime
import fitz  # PyMuPDF
from flask import Blueprint, request, jsonify, current_app, send_file, render_template, url_for
from google import genai
from google.genai import types
from werkzeug.utils import secure_filename
from shared.common.connectors.google_drive import GoogleDriveConnector
from supabase import create_client as _supabase_create_client

def _get_supabase():
    url = os.environ.get('SUPABASE_URL')
    key = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ.get('SUPABASE_KEY')
    if url and key:
        return _supabase_create_client(url, key)
    return None

embedder_bp = Blueprint('embedder', __name__, template_folder='../templates')

MARKER_START = "<<<MD_SANDWICH_START>>>"
MARKER_END = "<<<MD_SANDWICH_END>>>"

# Gemini Client (lazy init)
def get_client():
    # MANDATORY: Only direct AI Studio API allowed. Fallback is PROHIBITED.
    return "AI_STUDIO_DIRECT"

@embedder_bp.route('/')
def index():
    return render_template('md_embedder.html')

@embedder_bp.route('/load_from_drive', methods=['POST'])
def load_from_drive():
    try:
        data = request.json
        drive_file_id = data.get('drive_file_id')
        if not drive_file_id:
            return jsonify({'error': 'No drive_file_id provided'}), 400
            
        drive = GoogleDriveConnector()
        # Get metadata to get the name
        file_meta = drive.service.files().get(fileId=drive_file_id, fields='name', supportsAllDrives=True).execute()
        original_name = file_meta.get('name', 'drive_file.pdf')
        
        file_id = str(uuid.uuid4())
        upload_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], 'embedder')
        os.makedirs(upload_dir, exist_ok=True)
        
        # Download from drive to local server
        local_path = drive.download_file(drive_file_id, original_name, upload_dir)
        # Rename to include uuid
        new_name = f"{file_id}_{secure_filename(original_name)}"
        new_path = os.path.join(upload_dir, new_name)
        os.rename(local_path, new_path)

        # Generate previews
        previews = []
        doc = fitz.open(new_path)
        preview_dir = os.path.join(current_app.config['STATIC_FOLDER'], 'previews', file_id)
        os.makedirs(preview_dir, exist_ok=True)
        
        for i in range(len(doc)):
            page = doc[i]
            pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5))
            img_name = f"page_{i}.png"
            pix.save(os.path.join(preview_dir, img_name))
            previews.append({
                "index": i,
                "url": url_for('static', filename=f'previews/{file_id}/{img_name}'),
                "width": pix.width,
                "height": pix.height
            })
        doc.close()

        return jsonify({
            'file_id': file_id,
            'safe_filename': original_name,
            'previews': previews,
            'drive_file_id': drive_file_id # Return it so frontend can store it
        })
    except Exception as e:
        logging.error(f"Error loading from drive: {e}")
        return jsonify({'error': str(e)}), 500

@embedder_bp.route('/list_drive_files', methods=['POST'])
def list_drive_files():
    try:
        data = request.json
        folder_id = data.get('folder_id', 'root')
        
        drive = GoogleDriveConnector()
        query = f"'{folder_id}' in parents and trashed=false"
        results = drive.service.files().list(
            q=query,
            fields='files(id, name, mimeType, size)',
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
            corpora='allDrives'
        ).execute()
        
        files = results.get('files', [])
        # Sort folders first, then by name
        files.sort(key=lambda x: (x['mimeType'] != 'application/vnd.google-apps.folder', x['name'].lower()))
        
        return jsonify({'files': files})
    except Exception as e:
        logging.error(f"Error listing drive files: {e}")
        return jsonify({'error': str(e)}), 500

@embedder_bp.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        if 'pdf_file' in request.files:
            file = request.files['pdf_file']
        else:
            return jsonify({'error': 'No file part'}), 400
    else:
        file = request.files['file']

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
        selected_model = data.get('model', os.environ.get("STAGE1_MODEL", "gemini-2.5-flash-lite"))

        upload_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], 'embedder')
        img_name = f"{file_id}_page_{page_index}.png"
        img_path = os.path.join(upload_dir, img_name)
        
        if not os.path.exists(img_path):
            return jsonify({'error': 'Image for this page not found on server'}), 404

        client = get_client()
        with open(img_path, "rb") as f:
            img_bytes = f.read()
        
        prompt = """
        あなたは高度なOCRおよびデータ抽出システムです。
        提供された画像からすべての情報を抽出し、マークダウン形式で返してください。
        「一列のズレ、一行の結合も許さない」という極めて厳格な姿勢で臨んでください。

        【思考プロセス（重要）】
        正確な抽出のために、以下の手順を厳守してください。

        1. **垂直境界（列のコンテナ）の定義**: 
           まずヘッダー行を精査し、各列の水平方向の開始位置と終了位置を確定させてください。これを「列のコンテナ」と呼びます。
        2. **座標ベースの列割り当て**: 
           すべてのテキストブロックについて、その水平方向の中心座標を計算し、それがどの「列のコンテナ」に属するかを物理的に判定してください。
           **重要：データがない列を飛ばして左に詰めることは「データ改ざん」とみなし、絶対に禁止します。**
        3. **垂直スキャンによる検算**: 
           各列のヘッダーから下方向へ垂直に視線を走らせ、その「コンテナ」の中にデータが正しく縦一列に並んでいるかを確認してください。
        4. **行の解体（結合セルの排除）**: 
           画像上で上下に並んでいるデータは、一つのセルにまとめず、必ず**独立した複数の行**として書き出してください。結合セルによって省略されている情報は、すべての行に繰り返し入力してください。

        【抽出ルール】
        0. **ドキュメントタイトル**: 画像の最上部にある文書名やタイトルを特定し、必ずMarkdownの最初に `# ` (H1) ヘッダーとして記述してください。
        1. **1データ1行の徹底**: セル内で改行して複数のクラスや時間を詰め込まないでください。行を分けて出力してください。
        2. **空セルの厳格維持**: データが存在しない列は、必ず `| |` （半角スペース一つ）を入れて列のカウントを維持してください。
        3. **結合セルの完全展開**: 縦または横に結合されたセル（学年、校舎など）は、その範囲に含まれる**すべての行・列にその値をコピー**して出力してください。
        4. **周辺テキストの抽出**: 表の外にある注釈、ヘッダー、フッター、地文なども漏らさず適切にMarkdown（##, ###, または通常の段落）として構成してください。
        5. **出力形式**: 構造化されたMarkdown形式を出力し、AI自身の説明（「抽出しました」等）は一切省いてください。
        6. **言語**: 日本語のまま抽出してください。
        """
        
        if custom_instructions:
            prompt += f"\n\n【追加のユーザー指示】\n{custom_instructions}\n上記指示に従ってフォーマットを調整してください。"

        # Direct REST API call to Google AI Studio ONLY. NO FALLBACK.
        api_key = "AIzaSyDiVwSXMSzwtCI02lhIbkw6_04LleMvz2Q"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{selected_model}:generateContent?key={api_key}"
        
        # Prepare Base64 image
        img_base64 = base64.b64encode(img_bytes).decode('utf-8')
        
        payload = {
            "contents": [{
                "parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": "image/png", "data": img_base64}}
                ]
            }]
        }
        
        resp = requests.post(url, json=payload)
        if resp.status_code != 200:
            raise Exception(f"Gemini API Error ({resp.status_code}): {resp.text}")
        
        res_json = resp.json()
        text_result = res_json['candidates'][0]['content']['parts'][0]['text']
        
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
        import traceback
        print("Detailed Embedder Error Traceback:")
        traceback.print_exc()
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

        # Register Japanese Font to avoid "need font file or buffer"
        font_path = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
        if not os.path.exists(font_path):
            # Fallback if the path is slightly different
            font_path = "/usr/share/fonts/opentype/noto/NotoSansCJK-jp-Regular.otf"
        
        has_font = os.path.exists(font_path)

        for str_idx, page_data in pages_data.items():
            page_idx = int(str_idx)
            if page_idx < len(doc):
                page = doc[page_idx]
                
                md_content = page_data.get('markdown', '')
                payload = f"{MARKER_START}\n{md_content}\n{MARKER_END}"
                
                rect = fitz.Rect(0, 0, page.rect.width, page.rect.height)
                
                if has_font:
                    # Insert with explicit font to support Japanese
                    page.insert_textbox(rect, payload, fontsize=6, fontname="noto", fontfile=font_path, render_mode=3)
                else:
                    # Last resort fallback
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

@embedder_bp.route('/save_to_drive', methods=['POST'])
def save_to_drive():
    try:
        data = request.json
        drive_file_id = data.get('drive_file_id')
        filename = data.get('filename')  # The generated file on server

        if not drive_file_id or not filename:
            return jsonify({'error': 'Missing drive_file_id or filename'}), 400

        output_filepath = os.path.join(current_app.config['OUTPUT_FOLDER'], filename)
        if not os.path.exists(output_filepath):
            return jsonify({'error': 'Generated file not found on server'}), 404

        drive = GoogleDriveConnector()
        success = drive.update_file_content(drive_file_id, output_filepath)

        if not success:
            return jsonify({'error': 'Failed to update Google Drive file'}), 500

        # ===== Supabase 更新: テキスト埋め込み済みフラグを記録 =====
        now_iso = datetime.now(timezone.utc).isoformat()
        sb_updated = {}
        try:
            sb = _get_supabase()
            if sb:
                # 1) pipeline_meta: drive_file_id カラムで直接検索、なければ raw_table 経由
                pm_res = sb.table('pipeline_meta') \
                    .update({
                        'text_embedded': True,
                        'text_embedded_at': now_iso,
                        'drive_file_id': drive_file_id
                    }) \
                    .eq('drive_file_id', drive_file_id) \
                    .execute()
                sb_updated['pipeline_meta_by_id'] = len(pm_res.data or [])

                # 2) 09_unified_documents: file_url に drive_file_id が含まれるレコードを更新
                ud_res = sb.table('09_unified_documents') \
                    .update({
                        'text_embedded': True,
                        'text_embedded_at': now_iso
                    }) \
                    .ilike('file_url', f'%{drive_file_id}%') \
                    .execute()
                sb_updated['unified_documents'] = len(ud_res.data or [])

                # 3) raw テーブル経由で pipeline_meta を追加更新（file_url 一致）
                for raw_table in ['05_ikuya_waseaca_01_raw', '03_ema_classroom_01_raw',
                                  '04_ikuya_classroom_01_raw', '08_file_only_01_raw']:
                    try:
                        raw_res = sb.table(raw_table) \
                            .select('id') \
                            .ilike('file_url', f'%{drive_file_id}%') \
                            .execute()
                        raw_ids = [r['id'] for r in (raw_res.data or [])]
                        if raw_ids:
                            pm_res2 = sb.table('pipeline_meta') \
                                .update({
                                    'text_embedded': True,
                                    'text_embedded_at': now_iso,
                                    'drive_file_id': drive_file_id
                                }) \
                                .in_('raw_id', raw_ids) \
                                .eq('raw_table', raw_table) \
                                .execute()
                            sb_updated[raw_table] = len(pm_res2.data or [])
                    except Exception as te:
                        logging.warning(f"raw table {raw_table} update skipped: {te}")

        except Exception as se:
            logging.warning(f"Supabase update skipped (non-fatal): {se}")

        logging.info(f"Drive save complete. file_id={drive_file_id}, supabase={sb_updated}")
        return jsonify({
            'success': True,
            'message': f'Drive ファイル {drive_file_id} を更新しました',
            'supabase_updated': sb_updated
        })

    except Exception as e:
        logging.error(f"Error saving to drive: {e}")
        return jsonify({'error': str(e)}), 500
