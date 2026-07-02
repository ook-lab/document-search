import os
import re
import json
import logging
import smtplib
import tempfile
import sys
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

def load_companies_gdrive(folder_id):
    """Google Driveから会社マスタを読み込む"""
    try:
        drive = GoogleDriveConnector()
        file_id = get_gdrive_file_id(drive, "dms_companies_master.json", folder_id)
        
        if file_id:
            with tempfile.TemporaryDirectory() as temp_dir:
                local_path = drive.download_file(file_id, "dms_companies_master.json", temp_dir)
                if local_path and Path(local_path).exists():
                    with open(local_path, "r", encoding="utf-8") as f:
                        return json.load(f)
    except Exception as e:
        logger.warning(f"Google Driveマスタのロード失敗 (ローカルにフォールバックします): {e}")
        
    return load_companies_local()

def save_companies_gdrive(folder_id, data):
    """Google Driveへ会社マスタを保存する"""
    try:
        drive = GoogleDriveConnector()
        file_id = get_gdrive_file_id(drive, "dms_companies_master.json", folder_id)
        
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir) / "dms_companies_master.json"
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                
            if file_id:
                # 既存ファイルがある場合は上書き更新 (これで容量制限403を回避！)
                success = drive.update_file_content(file_id, str(temp_path))
                if success:
                    return True
                logger.warning("既存マスタの上書き失敗。新規作成を試みます。")
                
            new_id = drive.upload_file_from_path(str(temp_path), folder_id=folder_id)
            if not new_id:
                raise RuntimeError("Google Driveへのマスタファイルの新規アップロードに失敗しました。")
        return True
    except Exception as e:
        logger.error(f"Google Driveへのマスタ保存失敗: {e}")
        raise e

def load_mail_template_gdrive(folder_id):
    """Google Driveからメールテンプレートを読み込む"""
    try:
        drive = GoogleDriveConnector()
        file_id = get_gdrive_file_id(drive, "dms_mail_template.json", folder_id)
        
        if file_id:
            with tempfile.TemporaryDirectory() as temp_dir:
                local_path = drive.download_file(file_id, "dms_mail_template.json", temp_dir)
                if local_path and Path(local_path).exists():
                    with open(local_path, "r", encoding="utf-8") as f:
                        return json.load(f)
    except Exception as e:
        logger.warning(f"Google Driveテンプレートのロード失敗 (ローカルにフォールバックします): {e}")
        
    return load_mail_template_local()

def save_mail_template_gdrive(folder_id, data):
    """Google Driveへメールテンプレートを保存する"""
    try:
        drive = GoogleDriveConnector()
        file_id = get_gdrive_file_id(drive, "dms_mail_template.json", folder_id)
        
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir) / "dms_mail_template.json"
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                
            if file_id:
                # 既存ファイルがある場合は上書き更新 (これで容量制限403を回避！)
                success = drive.update_file_content(file_id, str(temp_path))
                if success:
                    return True
                logger.warning("既存テンプレートの上書き失敗。新規作成を試みます。")
                
            new_id = drive.upload_file_from_path(str(temp_path), folder_id=folder_id)
            if not new_id:
                raise RuntimeError("Google Driveへのテンプレートファイルの新規アップロードに失敗しました。")
        return True
    except Exception as e:
        logger.error(f"Google Driveへのテンプレート保存失敗: {e}")
        raise e

# =============================================================================

from flask_wtf.csrf import CSRFProtect, generate_csrf

