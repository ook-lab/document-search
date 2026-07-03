import os
import re
import json
import logging
import smtplib
import tempfile
import sys
import time
import datetime
import html as html_module
from pathlib import Path
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from flask import Flask, render_template, request, flash, redirect, url_for, jsonify
import fitz  # PyMuPDF
from pypdf import PdfReader, PdfWriter

# Google Drive コネクタのインポート
sys.path.insert(0, str(Path(__file__).resolve().parent))
from google_drive_connector import GoogleDriveConnector

# ロギング設定
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "order-sender-secret-key-999")

# マスタファイルのローカルパス (フォールバック用)
COMPANIES_JSON_PATH = Path(__file__).resolve().parent / "companies.json"
MAIL_TEMPLATE_PATH = Path(__file__).resolve().parent / "mail_template.json"

# デフォルトのGoogle DriveフォルダURL
DEFAULT_FOLDER_URL = "https://drive.google.com/drive/u/0/folders/11kzVIXBGob4b-EALJtoXmG_KcOmasJje"

# メール定型文のデフォルト設定 (フォールバック用)
DEFAULT_MAIL_TEMPLATE = {
    "subject": "注文明細書（祥伝社)",
    "body": "各社担当者さま\n \nお世話になります。\n注文明細書をお送りします。\nよろしくお願いします。\n \n祥伝社　大久保"
}

def load_companies_local():
    """ローカルの会社マスタを読み込む (フォールバック用)"""
    if COMPANIES_JSON_PATH.exists():
        try:
            with open(COMPANIES_JSON_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"ローカルマスタ読み込みエラー: {e}")
    return {}

def load_mail_template_local():
    """ローカルのメールテンプレートを読み込む (フォールバック用)"""
    if MAIL_TEMPLATE_PATH.exists():
        try:
            with open(MAIL_TEMPLATE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"ローカルテンプレート読み込みエラー: {e}")
    return DEFAULT_MAIL_TEMPLATE.copy()


# =============================================================================
# Google Drive 永続化ストレージ用ヘルパー関数
# =============================================================================

# 設定用JSONファイルの保存先フォルダID
SETTINGS_FOLDER_ID = os.getenv("GOOGLE_DRIVE_SETTINGS_FOLDER_ID", "1_Xb-hH41MsQfVcNcqAEuHfwhKo96sY6y").strip()

# 送信済み原本PDFの移動先フォルダID（固定）
ARCHIVE_FOLDER_ID = os.getenv("GOOGLE_DRIVE_ARCHIVE_FOLDER_ID", "1o1XC76Icng5bEkdWBxrkjCjhCwiAARa7").strip()

def get_gdrive_file_id(drive, filename, folder_id):
    """Google Drive上の特定のファイル名に対するファイルIDを取得する"""
    try:
        query = f"'{folder_id}' in parents and name = '{filename}' and trashed = false"
        files = drive.service.files().list(
            q=query,
            spaces='drive',
            fields='files(id, name)',
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
            corpora='allDrives'
        ).execute().get('files', [])
        if files:
            return files[0]['id']
    except Exception as e:
        logger.error(f"Google DriveファイルID取得失敗 ({filename}): {e}")
    return None

def load_companies_gdrive():
    """設定フォルダ(SETTINGS_FOLDER_ID)から会社マスタを読み込む"""
    try:
        drive = GoogleDriveConnector()
        file_id = get_gdrive_file_id(drive, "dms_companies_master.json", SETTINGS_FOLDER_ID)
        if file_id:
            with tempfile.TemporaryDirectory() as temp_dir:
                local_path = drive.download_file(file_id, "dms_companies_master.json", temp_dir)
                if local_path and Path(local_path).exists():
                    with open(local_path, "r", encoding="utf-8") as f:
                        return json.load(f)
    except Exception as e:
        logger.warning(f"会社マスタのロード失敗: {e}")
    return load_companies_local()

def save_companies_gdrive(data):
    """設定フォルダ(SETTINGS_FOLDER_ID)へ会社マスタを保存する"""
    try:
        drive = GoogleDriveConnector()
        file_id = get_gdrive_file_id(drive, "dms_companies_master.json", SETTINGS_FOLDER_ID)
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir) / "dms_companies_master.json"
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            if file_id:
                success = drive.update_file_content(file_id, str(temp_path))
                if success:
                    return True
                logger.warning("既存マスタの上書き失敗。新規作成を試みます。")
            new_id = drive.upload_file_from_path(str(temp_path), folder_id=SETTINGS_FOLDER_ID)
            if not new_id:
                raise RuntimeError("会社マスタファイルのアップロードに失敗しました。")
        return True
    except Exception as e:
        logger.error(f"会社マスタ保存失敗: {e}")
        raise e

