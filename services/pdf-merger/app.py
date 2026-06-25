import os
import re
import logging
import csv
import tempfile
from pathlib import Path
from flask import Flask, render_template, request, flash, redirect, url_for, jsonify
import fitz  # PyMuPDF
from pypdf import PdfWriter

# Google Drive コネクタのインポート
from google_drive_connector import GoogleDriveConnector

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "pdf-merger-secret-key-12345")

@app.template_filter('commajil')
def commajil_filter(value):
    try:
        return f"{int(value):,}"
    except (ValueError, TypeError):
        return value

# ロギング設定
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# サブフォルダ内PDFの優先キーワード順
PRIORITY_KEYWORDS = [
    "金銭請求伝票",
    "請求総括表",
    "（印刷）請求書_製作管理課",
    "（印刷）請求書【単・宣・映・事】_製作管理課",
    "（加工）請求書_製作管理課",
    "（製本）請求書_製作管理課"
]

def extract_code(filename):
    """ファイル名から括弧内の4桁の数字（会社コード）を抽出する"""
    match = re.search(r'\((\d{4})\)', filename)
    if match:
        return match.group(1)
    match = re.search(r'(\d{4})', filename)
    if match:
        return match.group(1)
    return None

def extract_prefix(filename):
    """ファイル名からプレフィックス（例: '2026年06月_制作月次_'）を抽出する"""
    match = re.match(r'^([^_]+_[^_]+_)', filename)
    if match:
        return match.group(1)
    if "_" in filename:
        parts = filename.split("_")
        if len(parts) >= 2:
            return f"{parts[0]}_{parts[1]}_"
    return "PDF結合_"

def get_subfolder_sort_key(file_path_name):
    """ファイル名でのソートキーを返す"""
    for i, keyword in enumerate(PRIORITY_KEYWORDS):
        if keyword in file_path_name:
            return i
    return len(PRIORITY_KEYWORDS)

def get_japanese_font():
    """環境に存在する日本語フォントファイルのパスを返す"""
    windows_fonts = [
        r"C:\Windows\Fonts\msgothic.ttc",
        r"C:\Windows\Fonts\msmincho.ttc",
        r"C:\Windows\Fonts\meiryo.ttc"
    ]
    linux_fonts = [
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/fonts-japanese-gothic.ttf"
    ]
    for f in windows_fonts + linux_fonts:
        if os.path.exists(f):
            return f
    return None

