import os
import io
from flask import Flask, render_template_string, request, send_file, flash, redirect
from werkzeug.utils import secure_filename
from pypdf import PdfReader, PdfWriter

app = Flask(__name__)
app.secret_key = "super_secret_key_for_flash"

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>PDFスプリッター</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-color: #0f172a;
            --card-bg: #1e293b;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --accent: #3b82f6;
            --accent-hover: #2563eb;
            --border-color: #334155;
            --success: #10b981;
            --error: #ef4444;
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }

        body {
            font-family: 'Inter', sans-serif;
            background-color: var(--bg-color);
            color: var(--text-main);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 2rem;
        }

        .container {
            width: 100%;
            max-width: 540px;
            background: var(--card-bg);
            border-radius: 20px;
            padding: 3rem 2rem;
            box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5);
            border: 1px solid var(--border-color);
            position: relative;
            overflow: hidden;
        }

        /* Subtle glowing effect */
        .container::before {
            content: '';
            position: absolute;
            top: -50%;
            left: -50%;
            width: 200%;
            height: 200%;
            background: radial-gradient(circle at 50% 50%, rgba(59, 130, 246, 0.1), transparent 60%);
            z-index: 0;
            pointer-events: none;
        }

        .content {
            position: relative;
            z-index: 1;
        }

        h1 {
            text-align: center;
            font-size: 2rem;
            font-weight: 700;
            margin-bottom: 0.5rem;
            background: linear-gradient(135deg, #60a5fa, #3b82f6);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }

        p.subtitle {
            text-align: center;
            color: var(--text-muted);
            margin-bottom: 2.5rem;
            font-size: 0.95rem;
        }

        .drop-zone {
            border: 2px dashed var(--border-color);
            border-radius: 12px;
            padding: 3rem 2rem;
            text-align: center;
            transition: all 0.3s ease;
            background: rgba(15, 23, 42, 0.5);
            margin-bottom: 1.5rem;
            cursor: pointer;
        }

        .drop-zone.dragover {
            border-color: var(--accent);
            background: rgba(59, 130, 246, 0.1);
            transform: scale(1.02);
        }

        .icon {
            font-size: 3rem;
            margin-bottom: 1rem;
            display: inline-block;
        }

        .file-input {
            display: none;
        }

        .btn-select {
            display: inline-block;
            background: var(--border-color);
            color: var(--text-main);
            padding: 0.5rem 1.25rem;
            border-radius: 8px;
            font-size: 0.9rem;
            font-weight: 500;
            margin-top: 1rem;
            transition: background 0.3s ease;
        }
        
        .drop-zone:hover .btn-select {
            background: #475569;
        }
        
        .file-name {
            display: block;
            margin-top: 1rem;
            font-weight: 500;
            color: var(--accent);
            word-break: break-all;
        }

        .form-group {
            margin-bottom: 1.5rem;
        }

        .label {
            display: block;
            margin-bottom: 0.5rem;
            font-size: 0.9rem;
            color: var(--text-muted);
            font-weight: 500;
        }

        select {
            width: 100%;
            padding: 0.875rem 1rem;
            background: rgba(15, 23, 42, 0.8);
            border: 1px solid var(--border-color);
            border-radius: 10px;
            color: var(--text-main);
            font-family: inherit;
            font-size: 1rem;
            outline: none;
            transition: border-color 0.3s ease;
            appearance: none;
            background-image: url('data:image/svg+xml;charset=US-ASCII,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20width%3D%22292.4%22%20height%3D%22292.4%22%3E%3Cpath%20fill%3D%22%2394a3b8%22%20d%3D%22M287%2069.4a17.6%2017.6%200%200%200-13-5.4H18.4c-5%200-9.3%201.8-12.9%205.4A17.6%2017.6%200%200%200%200%2082.2c0%205%201.8%209.3%205.4%2012.9l128%20127.9c3.6%203.6%207.8%205.4%2012.8%205.4s9.2-1.8%2012.8-5.4L287%2095c3.5-3.5%205.4-7.8%205.4-12.8%200-5-1.9-9.2-5.5-12.8z%22%2F%3E%3C%2Fsvg%3E');
            background-repeat: no-repeat;
            background-position: right 1rem top 50%;
            background-size: 0.65rem auto;
        }

        select:focus {
            border-color: var(--accent);
        }

        .btn-submit {
            width: 100%;
            background: linear-gradient(135deg, var(--accent), var(--accent-hover));
            color: white;
            border: none;
            padding: 1rem;
            border-radius: 10px;
            font-size: 1.1rem;
            font-weight: 600;
            cursor: pointer;
            transition: transform 0.2s ease, box-shadow 0.2s ease;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 0.5rem;
        }

        .btn-submit:hover {
            transform: translateY(-2px);
            box-shadow: 0 10px 20px -10px rgba(59, 130, 246, 0.5);
        }

        .btn-submit:active {
            transform: translateY(0);
        }

        .btn-submit:disabled {
            background: #475569;
            cursor: not-allowed;
            transform: none;
            box-shadow: none;
        }

        .error {
            background: rgba(239, 68, 68, 0.1);
            color: var(--error);
            padding: 1rem;
            border-radius: 8px;
            margin-bottom: 1.5rem;
            font-size: 0.9rem;
            border: 1px solid rgba(239, 68, 68, 0.2);
            text-align: center;
        }
        
        .loading {
            display: none;
            width: 20px;
            height: 20px;
            border: 3px solid rgba(255,255,255,0.3);
            border-radius: 50%;
            border-top-color: white;
            animation: spin 1s ease-in-out infinite;
        }
        
        @keyframes spin {
            to { transform: rotate(360deg); }
        }
    </style>
</head>
<body>

    <div class="container">
        <div class="content">
            <h1>PDFスプリッター</h1>
            <p class="subtitle">PDFの各ページを左右半分に分割します。</p>

            {% with messages = get_flashed_messages() %}
              {% if messages %}
                <div class="error">
                  {{ messages[0] }}
                </div>
              {% endif %}
            {% endwith %}

            <form id="upload-form" action="/split" method="POST" enctype="multipart/form-data">
                <div class="drop-zone" id="drop-zone">
                    <span class="icon">📄</span>
                    <h3>PDFファイルをここにドロップ</h3>
                    <p style="color: var(--text-muted); margin-top: 0.5rem; font-size: 0.85rem;">または</p>
                    <span class="btn-select">ファイルを選択する</span>
                    <input type="file" name="pdf_file" id="file-input" class="file-input" accept="application/pdf">
                    <span class="file-name" id="file-name"></span>
                </div>

                <div class="form-group">
                    <label class="label">出力ページの順序</label>
                    <select name="order" id="order">
                        <option value="ltor">左側 → 右側 (一般的な横書き)</option>
                        <option value="rtol">右側 → 左側 (一般的な縦書き・漫画)</option>
                    </select>
                </div>

                <button type="submit" class="btn-submit" id="submit-btn" disabled>
                    処理を開始する
                    <div class="loading" id="loading-spinner"></div>
                </button>
            </form>
        </div>
    </div>

    <script>
        const dropZone = document.getElementById('drop-zone');
        const fileInput = document.getElementById('file-input');
        const fileNameDisplay = document.getElementById('file-name');
        const submitBtn = document.getElementById('submit-btn');
        const form = document.getElementById('upload-form');
        const loadingSpinner = document.getElementById('loading-spinner');

        // Prevent default drag behaviors
        ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
            dropZone.addEventListener(eventName, preventDefaults, false);
            document.body.addEventListener(eventName, preventDefaults, false);
        });

        function preventDefaults(e) {
            e.preventDefault();
            e.stopPropagation();
        }

        // Highlight drop zone when item is dragged over it
        ['dragenter', 'dragover'].forEach(eventName => {
            dropZone.addEventListener(eventName, highlight, false);
        });

        ['dragleave', 'drop'].forEach(eventName => {
            dropZone.addEventListener(eventName, unhighlight, false);
        });

        function highlight(e) {
            dropZone.classList.add('dragover');
        }

        function unhighlight(e) {
            dropZone.classList.remove('dragover');
        }

        // Handle dropped files
        dropZone.addEventListener('drop', handleDrop, false);

        function handleDrop(e) {
            const dt = e.dataTransfer;
            const files = dt.files;
            handleFiles(files);
        }

        dropZone.addEventListener('click', () => {
            fileInput.click();
        });

        fileInput.addEventListener('change', function() {
            handleFiles(this.files);
        });

        function handleFiles(files) {
            if (files.length > 0) {
                const file = files[0];
                if (file.type === 'application/pdf') {
                    fileNameDisplay.textContent = file.name;
                    submitBtn.disabled = false;
                    
                    // Assign file to input if generated from drop
                    if (fileInput.files.length === 0 || fileInput.files[0] !== file) {
                         const dataTransfer = new DataTransfer();
                         dataTransfer.items.add(file);
                         fileInput.files = dataTransfer.files;
                    }
                } else {
                    fileNameDisplay.textContent = 'エラー: PDFファイルを選択してください。';
                    fileNameDisplay.style.color = 'var(--error)';
                    submitBtn.disabled = true;
                    fileInput.value = ''; // Reset
                }
            }
        }

        form.addEventListener('submit', () => {
            submitBtn.disabled = true;
            // temporarily change text and show loading
            const btnText = submitBtn.childNodes[0];
            btnText.textContent = '処理中... ';
            loadingSpinner.style.display = 'block';
            
            // Re-enable after a short delay so user can process another file
            setTimeout(() => {
                submitBtn.disabled = false;
                btnText.textContent = '処理を開始する ';
                loadingSpinner.style.display = 'none';
            }, 3000); // Resume button after 3 seconds assuming download prompt appeared
        });
    </script>