CSRFProtect(app)

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
        prefix = "注文明細書"

    types_mapping = {
        "（印刷）注文明細書_製作管理課": "印刷",
        "（加工）注文明細書_製作管理課": "加工",
        "（製本）注文明細書_製作管理課": "製本",
        "（印刷）注文明細書【単・宣・映・事】_製作管理課": "単独改装"
    }

    company_jobs = {}
    companies_master = load_companies_gdrive(folder_id)

    for pdf in pdf_files:
        filename = pdf["name"]
        job_type = None
        for key, val in types_mapping.items():
            if key in filename:
                job_type = val
                break
        if not job_type:
            continue

        local_path = drive.download_file(pdf["id"], filename, temp_dir)
        if not local_path:
            continue

        doc = fitz.open(local_path)
        for page_idx in range(len(doc)):
            page = doc[page_idx]
            code, name = extract_company_from_page(page)
            if not code:
                logger.warning(f"会社コードを抽出できませんでした: {filename} Page {page_idx + 1}")
                code = "unknown"
                name = "宛先不明"

            if code not in company_jobs:
                m_name = companies_master.get(code, {}).get("name")
                company_jobs[code] = {
                    "code": code,
                    "name": m_name or name,
                    "email": companies_master.get(code, {}).get("email") or "",
                    "jobs": {}
                }

            if job_type not in company_jobs[code]["jobs"]:
                company_jobs[code]["jobs"][job_type] = {
                    "source_path": str(local_path),
                    "pages": []
                }
            company_jobs[code]["jobs"][job_type]["pages"].append(page_idx)
        doc.close()

    return company_jobs, prefix

def create_split_pdfs_for_company(code, info, output_dir, prefix):
    """特定の会社向けに、仕分けられたページを結合して分割PDFファイルを作成する"""
    attachments = []
    
    reverse_types_mapping = {
        "印刷": "（印刷）注文明細書_製作管理課",
        "加工": "（加工）注文明細書_製作管理課",
        "製本": "（製本）注文明細書_製作管理課",
        "単独改装": "（印刷）注文明細書【単・宣・映・事】_製作管理課"
    }
    
    for job_type, job_info in info["jobs"].items():
        src_path = Path(job_info["source_path"])
        pages = job_info["pages"]
        
        writer = PdfWriter()
        reader = PdfReader(src_path)
        
        for p in pages:
            writer.add_page(reader.pages[p])
            
        detail_name = reverse_types_mapping.get(job_type, job_type)
        out_filename = f"{prefix}_{detail_name}({code}).pdf"
        out_path = Path(output_dir) / out_filename
        
        with open(out_path, "wb") as out_f:
            writer.write(out_f)
            
        attachments.append(out_path)
        
    return attachments

