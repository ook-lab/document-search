"""Unified Gmail processing: fetch, OCR, markdown, chunk, embed, analyze.

All gmail-service logic in one module:
  - fetch_emails(): Gmail API -> 01_gmail_01_raw
  - process_emails(): OCR -> MD -> 09_unified -> chunk -> 10_ix_search_index
  - analyze_emails(): Gemini expiry detection
  - delete_emails(): 10_ix + 09_unified + pipeline_meta + 01_raw cascade
"""
from __future__ import annotations

import io
import os
import re
import sys
import json
import logging
from datetime import datetime, timezone
from email.utils import parseaddr, parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Optional

_repo = Path(__file__).resolve().parents[2]
if str(_repo) not in sys.path:
    sys.path.insert(0, str(_repo))

from shared.common.database.client import DatabaseClient

logger = logging.getLogger(__name__)

_IMAGE_MIMES = frozenset({
    "image/png", "image/jpeg", "image/jpg", "image/webp",
    "image/gif", "image/bmp", "image/tiff",
})


# ===================================================================
# Helpers
# ===================================================================

def _gemini_client():
    from google import genai
    key = os.environ.get("GOOGLE_AI_API_KEY")
    if not key:
        raise RuntimeError("GOOGLE_AI_API_KEY not set")
    return genai.Client(api_key=key)


def _ocr_image_bytes(client, image_bytes: bytes, mime_type: str) -> str:
    from google.genai import types
    part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
    resp = client.models.generate_content(
        model="gemini-2.5-flash-lite",
        contents=[
            "Extract all text from this image. Return only the text, "
            "preserving line breaks. Return empty string if no text.",
            part,
        ],
    )
    return (resp.text or "").strip()


def _download_drive_bytes(file_id: str) -> tuple[bytes, str]:
    from shared.common.connectors.google_drive import GoogleDriveConnector
    from googleapiclient.http import MediaIoBaseDownload
    drive = GoogleDriveConnector()
    meta = drive.service.files().get(
        fileId=file_id, fields="mimeType", supportsAllDrives=True,
    ).execute()
    mime = meta.get("mimeType", "application/octet-stream")
    req = drive.service.files().get_media(fileId=file_id, supportsAllDrives=True)
    buf = io.BytesIO()
    dl = MediaIoBaseDownload(buf, req)
    done = False
    while not done:
        _, done = dl.next_chunk()
    return buf.getvalue(), mime


class _StripHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self._p: list[str] = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("style", "script"):
            self._skip = True

    def handle_endtag(self, tag):
        if tag in ("style", "script"):
            self._skip = False
        if tag in ("p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4"):
            self._p.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self._p.append(data)

    def text(self) -> str:
        return re.sub(r"\n{3,}", "\n\n", "".join(self._p)).strip()


def _html_to_text(html: str) -> str:
    h = _StripHTML()
    h.feed(html)
    return h.text()


def _build_markdown(email: dict, ocr_results: list[dict]) -> str:
    subj = email.get("header_subject") or "(件名なし)"
    fn = email.get("from_name") or ""
    fe = email.get("from_email") or ""
    to = email.get("header_to") or ""
    tid = email.get("thread_id", "")
    sent = email.get("sent_at")
    ds = ""
    if sent:
        try:
            dt = datetime.fromisoformat(str(sent).replace("Z", "+00:00"))
            ds = dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            ds = str(sent)
    gmail_url = f"https://mail.google.com/mail/u/0/#all/{tid}" if tid else ""
    body = email.get("body_plain") or ""
    if not body and email.get("body_html"):
        body = _html_to_text(email["body_html"])

    L: list[str] = [f"# {subj}\n"]
    L.append("| Field | Value |")
    L.append("|-------|-------|")
    if fn or fe:
        sender = f"{fn} <{fe}>" if fn and fn != fe else (fe or fn)
        L.append(f"| From | {sender} |")
    if to:
        L.append(f"| To | {to} |")
    if ds:
        L.append(f"| Date | {ds} |")
    if gmail_url:
        L.append(f"| Gmail | {gmail_url} |")
    L += ["", "---\n"]
    if body:
        L += [body, ""]
    for ocr in ocr_results:
        t = ocr.get("text", "")
        if t:
            L.append(f"> **{ocr.get('filename','image')}** (OCR)\n>")
            for ln in t.split("\n"):
                L.append(f"> {ln}")
            L.append("")
    if gmail_url:
        L += ["---", f"*Source: {gmail_url}*"]
    return "\n".join(L)