def load_mail_template_gdrive(doc_type="order"):
    """設定フォルダ(SETTINGS_FOLDER_ID)からメールテンプレートを読み込む"""
    filename_map = {
        "order": "dms_mail_template.json",
        "paper": "dms_mail_template_paper.json",
        "matching": "dms_mail_template_matching.json",
    }
    filename = filename_map.get(doc_type, "dms_mail_template.json")
    try:
        drive = GoogleDriveConnector()
        file_id = get_gdrive_file_id(drive, filename, SETTINGS_FOLDER_ID)
        if file_id:
            with tempfile.TemporaryDirectory() as temp_dir:
                local_path = drive.download_file(file_id, filename, temp_dir)
                if local_path and Path(local_path).exists():
                    with open(local_path, "r", encoding="utf-8") as f:
                        return json.load(f)
    except Exception as e:
        logger.warning(f"メールテンプレートのロード失敗 ({filename}): {e}")
    if doc_type == "paper":
        return {
            "subject": "月報（祥伝社）",
            "body": "各社担当者様\n\nお世話になります。\n納入月報を添付します。\nB４に 出力後、記入して{deadline_date}午前中までに提出をしてください。\n赤字記入後、スキャンしたものをメールで提出お願いいたします。\n\nよろしくお願いします。\n祥伝社　大久保"
        }
    elif doc_type == "matching":
        return {
            "subject": "付合せ明細書",
            "body": "各社担当者さま\n\nお世話になります。\n付け合わせ内容を記入の上、スキャンPDFで戻してください。\nよろしくお願いします。\n\n祥伝社　大久保"
        }
    return load_mail_template_local()

def save_mail_template_gdrive(data, doc_type="order"):
    """設定フォルダ(SETTINGS_FOLDER_ID)へメールテンプレートを保存する"""
    filename_map = {
        "order": "dms_mail_template.json",
        "paper": "dms_mail_template_paper.json",
        "matching": "dms_mail_template_matching.json",
    }
    filename = filename_map.get(doc_type, "dms_mail_template.json")
    try:
        drive = GoogleDriveConnector()
        file_id = get_gdrive_file_id(drive, filename, SETTINGS_FOLDER_ID)
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir) / filename
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            if file_id:
                success = drive.update_file_content(file_id, str(temp_path))
                if success:
                    return True
                logger.warning(f"既存テンプレート({filename})の上書き失敗。新規作成を試みます。")
            new_id = drive.upload_file_from_path(str(temp_path), folder_id=SETTINGS_FOLDER_ID)
            if not new_id:
                raise RuntimeError(f"テンプレートファイル({filename})のアップロードに失敗しました。")
        return True
    except Exception as e:
        logger.error(f"メールテンプレート保存失敗 ({filename}): {e}")
        raise e

# =============================================================================

def get_next_business_day_str():
    """中1日平日（翻日から最初の平日）の日付文字列を生成する"""
    WEEKDAYS_JP = ['月', '火', '水', '木', '金', '土', '日']
    candidate = datetime.date.today() + datetime.timedelta(days=1)
    while candidate.weekday() >= 5:  # 土(5)・日(6)はスキップ
        candidate += datetime.timedelta(days=1)
    return f"{candidate.month}月{candidate.day}日({WEEKDAYS_JP[candidate.weekday()]})"

from flask_wtf.csrf import CSRFProtect, generate_csrf

csrf = CSRFProtect(app)

@app.context_processor
def inject_csrf_token():
    return dict(csrf_token=generate_csrf)

def extract_folder_id(input_str):
    """入力文字列からGoogle DriveのフォルダIDを抽出する"""
    input_str = input_str.strip()
    if "folders/" in input_str:
        match = re.search(r"folders/([a-zA-Z0-9-_]+)", input_str)
        if match:
            return match.group(1)
    return input_str

