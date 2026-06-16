import os
import shutil
import uuid
import zipfile
import copy
import tempfile
from flask import Flask, request, jsonify, render_template, send_from_directory
import pypdfium2 as pdfium
import pdfplumber
import pypdf
from PIL import Image

app = Flask(__name__)

# Configure folder paths in the system's temporary directory to avoid Flask auto-reload
SYSTEM_TEMP = tempfile.gettempdir()
UPLOAD_FOLDER = os.path.join(SYSTEM_TEMP, 'pdf_splitter_uploads')
TEMP_FOLDER = os.path.join(SYSTEM_TEMP, 'pdf_splitter_pages')

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(TEMP_FOLDER, exist_ok=True)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['TEMP_FOLDER'] = TEMP_FOLDER

@app.route('/')
def index():
    return render_template('index.html')

# Custom route to serve page images from the system temporary folder
@app.route('/temp_pages/<session_id>/<filename>')
def serve_temp_page(session_id, filename):
    safe_dir = os.path.join(app.config['TEMP_FOLDER'], session_id)
    return send_from_directory(safe_dir, filename)

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'Empty filename'}), 400
        
    if not file.filename.lower().endswith('.pdf'):
        return jsonify({'error': 'Only PDF files are supported'}), 400

    # Generate a unique directory name for this upload
    session_id = str(uuid.uuid4())
    pdf_name = f"{session_id}_{file.filename}"
    pdf_path = os.path.join(app.config['UPLOAD_FOLDER'], pdf_name)
    file.save(pdf_path)

    # Output directory for page images
    page_img_dir = os.path.join(app.config['TEMP_FOLDER'], session_id)
    os.makedirs(page_img_dir, exist_ok=True)

    pages_info = []

    try:
        # Render pages to PNG using pypdfium2
        doc = pdfium.PdfDocument(pdf_path)
        num_pages = len(doc)
        
        # Open with pdfplumber for coordinate extraction
        with pdfplumber.open(pdf_path) as plumber_pdf:
            for idx in range(num_pages):
                # Save page image
                page = doc[idx]
                bitmap = page.render(scale=2.0)
                pil_img = bitmap.to_pil()
                
                img_filename = f"page_{idx+1}.png"
                img_path = os.path.join(page_img_dir, img_filename)
                pil_img.save(img_path)
                
                # Get dimensions
                plumber_page = plumber_pdf.pages[idx]
                width_pts = float(plumber_page.width)
                height_pts = float(plumber_page.height)
                pixel_width, pixel_height = pil_img.size
                
                # Extract words and detect question positions
                words = plumber_page.extract_words()
                
                # 1. Big questions (x0 around 56.7 pt)
                big_q_y = [w['top'] for w in words if 56.0 <= w['x0'] <= 58.0 and w['top'] < height_pts - 50]
                
                # 2. Sub questions (x0 around 67.9 pt and short text)
                sub_q_y = [w['top'] for w in words if 67.0 <= w['x0'] <= 69.0 and len(w['text'].strip()) <= 5 and w['top'] < height_pts - 50]
                
                # Clean and merge close coordinates
                def clean_markers(y_list):
                    if not y_list:
                        return []
                    y_list = sorted(list(set(y_list)))
                    cleaned = [y_list[0]]
                    for y in y_list[1:]:
                        if y - cleaned[-1] > 15: # at least 15 points gap
                            cleaned.append(y)
                    return cleaned

                big_q_y = clean_markers(big_q_y)
                sub_q_y = clean_markers(sub_q_y)
                
                # Calculate midpoints for splits
                page_start = 40.0 # Exclude header margins
                page_end = height_pts - 35.0 # Exclude footer margins
                
                # Big Question Splits (Only big questions)
                big_markers = [m for m in big_q_y if page_start < m < page_end]
                splits_big = []
                for i in range(len(big_markers) - 1):
                    mid = (big_markers[i] + big_markers[i+1]) / 2.0
                    splits_big.append(mid)
                
                # Sub Question Splits (Big questions + Sub questions)
                sub_markers = sorted(list(set(big_q_y + sub_q_y)))
                sub_markers = clean_markers(sub_markers)
                sub_markers = [m for m in sub_markers if page_start < m < page_end]
                
                splits_sub = []
                for i in range(len(sub_markers) - 1):
                    mid = (sub_markers[i] + sub_markers[i+1]) / 2.0
                    splits_sub.append(mid)
                
                # Convert splits to pixels (scale=2)
                splits_big_px = [int(s * 2.0) for s in splits_big]
                splits_sub_px = [int(s * 2.0) for s in splits_sub]
                
                pages_info.append({
                    'page_num': idx + 1,
                    'image_url': f'/temp_pages/{session_id}/{img_filename}',
                    'width': width_pts,
                    'height': height_pts,
                    'pixel_width': pixel_width,
                    'pixel_height': pixel_height,
                    'default_splits_big': splits_big_px,
                    'default_splits_sub': splits_sub_px
                })
                
    except Exception as e:
        return jsonify({'error': f"Failed to process PDF: {str(e)}"}), 500
        
    return jsonify({
        'pdf_name': pdf_name,
        'session_id': session_id,
        'pages': pages_info
    })

