import sys
from pathlib import Path

project_dir = Path("c:/Users/ookub/document-management-system")
sys.path.insert(0, str(project_dir))
sys.path.insert(0, str(project_dir / "services" / "order-detail-sender"))

try:
    from dotenv import load_dotenv
    load_dotenv(project_dir / ".env")
except Exception:
    pass

from app import save_mail_template_gdrive

template = {
    "subject": "付合せ明細書",
    "body": "各社担当者さま\n\nお世話になります。\n付け合わせ内容を記入の上、スキャンPDFで戻してください。\nよろしくお願いします。\n\n祥伝社　大久保"
}

print("Saving matching template to settings folder...")
result = save_mail_template_gdrive(template, doc_type="matching")
print(f"Result: {result}")