def extract_company_from_page(page):
    """PDFページ右上から会社名とコードを抽出する"""
    rect = fitz.Rect(495, 45, 820, 82)
    text = page.get_text("text", clip=rect).strip()
    
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if not lines:
        full_text = page.get_text("text")
        match = re.search(r"([^\n|]+)\s*\|\s*(\d{4})\s+\d{4}", full_text)
        if match:
            return match.group(2).strip(), match.group(1).strip()
        return None, None

    combined_text = " ".join(lines)
    match = re.search(r"([^\n|]+)\s*\|\s*(\d{4})", combined_text)
    if match:
        name = match.group(1).strip()
        code = match.group(2).strip()
        
        name = re.sub(r"^[\d\s|]+", "", name)
        name = re.sub(r"\bM\d{2}\b", "", name)
        name = re.sub(r"[\d\s|]+$", "", name)
        name = re.sub(r"\s+", " ", name).strip()
        
        return code, name
        
    code_match = re.search(r"(\d{4})", combined_text)
    if code_match:
        code = code_match.group(1)
        name = combined_text.replace(code, "").replace("|", "").strip()
        
        name = re.sub(r"^[\d\s|]+", "", name)
        name = re.sub(r"\bM\d{2}\b", "", name)
        name = re.sub(r"[\d\s|]+$", "", name)
        name = re.sub(r"\s+", " ", name).strip()
        
        return code, name
        
    return None, None

def extract_paper_header(page):
    """用紙納入月報PDFページから宛先会社名とページ番号を抽出する"""
    # 会社名: Y [30, 50], X [700, 950]
    crop_rect_name = fitz.Rect(700, 30, 950, 50)
    name = page.get_text("text", clip=crop_rect_name).strip()
    name = name.replace("\n", "").strip()
    
    # ページ番号: Y [10, 25], X [930, 990]
    crop_rect_page = fitz.Rect(930, 10, 990, 25)
    page_text = page.get_text("text", clip=crop_rect_page).strip()
    page_text = page_text.replace("\n", "").strip()
    
    match = re.search(r"(\d+)\s*/\s*(\d+)", page_text)
    if match:
        cur_p = int(match.group(1))
        tot_p = int(match.group(2))
        return name, cur_p, tot_p
    return name, None, None

def extract_matching_header(page):
    """付合わせ明細書PDFページから会社コード、会社名、ページ番号を抽出する"""
    # 会社コード: Y [38, 55], X [590, 640]
    crop_rect_code = fitz.Rect(590, 38, 640, 55)
    code = page.get_text("text", clip=crop_rect_code).strip()
    code = code.replace("\n", "").strip()
    
    # 会社名: Y [38, 55], X [640, 760]
    crop_rect_name = fitz.Rect(640, 38, 760, 55)
    name = page.get_text("text", clip=crop_rect_name).strip()
    name = name.replace("\n", "").strip()
    
    # ページ番号: Y [30, 45], X [770, 810]
    crop_rect_page = fitz.Rect(770, 30, 810, 45)
    page_text = page.get_text("text", clip=crop_rect_page).strip()
    page_text = page_text.replace("\n", "").strip()
    
    match = re.search(r"(\d+)\s*/\s*(\d+)", page_text)
    if match:
        cur_p = int(match.group(1))
        tot_p = int(match.group(2))
        return code, name, cur_p, tot_p
    return code, name, None, None

def load_sent_status_gdrive():
    """設定フォルダ(SETTINGS_FOLDER_ID)から送信ステータスを読み込む"""
    filename = "dms_sent_status.json"
    try:
        drive = GoogleDriveConnector()
        file_id = get_gdrive_file_id(drive, filename, SETTINGS_FOLDER_ID)
        if file_id:
            with tempfile.TemporaryDirectory() as temp_dir:
                local_path = drive.download_file(file_id, filename, temp_dir)
                if local_path and Path(local_path).exists():
                    with open(local_path, "r", encoding="utf-8") as f:
                        return json.load(f)
    except Exception as e:
        logger.warning(f"送信ステータスのロード失敗 ({filename}): {e}")
    return {"sent_jobs": []}

def save_sent_status_gdrive(data):
    """設定フォルダ(SETTINGS_FOLDER_ID)へ送信ステータスを保存する"""
    filename = "dms_sent_status.json"
    try:
        drive = GoogleDriveConnector()
        file_id = get_gdrive_file_id(drive, filename, SETTINGS_FOLDER_ID)
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir) / filename
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            if file_id:
                success = drive.update_file_content(file_id, str(temp_path))
                if success:
                    return True
                logger.warning(f"既存送信ステータス({filename})の上書き失敗。新規作成を試みます。")
            new_id = drive.upload_file_from_path(str(temp_path), folder_id=SETTINGS_FOLDER_ID)
            if not new_id:
                raise RuntimeError(f"送信ステータスファイル({filename})のアップロードに失敗しました。")
        return True
    except Exception as e:
        logger.error(f"送信ステータス保存失敗 ({filename}): {e}")
        return False