</body>
</html>
"""

def split_pdf_left_right(input_stream, order='ltor'):
    input_stream.seek(0)
    data = input_stream.read()
    
    # We create two distinct readers from the same byte stream 
    # to avoid interference between cropbox modifications
    reader_left = PdfReader(io.BytesIO(data))
    reader_right = PdfReader(io.BytesIO(data))
    
    writer = PdfWriter()
    
    for i in range(len(reader_left.pages)):
        page_l = reader_left.pages[i]
        page_r = reader_right.pages[i]
        
        # Calculate the middle coordinate
        # mediabox dimensions are typically Decimal or Float objects
        width = float(page_l.mediabox.width)
        half_x = float(page_l.mediabox.left) + (width / 2.0)
        
        # Left half
        page_l.mediabox.right = half_x
        page_l.cropbox.right = half_x
        
        # Right half
        page_r.mediabox.left = half_x
        page_r.cropbox.left = half_x
        
        if order == 'ltor':
            writer.add_page(page_l)
            writer.add_page(page_r)
        else:
            # Japanese vertical writing / Manga reading direction
            writer.add_page(page_r)
            writer.add_page(page_l)

    out_stream = io.BytesIO()
    writer.write(out_stream)
    out_stream.seek(0)
    return out_stream

@app.route("/", methods=["GET"])
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route("/split", methods=["POST"])
def split_pdf():
    if "pdf_file" not in request.files:
        flash("ファイルが見つかりません。")
        return redirect("/")
        
    file = request.files["pdf_file"]
    
    if file.filename == "":
        flash("ファイルが選択されていません。")
        return redirect("/")
        
    if not file.filename.lower().endswith(".pdf"):
        flash("PDFファイルのみ対応しています。")
        return redirect("/")
        
    order = request.form.get("order", "ltor")
    
    try:
        out_stream = split_pdf_left_right(file.stream, order=order)
        
        # Generate new filename
        original_base = os.path.splitext(file.filename)[0]
        new_filename = f"{original_base}_split.pdf"
        
        return send_file(
            out_stream,
            as_attachment=True,
            download_name=new_filename,
            mimetype="application/pdf"
        )
    except Exception as e:
        print(f"Error processing PDF: {e}")
        flash("PDFの処理中にエラーが発生しました。")
        return redirect("/")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
