import os
import json
import uuid
import fitz  # PyMuPDF
import google.generativeai as genai
from flask import Flask, render_template, request, jsonify, send_file, url_for
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
from PIL import Image

# Load environment variables
dotenv_path = os.path.join(os.path.dirname(__file__), '../../.env')
load_dotenv(dotenv_path)

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(__file__), 'uploads')
app.config['OUTPUT_FOLDER'] = os.path.join(os.path.dirname(__file__), 'outputs')
app.config['STATIC_FOLDER'] = os.path.join(os.path.dirname(__file__), 'static')

# Create necessary directories
for folder in [app.config['UPLOAD_FOLDER'], app.config['OUTPUT_FOLDER']]:
    if not os.path.exists(folder):
        os.makedirs(folder)

# Gemini Setup
genai.configure(api_key=os.getenv("GOOGLE_AI_API_KEY"))
model_name = os.getenv("STAGE1_MODEL", "gemini-2.5-flash-lite")
model = genai.GenerativeModel(model_name)

# Template Store (In-memory for prototype, could be JSON file)
TEMPLATES_FILE = os.path.join(os.path.dirname(__file__), 'templates.json')
if not os.path.exists(TEMPLATES_FILE):
    with open(TEMPLATES_FILE, 'w', encoding='utf-8') as f:
        json.dump({
            "Standard": {
                "name": "Standard OCR",
                "prompt": "Extract all text and their bounding boxes. Group related text, sentences, and paragraphs into single continuous blocks where appropriate. Do NOT extract character by character or separate small fragments unless they are independent. Focus on preserving the natural reading layout."
            },
            "Invoice": {
                "name": "Invoice Parser",
                "prompt": "Identify Invoice Number, Date, Vendor Name, Total Amount, and separate line items with their price and quantity. Group related fields. Provide coordinates for each detected text block."
            }
        }, f, indent=2, ensure_ascii=False)

def load_templates():
    with open(TEMPLATES_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

@app.route('/')
def index():
    templates = load_templates()
    return render_template('index.html', templates=templates)

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400
    
    filename = str(uuid.uuid4()) + "_" + secure_filename(file.filename)
    pdf_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(pdf_path)
    
    # Process PDF to get previews
    doc = fitz.open(pdf_path)
    previews = []
    
    preview_dir = os.path.join(app.config['STATIC_FOLDER'], 'previews', filename)
    os.makedirs(preview_dir, exist_ok=True)
    
    for page_num in range(len(doc)):
        page = doc.load_page(page_num)
        zoom = 2  # high-res
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat)
        
        img_name = f"page_{page_num}.png"
        img_path = os.path.join(preview_dir, img_name)
        pix.save(img_path)
        
        previews.append({
            "page_num": page_num,
            "url": url_for('static', filename=f'previews/{filename}/{img_name}'),
            "width": pix.width,
            "height": pix.height,
            "page_width": page.rect.width,
            "page_height": page.rect.height
        })
        
    doc.close()
    
    return jsonify({
        "pdf_id": filename,
        "previews": previews
    })

@app.route('/ocr', methods=['POST'])
def run_ocr():
    data = request.json
    pdf_id = data.get('pdf_id')
    page_num = data.get('page_num', 0)
    template_id = data.get('template_id', 'Standard')
    
    img_path = os.path.abspath(os.path.join(app.config['STATIC_FOLDER'], 'previews', pdf_id, f"page_{page_num}.png"))
    
    templates = load_templates()
    template_prompt = templates.get(template_id, templates['Standard'])['prompt']
    
    full_prompt = (
        "Perform OCR on this image. "
        f"{template_prompt} "
        "Return a JSON array of objects. Each object MUST have: "
        "- 'text': The extracted text content. "
        "- 'box_2d': [ymin, xmin, ymax, xmax] in normalized 0-1000 format. "
        "Return ONLY the raw JSON array."
    )
    
    img = Image.open(img_path)
    response = model.generate_content([full_prompt, img])
    
    try:
        # Simple extraction of JSON from response text
        text_content = response.text.replace('```json', '').replace('```', '').strip()
        ocr_results = json.loads(text_content)
        return jsonify(ocr_results)
    except Exception as e:
        print(f"Error parsing Gemini response: {e}")
        return jsonify({"error": "Failed to parse AI response", "raw": response.text}), 500

@app.route('/save', methods=['POST'])
def save_pdf():
    data = request.json
    pdf_id = data.get('pdf_id')
    corrections = data.get('corrections', []) # List of {page_num, text, box_2d (normalized)}
    
    original_path = os.path.join(app.config['UPLOAD_FOLDER'], pdf_id)
    output_filename = f"searchable_{pdf_id}"
    output_path = os.path.join(app.config['OUTPUT_FOLDER'], output_filename)
    
    doc = fitz.open(original_path)
    
    for item in corrections:
        page_num = item.get('page_num')
        text = item.get('text')
        box = item.get('box_2d') # [ymin, xmin, ymax, xmax]
        
        page = doc.load_page(page_num)
        p_width = page.rect.width
        p_height = page.rect.height
        
        # Convert normalized 0-1000 to PDF coordinates
        ymin, xmin, ymax, xmax = box
        y = (ymin / 1000.0) * p_height
        x = (xmin / 1000.0) * p_width
        
        # Determine font size based on box height if possible
        box_h = ((ymax - ymin) / 1000.0) * p_height
        fontsize = max(8, box_h * 0.8)
        
        # render_mode=3 makes text invisible but searchable
        page.insert_text((x, y + (fontsize * 0.8)), text, fontsize=fontsize, render_mode=3)
        
    doc.save(output_path)
    doc.close()
    
    return jsonify({"download_url": url_for('download_file', filename=output_filename)})

@app.route('/download/<filename>')
def download_file(filename):
    return send_file(os.path.join(app.config['OUTPUT_FOLDER'], filename))

@app.route('/save_template', methods=['POST'])
def save_template():
    data = request.json
    template_id = data.get('id')
    content = data.get('content') # {name, prompt}
    
    templates = load_templates()
    templates[template_id] = content
    
    with open(TEMPLATES_FILE, 'w', encoding='utf-8') as f:
        json.dump(templates, f, indent=2, ensure_ascii=False)
        
    return jsonify({"success": True})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5003))
    app.run(debug=True, host='0.0.0.0', port=port)
