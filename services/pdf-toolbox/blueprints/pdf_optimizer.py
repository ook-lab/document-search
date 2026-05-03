import os
import uuid
import logging
import textwrap
from urllib.parse import quote

import fitz
from flask import Blueprint, render_template, request, jsonify, send_file, current_app

from gemini_studio_key import google_ai_studio_api_key
from blueprints.drive_pdf import download_drive_pdf
from blueprints.gemini_http import post_generate_content
from blueprints.gemini_images import to_gemini_inline_image_part

optimizer_bp = Blueprint('optimizer', __name__, template_folder='../templates')

TOC_MODEL = os.environ.get("TOC_MODEL", "gemini-2.5-flash-lite")


def _safe_output_filename(filename):
    cleaned = (filename or "").strip()
    cleaned = os.path.basename(cleaned.replace("\\", "/"))
    for char in '<>:"/\\|?*':
        cleaned = cleaned.replace(char, "_")
    cleaned = "".join(ch for ch in cleaned if ch >= " " and ch != "\x7f").strip(" .")
    return cleaned or "toc.pdf"


def _call_gemini_for_toc_entry(page_number, page_count, image_bytes, document_title="", instructions=""):
    api_key = google_ai_studio_api_key()
    if not api_key:
        raise RuntimeError(
            "GOOGLE_AI_API_KEY が未設定です。pdf-toolbox の Cloud Run または .env に Gemini 用キーを設定してください。"
        )

    prompt = f"""
あなたは教材PDFの目次作成担当です。
添付画像は、元PDFの {page_number}/{page_count} ページ目です。
このアプリは先頭に目次ページを1枚挿入するため、この画像は最終PDFでは「PDF p.{page_number + 1}」になります。

目的:
- NotebookLM に投入するPDFの先頭へ置く目次を作ります。
- 元ページには何も書き込まないため、このページの内容を短く正確に表す目次行を作ってください。
- 本文全文のOCRではなく、ページを探すための目次エントリにしてください。
- NotebookLMはPDF内の物理ページ番号で参照するため、最終PDFの物理ページ番号を最優先で書いてください。

出力ルール:
- 日本語で1行だけ返してください。
- 形式は「PDF p.{page_number + 1} / 元PDF p.{page_number} / 原本 p.XX: 見出し - 内容要約」です。
- 画像下部などに印字された原本ページ番号が明確に読める場合だけ「原本 p.XX」と書いてください。
- 原本ページ番号が読めない、欠落している、または不確かな場合は「原本 p.不明」と書いてください。
- 目次の主キーは必ず「PDF p.{page_number + 1}」です。原本ページ番号を主キーにしないでください。
- 見出しがないページは、画像・図・表・問題などページの主内容を見出し化してください。
- ページが空白や表紙に近い場合も、見える内容から判断してください。
- 説明文、Markdownコードブロック、箇条書き、JSONは返さないでください。
""".strip()

    if document_title:
        prompt += f"\n\n文書タイトル候補: {document_title}"
    if instructions:
        prompt += f"\n\n追加指示:\n{instructions}"

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                    to_gemini_inline_image_part(image_bytes),
                ]
            }
        ]
    }

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{TOC_MODEL}:generateContent?key={api_key}"
    resp = requests_post_json(url, payload)
    if resp.status_code != 200:
        raise RuntimeError(f"Gemini API Error ({resp.status_code}): {resp.text}")

    result = resp.json()
    text = result["candidates"][0]["content"]["parts"][0]["text"].strip()
    text = text.replace("```", "").strip()
    return " ".join(text.splitlines()).strip()


def requests_post_json(url, payload):
    return post_generate_content(url, payload, timeout=120, logger=current_app.logger)


def _generate_toc_entries(doc, document_title="", instructions=""):
    entries = []
    page_count = len(doc)
    for page_index, page in enumerate(doc):
        pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
        entry = _call_gemini_for_toc_entry(
            page_number=page_index + 1,
            page_count=page_count,
            image_bytes=pix.tobytes("png"),
            document_title=document_title,
            instructions=instructions,
        )
        entries.append(entry)
    return entries


def _wrap_japanese_text(text, max_chars):
    lines = []
    for raw_line in text.splitlines():
        raw_line = raw_line.strip()
        if not raw_line:
            lines.append("")
            continue
        lines.extend(textwrap.wrap(raw_line, width=max_chars, break_long_words=True, break_on_hyphens=False))
    return lines


def _entries_to_markdown(title, entries):
    lines = [f"# {title or '目次'}", ""]
    lines.extend(f"- {entry}" for entry in entries)
    return "\n".join(lines).strip() + "\n"