def _chunk(text: str, size: int = 800, overlap: int = 100) -> list[str]:
    if len(text) <= size:
        return [text]
    out: list[str] = []
    s = 0
    while s < len(text):
        e = s + size
        c = text[s:e]
        if e < len(text):
            nl = c.rfind("\n")
            if nl > size // 2:
                e = s + nl + 1
                c = text[s:e]
        out.append(c.strip())
        s = e - overlap
        if s >= len(text):
            break
    return [x for x in out if x]


def _embed(chunks: list[str]) -> list[list[float]]:
    from openai import OpenAI
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY not set")
    r = OpenAI(api_key=key).embeddings.create(model="text-embedding-3-small", input=chunks)
    return [d.embedding for d in r.data]


# ===================================================================
# Main class
# ===================================================================

class GmailService:
    def __init__(self):
        self.db = DatabaseClient(use_service_role=True)

    # ---------------------------------------------------------------
    # 1. Fetch: Gmail API -> 01_gmail_01_raw
    # ---------------------------------------------------------------
    def fetch(self, mail_type: str = "DM", query: str | None = None,
              max_results: int = 50) -> dict:
        from shared.common.connectors.gmail_connector import GmailConnector
        from shared.common.connectors.google_drive import GoogleDriveConnector

        mt = mail_type.upper()
        user_email = os.getenv(f"GMAIL_{mt}_USER_EMAIL") or os.getenv("GMAIL_USER_EMAIL")
        if not user_email:
            raise ValueError(f"GMAIL_{mt}_USER_EMAIL not set")
        owner_id = os.getenv("SUPABASE_ADMIN_USER_ID")
        att_folder = os.getenv(f"GMAIL_{mt}_ATTACHMENT_FOLDER_ID")
        email_folder = os.getenv(f"GMAIL_{mt}_EMAIL_FOLDER_ID")

        gmail = GmailConnector(user_email=user_email)
        drive = GoogleDriveConnector()

        q = query or f"label:{mt}"
        msgs = gmail.list_messages(query=q, max_results=max_results)
        if not msgs:
            return {"fetched": 0, "skipped": 0}

        existing = self.db.client.table("01_gmail_01_raw").select("message_id").execute()
        existing_ids = {r["message_id"] for r in (existing.data or []) if r.get("message_id")}
        new_msgs = [m for m in msgs if m["id"] not in existing_ids]

        fetched = 0
        for msg_ref in new_msgs:
            try:
                self._ingest_one(gmail, drive, msg_ref["id"],
                                 att_folder, email_folder, owner_id, mt)
                fetched += 1
            except Exception as e:
                logger.error("ingest %s failed: %s", msg_ref["id"], e)

        return {"fetched": fetched, "skipped": len(msgs) - len(new_msgs),
                "total": len(msgs)}

    def _ingest_one(self, gmail, drive, message_id: str,
                    att_folder, email_folder, owner_id, mail_type):
        msg = gmail.get_message(message_id, format="full")
        headers = gmail.parse_message_headers(msg)
        parts = gmail.extract_message_parts(msg)

        subj = headers.get("subject", "")
        from_raw = headers.get("from", "")
        sender_name, sender_email = parseaddr(from_raw)
        if not sender_name:
            sender_name = sender_email

        text_plain = parts.get("text_plain", "")
        text_html = parts.get("text_html", "")
        attachments = parts.get("attachments", [])

        if text_html and attachments:
            text_html = gmail.convert_html_with_inline_images(
                message_id, text_html, attachments)

        sent_at = None
        if headers.get("date"):
            try:
                sent_at = parsedate_to_datetime(headers["date"]).isoformat()
            except Exception:
                pass

        if not text_plain and text_html:
            text_plain = _html_to_text(text_html)

        att_list = []
        exts = {".pdf",".doc",".docx",".xls",".xlsx",".png",".jpg",".jpeg",".gif",".webp"}
        for att in attachments:
            fn = att.get("filename", "")
            aid = att.get("attachmentId")
            ext = Path(fn).suffix.lower()
            if ext not in exts or not aid:
                continue
            data = gmail.get_attachment(message_id, aid)
            if not data:
                continue
            mime_map = {".pdf":"application/pdf",".png":"image/png",
                        ".jpg":"image/jpeg",".jpeg":"image/jpeg",
                        ".gif":"image/gif",".webp":"image/webp"}
            fid = drive.upload_file(
                file_content=data, file_name=fn,
                mime_type=mime_map.get(ext, "application/octet-stream"),
                folder_id=att_folder)
            if fid:
                att_list.append({"filename":fn,"drive_file_id":fid,
                                 "size":att.get("size",0),
                                 "mime_type":att.get("mimeType","")})

        raw_row = {
            "person": "宜紀",
            "source": "Gmail",
            "category": mail_type,
            "message_id": message_id,
            "thread_id": msg.get("threadId", ""),
            "sent_at": sent_at,
            "header_subject": subj,
            "from_name": sender_name,
            "from_email": sender_email,
            "header_to": headers.get("to", ""),
            "body_plain": text_plain,
            "body_html": text_html,
            "snippet": (text_plain or "")[:500],
            "attachments": att_list or None,
        }
        res = self.db.client.table("01_gmail_01_raw").insert(raw_row).execute()
        raw_id = res.data[0]["id"] if res.data else None
        if raw_id and owner_id:
            self.db.client.table("pipeline_meta").insert({
                "raw_id": raw_id, "raw_table": "01_gmail_01_raw",
                "person": "宜紀", "source": "Gmail",
                "processing_status": "pending", "owner_id": owner_id,
            }).execute()

    # ---------------------------------------------------------------
    # 2. Process: OCR -> MD -> 09 -> chunk -> 10_ix
    # ---------------------------------------------------------------
    def process(self, raw_ids: list[str]) -> list[dict]:
        results = []
        for rid in raw_ids:
            try:
                r = self._process_one(rid)
                results.append(r)
            except Exception as e:
                logger.exception("process %s", rid)
                self._set_status(rid, "error", str(e))
                results.append({"raw_id": rid, "success": False, "error": str(e)})
        return results

    def _process_one(self, raw_id: str) -> dict:
        self._set_status(raw_id, "processing")
        row = (self.db.client.table("01_gmail_01_raw")
               .select("*").eq("id", raw_id).maybe_single().execute())
        if not row or not row.data:
            raise ValueError(f"{raw_id} not found")
        email = row.data
        ocr = self._ocr_attachments(email.get("attachments") or [])
        md = _build_markdown(email, ocr)
        doc_id = self._upsert_unified(raw_id, email, md)
        self._chunk_and_embed(doc_id, md, email)
        self._set_status(raw_id, "completed")
        return {"raw_id": raw_id, "doc_id": str(doc_id), "success": True}

    def _ocr_attachments(self, attachments: list[dict]) -> list[dict]:
        results = []
        client = None
        for att in attachments:
            mime = (att.get("mime_type") or "").lower()
            if mime not in _IMAGE_MIMES:
                continue
            fid = att.get("drive_file_id")
            if not fid:
                continue
            try:
                if client is None:
                    client = _gemini_client()
                img, actual = _download_drive_bytes(fid)
                text = _ocr_image_bytes(client, img, actual)
                if text:
                    results.append({"filename": att.get("filename","image"), "text": text})
            except Exception as e:
                logger.warning("OCR %s: %s", att.get("filename"), e)
        return results

    def _upsert_unified(self, raw_id: str, email: dict, md: str) -> str:
        row = {
            "raw_id": raw_id, "raw_table": "01_gmail_01_raw",
            "person": email.get("person"), "source": email.get("source"),
            "category": email.get("category"),
            "title": email.get("header_subject"),
            "from_email": email.get("from_email"),
            "from_name": email.get("from_name"),
            "snippet": (email.get("snippet") or "")[:500],
            "post_at": email.get("sent_at"),
            "body": md,
            "indexed_at": datetime.now(timezone.utc).isoformat(),
        }
        ex = (self.db.client.table("09_unified_documents")
              .select("id").eq("raw_id", raw_id)
              .eq("raw_table", "01_gmail_01_raw").maybe_single().execute())
        if ex and ex.data:
            did = ex.data["id"]
            self.db.client.table("09_unified_documents").update(row).eq("id", did).execute()
        else:
            r = self.db.client.table("09_unified_documents").insert(row).execute()
            did = r.data[0]["id"]
        return did

    def _chunk_and_embed(self, doc_id: str, md: str, email: dict):
        self.db.client.table("10_ix_search_index").delete().eq("doc_id", doc_id).execute()
        chunks = _chunk(md)
        if not chunks:
            return
        embs = _embed(chunks)
        rows = []
        for i, (c, e) in enumerate(zip(chunks, embs)):
            rows.append({
                "doc_id": doc_id,
                "person": email.get("person"),
                "source": email.get("source"),
                "category": email.get("category"),
                "chunk_index": i, "chunk_text": c,
                "chunk_type": "email_content", "chunk_weight": 1.0,
                "embedding": "[" + ",".join(str(v) for v in e) + "]",
            })
        for i in range(0, len(rows), 20):
            self.db.client.table("10_ix_search_index").insert(rows[i:i+20]).execute()

    # ---------------------------------------------------------------
    # 3. Analyze: Gemini expiry detection
    # ---------------------------------------------------------------
    def analyze(self, raw_ids: list[str]) -> list[dict]:
        mails = (self.db.client.table("01_gmail_01_raw")
                 .select("id, header_subject, sent_at, snippet, body_plain, from_name")
                 .in_("id", raw_ids).execute()).data or []
        if not mails:
            return []

        client = _gemini_client()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        all_results: list[dict] = []

        for i in range(0, len(mails), 20):
            batch = mails[i:i+20]
            lines = []
            for m in batch:
                body = (m.get("body_plain") or m.get("snippet") or "")[:400]
                lines.append(
                    f'ID:{m["id"]}\n'
                    f'件名:{m.get("header_subject","")}\n'
                    f'送信日:{m.get("sent_at","")}\n'
                    f'送信者:{m.get("from_name","")}\n'
                    f'本文冒頭:{body}'
                )
            prompt = (
                f"今日は {today} です。\n"
                "以下のメールで、セール・イベント・キャンペーン等の期間が過ぎているものを判定。\n"
                "期間情報なし(請求書等)はexpired=false。\n"
                'JSON配列のみ返答: [{{"id":"...","expired":true,"reason":"..."}}]\n\n'
                + "\n===\n".join(lines)
            )
            resp = client.models.generate_content(
                model="gemini-2.5-flash-lite", contents=prompt)
            raw = (resp.text or "").strip()
            if "```json" in raw:
                raw = raw[raw.find("```json")+7:raw.rfind("```")].strip()
            elif "```" in raw:
                raw = raw[raw.find("```")+3:raw.rfind("```")].strip()
            try:
                all_results.extend(json.loads(raw))
            except json.JSONDecodeError:
                logger.warning("Gemini JSON parse failed: %s", raw[:200])

        return all_results

    # ---------------------------------------------------------------
    # 4. Delete: cascade 10_ix -> 09 -> pipeline_meta -> 01_raw
    # ---------------------------------------------------------------
    def delete(self, raw_ids: list[str]) -> dict:
        deleted = 0
        errors = []
        for rid in raw_ids:
            try:
                unified = (self.db.client.table("09_unified_documents")
                           .select("id").eq("raw_id", rid)
                           .eq("raw_table", "01_gmail_01_raw").execute())
                for doc in (unified.data or []):
                    self.db.client.table("10_ix_search_index").delete().eq("doc_id", doc["id"]).execute()
                    self.db.client.table("09_unified_documents").delete().eq("id", doc["id"]).execute()
                self.db.client.table("pipeline_meta").delete().eq(
                    "raw_id", rid).eq("raw_table", "01_gmail_01_raw").execute()
                self.db.client.table("01_gmail_01_raw").delete().eq("id", rid).execute()
                deleted += 1
            except Exception as e:
                errors.append({"id": rid, "error": str(e)})
        return {"deleted": deleted, "errors": errors}

    # ---------------------------------------------------------------
    # Helpers
    # ---------------------------------------------------------------
    def _set_status(self, raw_id: str, status: str, error: str | None = None):
        u: dict[str, Any] = {"processing_status": status}
        if status == "processing":
            u["started_at"] = datetime.now(timezone.utc).isoformat()
        elif status == "completed":
            u["completed_at"] = datetime.now(timezone.utc).isoformat()
        elif status == "error":
            u["error_message"] = (error or "")[:2000]
            u["failed_at"] = datetime.now(timezone.utc).isoformat()
        try:
            self.db.client.table("pipeline_meta").update(u).eq(
                "raw_id", raw_id).eq("raw_table", "01_gmail_01_raw").execute()
        except Exception as e:
            logger.warning("pipeline_meta update: %s", e)
