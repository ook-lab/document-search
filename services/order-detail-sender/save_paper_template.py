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
    "subject": "月報（祥伝社）",
    "body": "各社担当者様\n\nお世話になります。\n納入月報を添付します。\nB４に 出力後、記入して{deadline_date}午前中までに提出をしてください。\n赤字記入後、スキャンしたものをメールで提出お願いいたします。\n\nよろしくお願いします。\n祥伝社　大久保"
}

print("Saving paper template to settings folder...")
result = save_mail_template_gdrive(template, doc_type="paper")
print(f"Result: {result}")