def _insert_toc_markdown_page(doc, markdown_text):
    if len(doc) == 0:
        raise ValueError("PDF is empty")

    first_page = doc[0]
    page_width = first_page.rect.width
    page_height = first_page.rect.height
    margin = 42
    base_font_size = 9.2
    line_height = base_font_size * 1.45
    max_chars = max(24, int((page_width - margin * 2) / (base_font_size * 0.55)))

    page = doc.new_page(pno=0, width=page_width, height=page_height)

    printable_lines = []
    for raw_line in (markdown_text or "# 目次").splitlines():
        stripped = raw_line.strip()
        if not stripped:
            printable_lines.append(("", base_font_size, False))
            continue

        font_size = base_font_size
        content = stripped
        if stripped.startswith("# "):
            font_size = 16
            content = stripped[2:].strip()
        elif stripped.startswith("## "):
            font_size = 12.5
            content = stripped[3:].strip()

        for wrapped in _wrap_japanese_text(content, max_chars):
            printable_lines.append((wrapped, font_size, stripped.startswith("#")))

    available_height = page_height - margin * 2
    estimated_height = sum((size * 1.45 if line else line_height) for line, size, _ in printable_lines)
    scale = min(1.0, available_height / estimated_height) if estimated_height else 1.0
    scale = max(0.62, scale)

    y = margin
    for line, font_size, is_heading in printable_lines:
        actual_size = font_size * scale
        actual_line_height = actual_size * (1.6 if is_heading else 1.45)
        if y + actual_line_height > page_height - margin:
            page.insert_textbox(
                fitz.Rect(margin, y, page_width - margin, page_height - margin),
                "...",
                fontsize=max(6, base_font_size * scale),
                fontname="japan",
                color=(0, 0, 0),
            )
            break
        if line:
            page.insert_textbox(
                fitz.Rect(margin, y, page_width - margin, y + actual_line_height + 2),
                line,
                fontsize=actual_size,
                fontname="japan",
                color=(0, 0, 0),
                align=fitz.TEXT_ALIGN_LEFT,
            )
        y += actual_line_height

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
    return jsonify({'file_id': file_id, 'filename': file.filename})


@optimizer_bp.route('/load_from_drive', methods=['POST'])
def load_from_drive():
    try:
        data = request.json or {}
        file_id = str(uuid.uuid4())
        loaded = download_drive_pdf(data.get('drive_file_id'), current_app.config['UPLOAD_FOLDER'], prefix=file_id)
        os.replace(loaded['path'], os.path.join(current_app.config['UPLOAD_FOLDER'], f"{file_id}.pdf"))
        return jsonify({'file_id': file_id, 'filename': loaded['filename'], 'drive_file_id': loaded['drive_file_id']})
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        current_app.logger.error(f"Drive load error: {e}")
        return jsonify({'error': str(e)}), 500

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
        page_count = len(doc)
        doc.close()
        return jsonify({'metadata': meta, 'page_count': page_count})
    except Exception as e:
        current_app.logger.error(f"Error analyzing PDF: {e}")
        return jsonify({'error': str(e)}), 500

@optimizer_bp.route('/generate_toc/<file_id>', methods=['POST'])
def generate_toc(file_id):
    data = request.json or {}
    filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], f"{file_id}.pdf")
    if not os.path.exists(filepath):
        return jsonify({'error': 'File not found'}), 404

    doc = None
    try:
        doc = fitz.open(filepath)
        metadata = data.get('metadata') or {}
        document_title = (metadata.get('title') or doc.metadata.get('title') or '').strip()
        instructions = (data.get('instructions') or '').strip()
        toc_title = f"{document_title} 目次" if document_title else "目次"
        entries = _generate_toc_entries(doc, document_title=document_title, instructions=instructions)
        return jsonify({'toc_markdown': _entries_to_markdown(toc_title, entries), 'toc_entries': entries})
    except Exception as e:
        current_app.logger.error(f"Error generating TOC: {e}")
        return jsonify({'error': str(e)}), 500
    finally:
        if doc is not None:
            doc.close()

@optimizer_bp.route('/process/<file_id>', methods=['POST'])
def process_pdf(file_id):
    data = request.json or {}
    filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], f"{file_id}.pdf")
    if not os.path.exists(filepath):
        return jsonify({'error': 'File not found'}), 404
    output_filename = _safe_output_filename(data.get('filename') or '')
    if not output_filename:
        output_filename = f"{file_id}_toc.pdf"
    if not output_filename.lower().endswith(".pdf"):
        output_filename += ".pdf"
    out_filepath = os.path.join(current_app.config['OUTPUT_FOLDER'], output_filename)
    doc = None
    try:
        doc = fitz.open(filepath)
        meta = doc.metadata
        if 'metadata' in data:
            new_meta = data['metadata']
            if new_meta.get('title'): meta['title'] = new_meta['title']
            if new_meta.get('author'): meta['author'] = new_meta['author']
            if new_meta.get('subject'): meta['subject'] = new_meta['subject']
            doc.set_metadata(meta)

        toc_markdown = (data.get('toc_markdown') or '').strip()
        if not toc_markdown:
            return jsonify({'error': '目次テキストが空です。先に目次を生成するか、右側に目次を入力してください。'}), 400
        _insert_toc_markdown_page(doc, toc_markdown)

        doc.save(out_filepath)
        return jsonify({'download_url': f"/optimizer/download/{quote(output_filename)}", 'filename': output_filename})
    except Exception as e:
        current_app.logger.error(f"Error processing PDF: {e}")
        return jsonify({'error': str(e)}), 500
    finally:
        if doc is not None:
            doc.close()

@optimizer_bp.route('/download/<path:filename>')
def download(filename):
    safe_filename = _safe_output_filename(filename)
    out_filepath = os.path.join(current_app.config['OUTPUT_FOLDER'], safe_filename)
    if os.path.exists(out_filepath):
        return send_file(out_filepath, as_attachment=True, download_name=safe_filename)
    return "Not found", 404