def analyze_and_split_pdfs_gdrive(folder_id, temp_dir):
    """Google Drive上のフォルダ内のPDFファイルを解析・仕分けし、プレビューデータを生成する"""
    drive = GoogleDriveConnector()
    
    query = f"'{folder_id}' in parents and mimeType = 'application/pdf' and trashed = false"
    pdf_files = drive.service.files().list(
        q=query,
        spaces='drive',
        fields='files(id, name)',
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
        corpora='allDrives'
    ).execute().get('files', [])

    if not pdf_files:
        raise ValueError("指定されたフォルダ内にPDFファイルが見つかりませんでした。")
        
    prefix = ""
    for f in pdf_files:
        if "注文明細書" in f["name"]:
            match = re.match(r"^([^_]+_[^_]+)_", f["name"])
            if match:
                prefix = match.group(1)
                break
    if not prefix:
        for f in pdf_files:
            match = re.match(r"^(\d+年\d+月_[^_]+)_", f["name"])
            if match:
                prefix = match.group(1)
                break
    if not prefix:
        prefix = "明細書"

    # 注文明細書のサブタイプマッピング
    order_types_mapping = {
        "（印刷）注文明細書_製作管理課": "印刷",
        "（加工）注文明細書_製作管理課": "加工",
        "（製本）注文明細書_製作管理課": "製本",
        "（印刷）注文明細書【単・宣・映・事】_製作管理課": "単独改装"
    }

    company_jobs = {}
    companies_master = load_companies_gdrive()
    sent_status = load_sent_status_gdrive()
    sent_job_ids = {job["job_id"] for job in sent_status.get("sent_jobs", [])}

    for pdf in pdf_files:
        filename = pdf["name"]
        
        # ローカルにダウンロードして種類判定
        local_path = drive.download_file(pdf["id"], filename, temp_dir)
        if not local_path:
            continue

        doc = fitz.open(local_path)
        if len(doc) == 0:
            doc.close()
            continue

        # 1ページ目のテキストから書類種別を判定
        first_page_text = doc[0].get_text("text")
        
        doc_type = None
        if "用紙納入月報" in first_page_text or "用紙" in filename or "月報" in filename or "製紙会社" in first_page_text or "平巻" in first_page_text or "平判" in first_page_text or "銘柄" in first_page_text:
            doc_type = "paper"
        elif "付合わせ明細書" in first_page_text or "付合明細" in first_page_text or "付合わせ" in filename or "付合" in filename:
            doc_type = "matching"
        else:
            # 注文明細書の場合、ファイル名が合致するかチェック
            is_order = False
            for key in order_types_mapping.keys():
                if key in filename:
                    is_order = True
                    break
            if is_order or "注文明細" in filename:
                doc_type = "order"
                
        if not doc_type:
            # どの種類にも判定できなかったPDFはスキップ
            doc.close()
            continue

        # 各ページを解析して振り分ける
        for page_idx in range(len(doc)):
            page = doc[page_idx]
            
            code = ""
            name = ""
            job_type = "不明"

            if doc_type == "paper":
                name, cur_p, tot_p = extract_paper_header(page)
                if not name:
                    name = "用紙代理店不明"
                code = ""
                job_type = "用紙"
                job_id = f"{name}_paper"
                doc_type_name = "用紙納入月報"
            elif doc_type == "matching":
                code, name, cur_p, tot_p = extract_matching_header(page)
                if not code:
                    code = "unknown"
                if not name:
                    name = "付合わせ宛先不明"
                job_type = "付合わせ"
                job_id = f"{code}_matching"
                doc_type_name = "付合わせ明細書"
            else: # order
                code, name = extract_company_from_page(page)
                if not code:
                    code = "unknown"
                    name = "宛先不明"
                # サブタイプの判定
                job_type = "注文"
                for key, val in order_types_mapping.items():
                    if key in filename:
                        job_type = val
                        break
                job_id = f"{code}_order"
                doc_type_name = "注文明細書"

            # 送信リストへのマージ
            if job_id not in company_jobs:
                master_key = name if doc_type == "paper" else code
                master_info = companies_master.get(master_key, {})
                
                company_jobs[job_id] = {
                    "id": job_id,
                    "code": code,
                    "name": master_info.get("name") or name,
                    "email": master_info.get("email") or "",
                    "doc_type": doc_type,
                    "doc_type_name": doc_type_name,
                    "source_pdf_ids": [],
                    "source_pdf_names": [],
                    "sent": job_id in sent_job_ids,
                    "jobs": {}
                }

            if pdf["id"] not in company_jobs[job_id]["source_pdf_ids"]:
                company_jobs[job_id]["source_pdf_ids"].append(pdf["id"])
                company_jobs[job_id]["source_pdf_names"].append(filename)

            if job_type not in company_jobs[job_id]["jobs"]:
                company_jobs[job_id]["jobs"][job_type] = {
                    "source_path": str(local_path),
                    "pages": []
                }
            company_jobs[job_id]["jobs"][job_type]["pages"].append(page_idx)
            
        doc.close()

    return company_jobs, prefix