@app.route('/split', methods=['POST'])
def split_pdf():
    data = request.json
    if not data:
        return jsonify({'error': 'Missing request body'}), 400
        
    pdf_name = data.get('pdf_name')
    session_id = data.get('session_id')
    splits_dict = data.get('splits', {}) # key: page_num (str), value: list of y-coords in pixels
    export_format = data.get('format', 'png').lower() # 'png' or 'pdf'
    exclude_footer = data.get('exclude_footer', True)

    if not pdf_name or not session_id:
        return jsonify({'error': 'Missing pdf_name or session_id'}), 400

    pdf_path = os.path.join(app.config['UPLOAD_FOLDER'], pdf_name)
    if not os.path.exists(pdf_path):
        return jsonify({'error': 'PDF file not found'}), 400

    # Output ZIP file path
    zip_filename = f"split_{pdf_name.split('_', 1)[1].replace('.pdf', '')}_{uuid.uuid4().hex[:8]}.zip"
    zip_path = os.path.join(app.config['TEMP_FOLDER'], session_id, zip_filename)

    try:
        if export_format == 'pdf':
            reader = pypdf.PdfReader(pdf_path)
            
        # Create ZIP file
        with zipfile.ZipFile(zip_path, 'w') as zip_file:
            for page_str, splits in splits_dict.items():
                page_idx = int(page_str) - 1
                
                # Get page dimensions
                if export_format == 'pdf':
                    pdf_page = reader.pages[page_idx]
                    height_pts = float(pdf_page.mediabox.top)
                    width_pts = float(pdf_page.mediabox.right)
                    pixel_height = int(height_pts * 2.0)
                else:
                    # PNG mode: Load page image to get size
                    img_filename = f"page_{page_str}.png"
                    img_path = os.path.join(app.config['TEMP_FOLDER'], session_id, img_filename)
                    if not os.path.exists(img_path):
                        continue
                    page_img = Image.open(img_path)
                    pixel_width, pixel_height = page_img.size
                
                # Sort and clean splits
                splits = sorted(list(set(int(y) for y in splits)))
                
                # Add top boundary
                y_coords = [0]
                for y in splits:
                    # Bounds check
                    if 0 < y < pixel_height:
                        y_coords.append(y)
                
                # Add bottom boundary
                # If exclude_footer is true, cut off the footer (approx bottom 35pt / 70px)
                bottom_limit = pixel_height - 70 if exclude_footer else pixel_height
                y_coords.append(bottom_limit)
                
                # Perform slicing
                for slice_idx in range(len(y_coords) - 1):
                    y0 = y_coords[slice_idx]
                    y1 = y_coords[slice_idx + 1]
                    
                    if y1 - y0 < 10: # Skip extremely tiny crops
                        continue
                        
                    slice_filename = f"page_{page_str.zfill(2)}_part_{str(slice_idx+1).zfill(2)}"
                    
                    if export_format == 'pdf':
                        # PDF crop using pypdf
                        # Convert pixel y-coordinates (top-down) to PDF points (bottom-up)
                        pt_y0 = height_pts - (y0 / 2.0)
                        pt_y1 = height_pts - (y1 / 2.0)
                        
                        # In PDF, pt_y1 is lower than pt_y0 because coordinates are bottom-up
                        # So new bottom = pt_y1, new top = pt_y0
                        cropped_page = copy.copy(pdf_page)
                        cropped_page.mediabox.bottom = max(0.0, pt_y1)
                        cropped_page.mediabox.top = min(height_pts, pt_y0)
                        cropped_page.mediabox.left = 0.0
                        cropped_page.mediabox.right = width_pts
                        
                        # Write page to temporary PDF file
                        temp_pdf_path = os.path.join(app.config['TEMP_FOLDER'], session_id, f"{slice_filename}.pdf")
                        writer = pypdf.PdfWriter()
                        writer.add_page(cropped_page)
                        with open(temp_pdf_path, 'wb') as f:
                            writer.write(f)
                            
                        # Add to ZIP and clean up temp file
                        zip_file.write(temp_pdf_path, f"{slice_filename}.pdf")
                        os.remove(temp_pdf_path)
                    else:
                        # PNG crop using PIL
                        cropped_img = page_img.crop((0, y0, pixel_width, y1))
                        temp_png_path = os.path.join(app.config['TEMP_FOLDER'], session_id, f"{slice_filename}.png")
                        cropped_img.save(temp_png_path)
                        
                        # Add to ZIP and clean up temp file
                        zip_file.write(temp_png_path, f"{slice_filename}.png")
                        os.remove(temp_png_path)
                        
    except Exception as e:
        return jsonify({'error': f"Failed to split PDF: {str(e)}"}), 500
        
    return jsonify({
        'zip_url': f'/download/{session_id}/{zip_filename}'
    })

@app.route('/download/<session_id>/<filename>')
def download_file(session_id, filename):
    safe_dir = os.path.join(app.config['TEMP_FOLDER'], session_id)
    return send_from_directory(safe_dir, filename, as_attachment=True)

if __name__ == '__main__':
    app.run(debug=True, port=5000)