def create_payment_report_pdf(payment_list, output_path, prefix_title):
    """支払一覧表をA4 PDFとして綺麗に出力する"""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842) # A4
    
    font_file = get_japanese_font()
    if font_file:
        font_name = "jp-font"
        page.insert_font(fontname=font_name, fontfile=font_file)
    else:
        font_name = "helv"
        logger.warning("日本語フォントファイルが見つかりませんでした。英語フォントでフォールバックします。")

    # 1. タイトル
    title = f"{prefix_title.rstrip('_')} 支払一覧表"
    page.insert_text((40, 60), title, fontsize=16, fontname=font_name)
    
    # 2. テーブルヘッダー設定
    headers = ["コード", "支払い先名", "支払総額 (税込)", "消費税", "約束手形", "振込金額"]
    col_widths = [50, 145, 80, 80, 80, 80]
    x_positions = [40]
    for w in col_widths[:-1]:
        x_positions.append(x_positions[-1] + w)
        
    y = 100
    
    # ヘッダー背景
    page.draw_rect(fitz.Rect(40, y - 10, 555, y + 14), color=None, fill=(0.95, 0.95, 0.95), overlay=False)
    
    # ヘッダーテキスト (textboxを使う)
    for i, h in enumerate(headers):
        align = 2 if i >= 2 else 0 # 2=RIGHT, 0=LEFT
        rect = fitz.Rect(x_positions[i], y - 8, x_positions[i] + col_widths[i], y + 12)
        page.insert_textbox(rect, h, fontsize=9, fontname=font_name, align=align)
        
    y += 24
    
    # 3. データ行の描画
    total_total = 0
    total_tax = 0
    total_note = 0
    total_transfer = 0
    
    for item in payment_list:
        total_total += item["total"]
        total_tax += item["tax"]
        total_note += item["note"]
        total_transfer += item["transfer"]
        
        row_data = [
            str(item["code"]),
            item["name"],
            f"{item['total']:,}円",
            f"{item['tax']:,}円",
            f"{item['note']:,}円",
            f"{item['transfer']:,}円"
        ]
        
        # 細い仕切り線
        page.draw_line((40, y - 10), (555, y - 10), color=(0.9, 0.9, 0.9), width=0.5)
        
        for i, val in enumerate(row_data):
            align = 2 if i >= 2 else 0
            rect = fitz.Rect(x_positions[i], y - 8, x_positions[i] + col_widths[i], y + 12)
            page.insert_textbox(rect, val, fontsize=9, fontname=font_name, align=align)
            
        y += 22
        
    # 4. 合計行の描画
    page.draw_line((40, y - 10), (555, y - 10), color=(0.5, 0.5, 0.5), width=1)
    
    # 合計行背景
    page.draw_rect(fitz.Rect(40, y - 10, 555, y + 14), color=None, fill=(0.97, 0.97, 0.97), overlay=False)
    
    total_row = [
        "-",
        f"合計 ({len(payment_list)} 社)",
        f"{total_total:,}円",
        f"{total_tax:,}円",
        f"{total_note:,}円",
        f"{total_transfer:,}円"
    ]
    
    for i, val in enumerate(total_row):
        align = 2 if i >= 2 else 0
        rect = fitz.Rect(x_positions[i], y - 8, x_positions[i] + col_widths[i], y + 12)
        page.insert_textbox(rect, val, fontsize=9, fontname=font_name, align=align)
        
    # 下線
    page.draw_line((40, y + 14), (555, y + 14), color=(0.5, 0.5, 0.5), width=1)
    
    doc.save(str(output_path))
    doc.close()
    logger.info(f"支払一覧PDFを作成しました: {output_path}")

def extract_payment_values(pdf_path):
    """金銭請求伝票の物理レイアウト(X, Y座標)から金額データを高精度に抽出する"""
    total = 0
    net = 0
    tax = 0
    note_val = 0
    transfer_val = 0

    try:
        doc = fitz.open(pdf_path)
        page = doc[0]
        blocks = page.get_text("blocks")
        doc.close()
        
        blocks.sort(key=lambda b: (round(b[1], 1), round(b[0], 1)))
        
        for b in blocks:
            x0, y0, x1, y1, text, _, _ = b
            parts = re.split(r'\s{2,}|\n', text.strip())
            nums = []
            for p in parts:
                clean_p = p.replace(" ", "").replace(",", "").strip()
                if clean_p.isdigit() and len(clean_p) >= 3:
                    nums.append(int(clean_p))
                    
            if not nums:
                continue
                
            # 1. 支払総額 (Y=140〜175)
            if 140 <= y0 <= 175:
                total = nums[0]
            # 2. 本体価格 (Y=180〜215)
            elif 180 <= y0 <= 215:
                net = nums[0]
            # 3. 消費税 (Y=250〜290)
            elif 250 <= y0 <= 290:
                tax = nums[0]
            # 4. 約束手形の金額 (Y=310〜335)
            elif 310 <= y0 <= 335:
                note_val = nums[0]
            # 5. 振込金額 (Y=340〜365)
            elif 340 <= y0 <= 365:
                transfer_val = nums[0]
                
        # フォールバック (手形・振込が両方0の場合のみ)
        if note_val == 0 and transfer_val == 0 and total:
            if total < 150000:
                transfer_val = total
            else:
                note_val = net
                transfer_val = tax
                
    except Exception as e:
        logger.error(f"金銭請求伝票の解析エラー ({pdf_path.name}): {e}")
        
    return {
        "total": total,
        "net": net,
        "tax": tax,
        "note": note_val,
        "transfer": transfer_val
    }