def create_split_pdfs_for_company(job_id, info, output_dir, prefix):
    """特定の送信ジョブ向けに、仕分けられたページを結合して分割PDFファイルを作成する"""
    attachments = []
    
    reverse_types_mapping = {
        "印刷": "（印刷）注文明細書_製作管理課",
        "加工": "（加工）注文明細書_製作管理課",
        "製本": "（製本）注文明細書_製作管理課",
        "単独改装": "（印刷）注文明細書【単・宣・映・事】_製作管理課",
        "用紙": "用紙納入月報",
        "付合わせ": "付合わせ明細書"
    }
    
    display_key = info["code"] if info["code"] and info["code"] != "unknown" else info["name"]
    
    for job_type, job_info in info["jobs"].items():
        src_path = Path(job_info["source_path"])
        pages = job_info["pages"]
        
        writer = PdfWriter()
        reader = PdfReader(src_path)
        
        for p in pages:
            writer.add_page(reader.pages[p])
            
        detail_name = reverse_types_mapping.get(job_type, job_type)
        out_filename = f"{prefix}_{detail_name}({display_key}).pdf"
        out_path = Path(output_dir) / out_filename
        
        with open(out_path, "wb") as out_f:
            writer.write(out_f)
            
        attachments.append(out_path)
        
    return attachments

def send_smtp_email(to_email, subject, body, attachments, from_email, smtp_username, smtp_password, html_body=None):
    """SMTPを使用して添付ファイル付きのメールを送信する。html_bodyを指定するとHTMLメールになる"""
    if not to_email:
        raise ValueError("宛先メールアドレスが設定されていません。")
        
    msg = MIMEMultipart('mixed')
    msg["From"] = from_email
    
    to_email_clean = to_email.replace(";", ",").strip()
    msg["To"] = to_email_clean
    msg["Bcc"] = "ookubo.y@workspace-o.com"
    msg["Subject"] = subject
    
    if html_body:
        # テキストとHTMLの両方を含む alternative パート
        alt = MIMEMultipart('alternative')
        alt.attach(MIMEText(body, 'plain', 'utf-8'))
        alt.attach(MIMEText(html_body, 'html', 'utf-8'))
        msg.attach(alt)
    else:
        msg.attach(MIMEText(body, "plain", "utf-8"))
    
    for file_path in attachments:
        path = Path(file_path)
        part = MIMEBase("application", "pdf")
        with open(path, "rb") as f:
            part.set_payload(f.read())
        encoders.encode_base64(part)
        
        from email.header import Header
        filename_header = Header(path.name, 'utf-8').encode()
        
        part.add_header(
            "Content-Disposition",
            "attachment",
            filename=filename_header
        )
        msg.attach(part)
        
    smtp_host = "smtp.gmail.com"
    smtp_port = 465 # SSL
    
    try:
        with smtplib.SMTP_SSL(smtp_host, smtp_port) as server:
            server.login(smtp_username, smtp_password)
            server.send_message(msg)
        return True
    except Exception as e:
        logger.error(f"メール送信エラー ({to_email_clean}): {e}")
        raise e