def send_smtp_email(to_email, subject, body, attachments, from_email, smtp_username, smtp_password):
    """SMTPを使用して添付ファイル付きのメールを送信する"""
    if not to_email:
        raise ValueError("宛先メールアドレスが設定されていません。")
        
    msg = MIMEMultipart()
    msg["From"] = from_email
    
    to_email_clean = to_email.replace(";", ",").strip()
    msg["To"] = to_email_clean
    msg["Bcc"] = "ookubo.y@workspace-o.com"
    msg["Subject"] = subject
    
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
    companies = load_companies_gdrive(folder_id)
    mail_template = load_mail_template_gdrive(folder_id)
    
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
                
    # マスタ表示用の会社リストを作成 (未登録優先でソート)
    display_companies = []
    for code, info in companies.items():
        display_companies.append({
            "code": code,
            "name": info.get("name", ""),
            "email": info.get("email", ""),
            "in_master": True,
            "detected": False
        })
        
    if preview_data:
        for code, info in preview_data.items():
            found = False
            for c in display_companies:
                if c["code"] == code:
                    c["detected"] = True
                    found = True
                    break
            if not found:
                display_companies.append({
                    "code": code,
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
        preview_data=preview_data,
        prefix=prefix,
        companies=companies,
        display_companies=display_companies,
        mail_template=mail_template
    )

@app.route("/api/save_master", methods=["POST"])
def api_save_master():
    """Webからマスタデータを保存するAPI"""
    req_data = request.get_json() or {}
    folder_url = req_data.get("folder_url", "").strip()
    
    if not folder_url:
        return jsonify({"success": False, "error": "フォルダURLが必要です"}), 400
        
    folder_id = extract_folder_id(folder_url)
    companies = load_companies_gdrive(folder_id)
    
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
        if save_companies_gdrive(folder_id, companies):
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
def api_save_template():
    """メールテンプレートを保存するAPI"""
    req_data = request.get_json() or {}
    folder_url = req_data.get("folder_url", "").strip()
    
    if not folder_url:
        return jsonify({"success": False, "error": "フォルダURLが必要です"}), 400
        
    folder_id = extract_folder_id(folder_url)
    subject = req_data.get("subject", "").strip()
    body = req_data.get("body", "").strip()
    
    if not subject or not body:
        return jsonify({"success": False, "error": "件名と本文が必要です"}), 400
        
    template = {"subject": subject, "body": body}
    try:
        if save_mail_template_gdrive(folder_id, template):
            return jsonify({"success": True})
    except Exception as e:
        error_msg = str(e)
        if "storageQuotaExceeded" in error_msg or "storage quota" in error_msg:
            return jsonify({
                "success": False,
                "error": "Google Driveの制限により、新規ファイルを作成できませんでした。\n\n対象のGoogle Driveフォルダ内に、ご自身のアカウントで空のテキストファイル「dms_mail_template.json」を新規作成（中身に {} とだけ入力して保存）してから、もう一度テンプレート保存を実行してください。"
            }), 403
        return jsonify({"success": False, "error": f"保存に失敗しました: {error_msg}"}), 500
    return jsonify({"success": False, "error": "Google Driveテンプレートの保存に失敗しました"}), 500

@app.route("/api/send_emails", methods=["POST"])
def api_send_emails():
    """選択された会社宛てにメールを一括送信するAPI"""
    req_data = request.get_json() or {}
    folder_url = req_data.get("folder_url", "").strip()
    selected_codes = req_data.get("codes", [])
    
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
            output_folder_id = None
            
            query = f"'{folder_id}' in parents and name = '送信済み明細_{prefix}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
            folders = drive.service.files().list(
                q=query,
                spaces='drive',
                fields='files(id, name)',
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
                corpora='allDrives'
            ).execute().get('files', [])
            
            if folders:
                output_folder_id = folders[0]['id']
            else:
                output_folder_id = drive.create_folder(f"送信済み明細_{prefix}", folder_id)
                
            if not output_folder_id:
                raise RuntimeError(f"Google Drive上にフォルダ '送信済み明細_{prefix}' を作成できませんでした。")

            mail_template = load_mail_template_gdrive(folder_id)
            success_count = 0
            errors = []

            for code in selected_codes:
                if code not in company_jobs:
                    logger.warning(f"会社コード {code} は解析結果に存在しないため送信をスキップします。")
                    continue
                    
                info = company_jobs[code]
                to_email = info["email"]
                
                if not to_email:
                    logger.warning(f"会社 {info['name']} ({code}) はメールアドレスが未設定のため送信をスキップします。")
                    errors.append(f"{info['name']} ({code}): メールアドレスが登録されていません。")
                    continue
                    
                try:
                    logger.info(f"メール送信処理開始: {info['name']} ({code}) -> 宛先: {to_email}")
                    attachments = create_split_pdfs_for_company(code, info, temp_dir, prefix)
                    
                    subject = mail_template["subject"].format(company_name=info["name"], company_code=code)
                    body = mail_template["body"].format(company_name=info["name"], company_code=code)
                    
                    send_smtp_email(to_email, subject, body, attachments, from_email, smtp_username, smtp_password)
                    logger.info(f"メール送信成功: {info['name']} ({code})")
                    
                    for att in attachments:
                        drive.upload_file_from_path(att, folder_id=output_folder_id)
                                
                    success_count += 1
                    
                except Exception as e:
                    logger.exception(f"メール送信エラー: {code}")
                    errors.append(f"{info['name']} ({code}): {str(e)}")

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
