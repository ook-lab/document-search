import base64
import io

from PIL import Image


def to_gemini_inline_image_part(image_bytes, *, max_side=1800, quality=85):
    """Normalize PDF preview images before sending them to Gemini.

    Large PNG renders from scanned PDFs can trigger repeated upstream 500s.
    JPEG keeps OCR-visible detail while making the request much smaller.
    """
    with Image.open(io.BytesIO(image_bytes)) as image:
        image = image.convert("RGB")
        if max(image.size) > max_side:
            image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)

        output = io.BytesIO()
        image.save(output, format="JPEG", quality=quality, optimize=True)
        normalized = output.getvalue()

    return {
        "inline_data": {
            "mime_type": "image/jpeg",
            "data": base64.b64encode(normalized).decode("utf-8"),
        }
    }