@app.route("/", methods=["GET", "POST"])
def index():
    # セッションまたはフォームからフォルダURLを維持
    folder_url = request.form.get("folder_url", "").strip() or request.args.get("folder_url", "").strip()
    if not folder_url:
        folder_url = DEFAULT_FOLDER_URL
        
    folder_id = extract_folder_id(folder_url)
    
    # Google Drive 側の永続ファイルからデータを取得
    companies = load_companies_gdrive()
    
    # 3つのテンプレートをそれぞれ取得
    mail_template_order = load_mail_template_gdrive(doc_type="order")
    mail_template_paper = load_mail_template_gdrive(doc_type="paper")
    mail_template_matching = load_mail_template_gdrive(doc_type="matching")
    
    preview_data = None
    prefix = ""
    
    if request.method == "POST" and "analyze" in request.form:
        if not folder_url:
            flash("Google DriveのフォルダURLまたはIDを指定してください。", "warning")
        else:
            try:
                # 一時フォルダ作成してPDFをスキャン
                temp_dir = tempfile.mkdtemp()
                preview_data, prefix = analyze_and_split_pdfs_gdrive(folder_id, temp_dir)
            except Exception as e:
                logger.exception("PDF解析エラー")
                flash(f"PDFの解析中にエラーが発生しました: {str(e)}", "danger")
                
    # 宛先マスタ一覧テーブル表示用の会社リストを作成 (未登録優先でソート)
    display_companies = []
    for code, info in companies.items():
        display_companies.append({
            "code": code,
            "name": info.get("name", ""),
            "email": info.get("email", ""),
            "in_master": True,
            "detected": False
        })
        
    # スキャンで検出された宛先をマスタテーブルにマージ表示
    # (ただし、今回はキーが name である用紙納入月報と、code である注文/付合わせが混在する)
    if preview_data:
        for job_id, info in preview_data.items():
            doc_type = info["doc_type"]
            master_key = info["name"] if doc_type == "paper" else info["code"]
            
            found = False
            for c in display_companies:
                # マスタキーが一致するかチェック
                if doc_type == "paper" and c["name"] == master_key:
                    c["detected"] = True
                    found = True
                    break
                elif doc_type != "paper" and c["code"] == master_key:
                    c["detected"] = True
                    found = True
                    break
                    
            if not found:
                display_companies.append({
                    "code": info["code"],
                    "name": info["name"],
                    "email": "",
                    "in_master": False,
                    "detected": True
                })
                
    def sort_key(c):
        if c["detected"] and not c["email"]:
            return 0
        if c["detected"]:
            return 1
        return 2
        
    display_companies.sort(key=sort_key)

    return render_template(
        "index.html",
        folder_url=folder_url,
        preview_data=preview_data, # 送信ジョブの一覧
        prefix=prefix,
        companies=companies,
        display_companies=display_companies,
        mail_template_order=mail_template_order,
        mail_template_paper=mail_template_paper,
        mail_template_matching=mail_template_matching
    )

@app.route("/api/save_master", methods=["POST"])
@csrf.exempt
def api_save_master():
    """Webからマスタデータを保存するAPI"""
    req_data = request.get_json() or {}
    folder_url = req_data.get("folder_url", "").strip()
    
    if not folder_url:
        return jsonify({"success": False, "error": "フォルダURLが必要です"}), 400
        
    folder_id = extract_folder_id(folder_url)
    companies = load_companies_gdrive()
    
    code = req_data.get("code")
    email = req_data.get("email", "").strip()
    name = req_data.get("name", "").strip()
    
    if not code:
        return jsonify({"success": False, "error": "コードが必要です"}), 400
        
    if code not in companies:
        companies[code] = {}
        
    if name:
        companies[code]["name"] = name
    companies[code]["email"] = email
    
    try:
        if save_companies_gdrive(companies):
            return jsonify({"success": True})
    except Exception as e:
        error_msg = str(e)
        if "storageQuotaExceeded" in error_msg or "storage quota" in error_msg:
            return jsonify({
                "success": False,
                "error": "Google Driveの制限により、新規ファイルを作成できませんでした。\n\n対象のGoogle Driveフォルダ内に、ご自身のアカウントで空のテキストファイル「dms_companies_master.json」を新規作成（中身に {} とだけ入力して保存）してから、もう一度マスタ保存を実行してください。"
            }), 403
        return jsonify({"success": False, "error": f"保存に失敗しました: {error_msg}"}), 500
    return jsonify({"success": False, "error": "Google Driveマスタの保存に失敗しました"}), 500