def process_pdf_merger_gdrive(main_folder_id):
    """Google Drive上のPDFファイルを処理・結合し、集計結果を出力する"""
    drive = GoogleDriveConnector()

    # 1. メインフォルダ直下のサブフォルダ一覧を取得
    query_subdirs = f"'{main_folder_id}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    sub_dirs = drive.service.files().list(
        q=query_subdirs,
        spaces='drive',
        fields='files(id, name)',
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
        corpora='allDrives'
    ).execute().get('files', [])

    if not sub_dirs:
        raise ValueError("指定されたメインフォルダ内にサブフォルダが見つかりませんでした。")

    # 2. プレフィックスの事前特定 (スキップすべき成果物フォルダ名の確定のため)
    prefix = None
    for sub_dir in sub_dirs:
        pdf_files = drive.list_files_in_folder(sub_dir['id'], mime_type_filter="mimeType = 'application/pdf'")
        for pdf in pdf_files:
            # 成果物ファイル（出力用、支払一覧、会社コード等）は無視してオリジナルのプレフィックスを探す
            if not pdf['name'].endswith("_出力用.pdf") and not pdf['name'].endswith("_支払一覧.pdf") and not re.match(r'^\d{4}\.pdf$', pdf['name']):
                prefix = extract_prefix(pdf['name'])
                break
        if prefix:
            break
            
    if not prefix:
        prefix = "結合成果物_"

    output_dir_name = prefix.rstrip("_")

    # 3. 成果物保存先フォルダの特定または作成
    output_folder_id = None
    for sub_dir in sub_dirs:
        if sub_dir['name'] == output_dir_name:
            output_folder_id = sub_dir['id']
            break
            
    if not output_folder_id:
        output_folder_id = drive.create_folder(output_dir_name, main_folder_id)
        if not output_folder_id:
            raise RuntimeError(f"Google Drive上に成果物フォルダ '{output_dir_name}' を作成できませんでした。")

    processed_subfolders = []
    global_files = []
    payment_list = []

    # 4. ローカルの一時作業ディレクトリを作成
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        
        # 5. 各サブフォルダをループ処理
        for sub_dir in sub_dirs:
            # 成果物フォルダと同名の場合は完全にスキップ
            if sub_dir['name'] == output_dir_name:
                continue
                
            pdf_files = drive.list_files_in_folder(sub_dir['id'], mime_type_filter="mimeType = 'application/pdf'")
            if not pdf_files:
                continue

            company_code = None
            company_name = sub_dir['name']
            
            # 各ファイルのダウンロードとメタデータ解析
            local_pdfs = []
            for pdf in pdf_files:
                code = extract_code(pdf['name'])
                if code:
                    company_code = code
                
                # ローカルに一時ダウンロード
                local_path = drive.download_file(pdf['id'], pdf['name'], temp_path)
                if local_path:
                    local_pdfs.append(Path(local_path))

            if not local_pdfs:
                continue

            # 支払データの抽出 (金銭請求伝票)
            invoice_pdf = None
            for p in local_pdfs:
                if "金銭請求伝票" in p.name:
                    invoice_pdf = p
                    break
            
            if invoice_pdf:
                pay_data = extract_payment_values(invoice_pdf)
            else:
                pay_data = {"total": 0, "net": 0, "tax": 0, "note": 0, "transfer": 0}

            payment_list.append({
                "code": company_code or "不明",
                "name": company_name,
                "total": pay_data["total"],
                "net": pay_data["net"],
                "tax": pay_data["tax"],
                "note": pay_data["note"],
                "transfer": pay_data["transfer"]
            })

            # 結合対象ファイルのフィルタリングとソート
            # 「請求書（総額）」は除外
            target_pdfs = [f for f in local_pdfs if "請求書（総額）" not in f.name]
            if not target_pdfs:
                continue

            target_pdfs.sort(key=lambda x: get_subfolder_sort_key(x.name))

            # 全体「出力用」用の素材収集 (金銭請求伝票 と 請求総括表)
            for pdf in target_pdfs:
                if "金銭請求伝票" in pdf.name:
                    global_files.append((company_code or "9999", 0, pdf))
                elif "請求総括表" in pdf.name:
                    global_files.append((company_code or "9999", 1, pdf))

            # サブフォルダ結合
            merger = PdfWriter()
            for pdf in target_pdfs:
                merger.append(str(pdf))
            
            out_filename = f"{company_code}.pdf" if company_code else f"{sub_dir['name']}.pdf"
            local_out_path = temp_path / out_filename
            
            merger.write(str(local_out_path))
            merger.close()
            
            # Google Driveにアップロード
            drive.upload_file_from_path(local_out_path, folder_id=output_folder_id)
            
            processed_subfolders.append({
                "sub_dir_name": sub_dir['name'],
                "company_code": company_code or "不明",
                "files_count": len(target_pdfs),
                "output_file": out_filename
            })

        # 6. 全体「出力用」PDFの作成とアップロード
        global_result = None
        if global_files:
            global_files.sort(key=lambda x: (x[0], x[1]))
            global_merger = PdfWriter()
            for _, _, pdf_path in global_files:
                global_merger.append(str(pdf_path))
                
            global_output_name = f"{prefix}出力用.pdf"
            local_global_path = temp_path / global_output_name
            global_merger.write(str(local_global_path))
            global_merger.close()
            
            # Google Driveにアップロード
            drive.upload_file_from_path(local_global_path, folder_id=output_folder_id)
            
            global_result = {
                "output_file": global_output_name,
                "files_merged": len(global_files)
            }

        # 支払一覧の並び替え (会社コード昇順)
        payment_list.sort(key=lambda x: x["code"])

        # 7. 支払一覧表（CSV）の作成とアップロード
        csv_filename = f"{prefix}支払一覧.csv"
        local_csv_path = temp_path / csv_filename
        try:
            with open(local_csv_path, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["支払い先コード", "支払い先名", "支払総額", "消費税", "約束手形", "振込金額"])
                for item in payment_list:
                    writer.writerow([
                        item["code"],
                        item["name"],
                        item["total"],
                        item["tax"],
                        item["note"],
                        item["transfer"]
                    ])
            # Google Driveにアップロード
            drive.upload_file_from_path(local_csv_path, folder_id=output_folder_id)
        except Exception as e:
            logger.error(f"支払一覧CSVの作成失敗: {e}")

        # 8. 支払一覧表（PDF）の作成とアップロード
        pdf_report_filename = f"{prefix}支払一覧.pdf"
        local_pdf_report_path = temp_path / pdf_report_filename
        try:
            create_payment_report_pdf(payment_list, local_pdf_report_path, prefix)
            # Google Driveにアップロード
            drive.upload_file_from_path(local_pdf_report_path, folder_id=output_folder_id)
        except Exception as e:
            logger.error(f"支払一覧PDFの作成失敗: {e}")

    # 結果として成果物フォルダ名を表示する
    return processed_subfolders, global_result, output_dir_name, payment_list, csv_filename, pdf_report_filename

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        folder_id = request.form.get('folder_path', '').strip()
        
        # もし入力されたものがフルURL（https://drive.google.com/drive/folders/...）の場合は、末尾のIDを自動抽出
        if "folders/" in folder_id:
            match = re.search(r'folders/([a-zA-Z0-9-_]+)', folder_id)
            if match:
                folder_id = match.group(1)

        if not folder_id:
            flash("Google DriveのフォルダIDまたはURLを入力してください。", "warning")
            return render_template('index.html')
            
        try:
            results, global_result, output_dir, payment_list, csv_filename, pdf_report_filename = process_pdf_merger_gdrive(folder_id)
            return render_template(
                'index.html',
                success=True,
                results=results,
                global_result=global_result,
                output_dir=output_dir, # 成果物フォルダ名
                payment_list=payment_list,
                csv_filename=csv_filename,
                pdf_report_filename=pdf_report_filename,
                is_gdrive=True
            )
        except Exception as e:
            logger.exception("Google Drive PDF結合処理中にエラーが発生しました")
            flash(f"エラーが発生しました: {str(e)}", "danger")
            return render_template('index.html')

    return render_template('index.html')

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5005))
    app.run(debug=True, host='127.0.0.1', port=port)
