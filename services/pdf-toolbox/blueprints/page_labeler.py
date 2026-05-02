import io
import os
import uuid
from urllib.parse import quote

import fitz
from flask import Blueprint, current_app, jsonify, render_template, request, send_file
from PIL import Image, ImageDraw, ImageFont

labeler_bp = Blueprint("labeler", __name__, template_folder="../templates")


def _safe_output_filename(filename):
    cleaned = (filename or "").strip()
    cleaned = os.path.basename(cleaned.replace("\\", "/"))
    for char in '<>:"/\\|?*':
        cleaned = cleaned.replace(char, "_")
    cleaned = "".join(ch for ch in cleaned if ch >= " " and ch != "\x7f").strip(" .")
    return cleaned or "labeled.pdf"


def _font_candidates():
    return [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-jp-Regular.otf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        r"C:\Windows\Fonts\meiryo.ttc",
        r"C:\Windows\Fonts\YuGothM.ttc",
        r"C:\Windows\Fonts\msgothic.ttc",
        "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
    ]


def _load_font(font_size_px):
    for path in _font_candidates():
        if os.path.exists(path):
            return ImageFont.truetype(path, font_size_px)
    return ImageFont.load_default()


def _make_label_png(text, font_size_pt=8, padding_px=10, bg_opacity=215):
    scale = 3
    font = _load_font(max(10, int(font_size_pt * scale)))
    draw_probe = ImageDraw.Draw(Image.new("RGBA", (1, 1), (255, 255, 255, 0)))
    bbox = draw_probe.textbbox((0, 0), text, font=font)
    text_width = max(1, bbox[2] - bbox[0])
    text_height = max(1, bbox[3] - bbox[1])
    width = text_width + padding_px * 2
    height = text_height + padding_px * 2

    image = Image.new("RGBA", (width, height), (255, 255, 255, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (0, 0, width - 1, height - 1),
        radius=max(4, padding_px // 2),
        fill=(255, 255, 255, max(0, min(255, int(bg_opacity)))),
        outline=(30, 30, 30, 180),
        width=max(1, scale),
    )
    draw.text((padding_px, padding_px - bbox[1]), text, fill=(0, 0, 0, 255), font=font)

    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue(), width / scale, height / scale


def _label_rect(page, label_width_pt, label_height_pt, position, margin_mm):
    margin = float(margin_mm) * 72 / 25.4
    page_rect = page.rect
    position = position or "top-right"

    if "right" in position:
        x0 = page_rect.x1 - margin - label_width_pt
    else:
        x0 = page_rect.x0 + margin

    if "bottom" in position:
        y0 = page_rect.y1 - margin - label_height_pt
    else:
        y0 = page_rect.y0 + margin

    return fitz.Rect(x0, y0, x0 + label_width_pt, y0 + label_height_pt)


def insert_image_labels(doc, labels, options):
    font_size = float(options.get("font_size", 8))
    margin_mm = float(options.get("margin_mm", 5))
    bg_opacity = int(options.get("bg_opacity", 215))
    position = options.get("position", "top-right")

    for page_index_str, raw_text in (labels or {}).items():
        text = (raw_text or "").strip()
        if not text:
            continue
        page_index = int(page_index_str)
        if page_index < 0 or page_index >= len(doc):
            continue

        png_bytes, width_pt, height_pt = _make_label_png(
            text,
            font_size_pt=font_size,
            padding_px=max(8, int(font_size * 1.4)),
            bg_opacity=bg_opacity,
        )
        page = doc[page_index]
        rect = _label_rect(page, width_pt, height_pt, position, margin_mm)
        page.insert_image(rect, stream=png_bytes, keep_proportion=False, overlay=True)


@labeler_bp.route("/")
def index():
    return render_template("page_labeler.html")


@labeler_bp.route("/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        return jsonify({"error": "No file part"}), 400
    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No selected file"}), 400

    file_id = str(uuid.uuid4())
    filepath = os.path.join(current_app.config["UPLOAD_FOLDER"], f"{file_id}.pdf")
    file.save(filepath)
    return jsonify({"file_id": file_id, "filename": file.filename})


@labeler_bp.route("/files/<file_id>")
def serve_file(file_id):
    filepath = os.path.join(current_app.config["UPLOAD_FOLDER"], f"{file_id}.pdf")
    if os.path.exists(filepath):
        return send_file(filepath)
    return "Not found", 404


@labeler_bp.route("/process/<file_id>", methods=["POST"])
def process(file_id):
    data = request.json or {}
    filepath = os.path.join(current_app.config["UPLOAD_FOLDER"], f"{file_id}.pdf")
    if not os.path.exists(filepath):
        return jsonify({"error": "File not found"}), 404

    output_filename = _safe_output_filename(data.get("filename") or "")
    if not output_filename.lower().endswith(".pdf"):
        output_filename += ".pdf"
    out_filepath = os.path.join(current_app.config["OUTPUT_FOLDER"], output_filename)

    doc = None
    try:
        doc = fitz.open(filepath)
        insert_image_labels(doc, data.get("labels") or {}, data.get("options") or {})
        doc.save(out_filepath)
        return jsonify({"download_url": f"/labeler/download/{quote(output_filename)}", "filename": output_filename})
    except Exception as e:
        current_app.logger.error("Error labeling PDF: %s", e)
        return jsonify({"error": str(e)}), 500
    finally:
        if doc is not None:
            doc.close()


@labeler_bp.route("/download/<path:filename>")
def download(filename):
    safe_filename = _safe_output_filename(filename)
    out_filepath = os.path.join(current_app.config["OUTPUT_FOLDER"], safe_filename)
    if os.path.exists(out_filepath):
        return send_file(out_filepath, as_attachment=True, download_name=safe_filename)
    return "Not found", 404