@app.route("/api/save_template", methods=["POST"])
@csrf.exempt
def api_save_template():
    """メールテンプレートを保存するAPI"""
    req_data = request.get_json() or {}
    folder_url = req_data.get("folder_url", "").strip()
    doc_type = req_data.get("doc_type", "order").strip()
    
    if not folder_url:
        return jsonify({"success": False, "error": "フォルダURLが必要です"}), 400
        
    folder_id = extract_folder_id(folder_url)
    subject = req_data.get("subject", "").strip()
    body = req_data.get("body", "").strip()
    
    if not subject or not body:
        return jsonify({"success": False, "error": "件名と本文が必要です"}), 400
        
    template = {"subject": subject, "body": body}
    filename = "dms_mail_template.json"
    if doc_type == "paper":
        filename = "dms_mail_template_paper.json"
    elif doc_type == "matching":
        filename = "dms_mail_template_matching.json"

    try:
        if save_mail_template_gdrive(template, doc_type=doc_type):
            return jsonify({"success": True})
    except Exception as e:
        error_msg = str(e)
        if "storageQuotaExceeded" in error_msg or "storage quota" in error_msg:
            return jsonify({
                "success": False,
                "error": f"Google Driveの制限により、新規ファイルを作成できませんでした。\n\n対象のGoogle Driveフォルダ内に、ご自身のアカウントで空のテキストファイル「{filename}」を新規作成（中身に {{}} とだけ入力して保存）してから、もう一度テンプレート保存を実行してください。"
            }), 403
        return jsonify({"success": False, "error": f"保存に失敗しました: {error_msg}"}), 500
    return jsonify({"success": False, "error": "Google Driveテンプレートの保存に失敗しました"}), 500

@app.route("/api/send_emails", methods=["POST"])
@csrf.exempt
def api_send_emails():
    """選択された会社宛てにメールを一括送信するAPI"""
    req_data = request.get_json() or {}
    folder_url = req_data.get("folder_url", "").strip()
    selected_codes = req_data.get("codes", []) # 送信対象の job_id リリスト
    
    from_email = "ookubo@shodensha.co.jp"
    smtp_username = "ookubo.shodensha@gmail.com"
    smtp_password = os.environ.get("GMAIL_SMTP_PASSWORD", "").strip()
    
    if not smtp_password:
        load_dotenv_from_root()
        smtp_password = os.environ.get("GMAIL_SMTP_PASSWORD", "").strip()

    if not smtp_password:
        return jsonify({"success": False, "error": "環境変数 GMAIL_SMTP_PASSWORD が設定されていません。.env ファイルを確認してください。"}), 500
        
    if not folder_url or not selected_codes:
        return jsonify({"success": False, "error": "パラメータが不足しています"}), 400

    folder_id = extract_folder_id(folder_url)

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            company_jobs, prefix = analyze_and_split_pdfs_gdrive(folder_id, temp_dir)
            
            drive = GoogleDriveConnector()
            archive_folder_id = ARCHIVE_FOLDER_ID  # 固定の送信済みアーカイブフォルダ

            # 送信ステータスのロード
            sent_status = load_sent_status_gdrive()
            sent_job_ids = {job["job_id"] for job in sent_status.get("sent_jobs", [])}
            
            success_count = 0
            errors = []

            for job_id in selected_codes:
                if job_id not in company_jobs:
                    logger.warning(f"ジョブID {job_id} は解析結果に存在しないため送信をスキップします。")
                    continue
                    
                info = company_jobs[job_id]
                to_email = info["email"]
                doc_type = info["doc_type"]
                
                if not to_email:
                    logger.warning(f"会社 {info['name']} ({job_id}) はメールアドレスが未設定のため送信をスキップします。")
                    errors.append(f"{info['name']} ({info['doc_type_name']}): メールアドレスが登録されていません。")
                    continue
                    
                try:
                    logger.info(f"メール送信処理開始: {info['name']} ({job_id}) -> 宛先: {to_email}")
                    attachments = create_split_pdfs_for_company(job_id, info, temp_dir, prefix)
                    
                    # 書類種別ごとのテンプレート読み込み
                    mail_template = load_mail_template_gdrive(doc_type=doc_type)
                    
                    display_key = info["code"] if info["code"] and info["code"] != "unknown" else info["name"]
                    
                    # 用紙納入月報の場合、中1日平日の日付を自動生成してHTML本文を構築
                    html_body = None
                    if doc_type == "paper":
                        deadline_date = get_next_business_day_str()
                        plain_body_tpl = mail_template["body"].format(
                            company_name=info["name"],
                            company_code=display_key,
                            deadline_date=deadline_date
                        )
                        # HTML版: deadline_date を赤太字に
                        escaped = html_module.escape(plain_body_tpl)
                        html_colored = escaped.replace(
                            html_module.escape(deadline_date),
                            f'<span style="color:red;font-weight:bold;">{html_module.escape(deadline_date)}</span>'
                        ).replace('\n', '<br>')
                        html_body = f"<html><body style='font-family:sans-serif;'>{html_colored}</body></html>"
                        body = plain_body_tpl
                        subject = mail_template["subject"].format(company_name=info["name"], company_code=display_key)
                    else:
                        subject = mail_template["subject"].format(company_name=info["name"], company_code=display_key)
                        body = mail_template["body"].format(company_name=info["name"], company_code=display_key)
                    
                    # SMTPメール送信
                    send_smtp_email(to_email, subject, body, attachments, from_email, smtp_username, smtp_password, html_body=html_body)
                    logger.info(f"メール送信成功: {job_id}")
                                
                    # 送信ステータスに記録
                    if job_id not in sent_job_ids:
                        sent_status["sent_jobs"].append({
                            "job_id": job_id,
                            "source_pdf_ids": info["source_pdf_ids"],
                            "sent_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                        })
                        sent_job_ids.add(job_id)
                        
                    success_count += 1
                    
                except Exception as e:
                    logger.exception(f"メール送信エラー: {job_id}")
                    errors.append(f"{info['name']} ({info['doc_type_name']}): {str(e)}")

            # 送信ステータスを保存
            save_sent_status_gdrive(sent_status)

            # --- PDFファイルの自動アーカイブ移動判定 ---
            # 各PDFファイルに含まれるジョブ一覧を整理
            pdf_id_to_jobs = {}
            for j_id, j_info in company_jobs.items():
                for pdf_id in j_info["source_pdf_ids"]:
                    if pdf_id not in pdf_id_to_jobs:
                        pdf_id_to_jobs[pdf_id] = []
                    pdf_id_to_jobs[pdf_id].append(j_id)

            moved_pdfs = []
            for pdf_id, related_jobs in pdf_id_to_jobs.items():
                # このPDFに関連するジョブが全て送信済み（sent_job_idsに含まれている）か判定
                all_sent = all(j_id in sent_job_ids for j_id in related_jobs)
                if all_sent:
                    try:
                        # Google Drive上でファイルを固定の送信済みアーカイブフォルダへ移動
                        file_info = drive.service.files().get(
                            fileId=pdf_id,
                            fields='name, parents',
                            supportsAllDrives=True
                        ).execute()
                        
                        previous_parents = ",".join(file_info.get('parents', []))
                        
                        drive.service.files().update(
                            fileId=pdf_id,
                            addParents=archive_folder_id,
                            removeParents=previous_parents,
                            fields='id, parents',
                            supportsAllDrives=True
                        ).execute()
                        
                        logger.info(f"PDFファイルを移動しました: {file_info.get('name')} (ID: {pdf_id})")
                        moved_pdfs.append(pdf_id)
                    except Exception as move_err:
                        logger.exception(f"PDFファイルの移動エラー (ID: {pdf_id}): {move_err}")

            # 移動完了したPDFのステータス履歴をクリーンアップ
            if moved_pdfs:
                new_sent_jobs = []
                for job in sent_status.get("sent_jobs", []):
                    remaining_pdfs = [pid for pid in job.get("source_pdf_ids", []) if pid not in moved_pdfs]
                    if remaining_pdfs:
                        job["source_pdf_ids"] = remaining_pdfs
                        new_sent_jobs.append(job)
                sent_status["sent_jobs"] = new_sent_jobs
                save_sent_status_gdrive(sent_status)

        return jsonify({
            "success": len(errors) == 0,
            "success_count": success_count,
            "errors": errors
        })
        
    except Exception as e:
        logger.exception("送信バッチエラー")
        return jsonify({"success": False, "error": f"送信バッチ処理中にエラーが発生しました: {str(e)}"}), 500

def load_dotenv_from_root():
    """ルートディレクトリの.envから環境変数を読み込む"""
    try:
        from dotenv import load_dotenv
        root_path = Path(__file__).resolve().parents[2] / ".env"
        if root_path.exists():
            load_dotenv(root_path)
    except Exception:
        pass

if __name__ == "__main__":
    load_dotenv_from_root()
    port = int(os.environ.get("PORT", 5006))
    app.run(debug=True, host="127.0.0.1", port=port)
