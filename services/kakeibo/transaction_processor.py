"""
Kakeibo トランザクション処理
"""
import re
from datetime import datetime, date
from typing import Dict, List, Optional

from supabase import Client

from config import DEFAULT_OWNER_ID, SUPABASE_URL, SUPABASE_KEY
from gemini_client import GeminiClient
import httpx


class TransactionProcessor:
    """レシートOCR結果をDBに登録するプロセッサ"""

    def __init__(self):
        from db_client import get_db
        self.db: Client = get_db(force_new=True)
        self.gemini = GeminiClient()
        self.headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal"
        }

        # マスタデータをキャッシュ
        self.aliases          = self._load_aliases()
        self.product_dict     = self._load_product_dictionary()
        self.product_generalize = self._load_product_generalize()
        self.situations       = self._load_situations()
        self.categories       = self._load_categories()

    # ── メイン処理 ────────────────────────────────────────────

    def process(
        self,
        ocr_result: Dict,
        file_name: str,
        drive_file_id: str,
        model_name: str = "gemini-2.5-flash",
        source_folder: str = "INBOX",
    ) -> Dict:
        try:
            if "error" in ocr_result:
                self._log_error(file_name, drive_file_id, ocr_result, model_name, None)
                return ocr_result

            trans_date = datetime.strptime(ocr_result["transaction_date"], "%Y-%m-%d").date()

            receipt_id   = self._insert_receipt(ocr_result, file_name, drive_file_id, model_name, source_folder)
            situation_id = self._determine_situation(trans_date)

            normalized_items = []
            for item in ocr_result["items"]:
                line_type = item.get("line_type", "ITEM")
                if line_type in ["SUBTOTAL", "TOTAL"]:
                    name = (
                        item.get("product_name") or item.get("line_text")
                        or item.get("ocr_raw_text")
                        or ("小計" if line_type == "SUBTOTAL" else "合計")
                    )
                    if not name or not name.strip():
                        name = "小計" if line_type == "SUBTOTAL" else "合計"
                    normalized_items.append({
                        "raw_item": item,
                        "normalized": {
                            "product_name": name,
                            "general_name": None,
                            "category_id": None,
                            "tax_rate": item.get("tax_rate", 10),
                            "tax_rate_fixed": False,
                            "tax_amount": item.get("tax_amount", 0),
                        },
                    })
                    continue
                normalized_items.append({
                    "raw_item": item,
                    "normalized": self._normalize_item(item, ocr_result["shop_name"], ocr_result.get("tax_summary")),
                })

            items_with_tax = self._calculate_and_distribute_tax(
                normalized_items, ocr_result.get("tax_summary")
            )

            transaction_ids = []
            for line_num, item_data in enumerate(items_with_tax, start=1):
                item       = item_data["raw_item"]
                normalized = item_data["normalized"]
                trans_id   = self._insert_transaction(
                    receipt_id=receipt_id,
                    line_number=line_num,
                    ocr_raw_text=item.get("ocr_raw", item["product_name"]),
                    ocr_confidence=item.get("confidence"),
                    product_name=normalized["product_name"],
                    item_name=None,
                    unit_price=item.get("unit_price"),
                    quantity=item.get("quantity", 1),
                    marks_text=item.get("tax_mark"),
                    discount_text=item.get("discount_text"),
                    normalized=normalized,
                    situation_id=situation_id,
                    total_amount=item.get("total_amount", item.get("amount", 0)),
                    tax_amount=normalized["tax_amount"],
                    needs_review=item_data.get("needs_review", False),
                )
                transaction_ids.append(trans_id)

            processing_log_id = self._log_success(
                file_name, drive_file_id, receipt_id, transaction_ids, ocr_result, model_name
            )

            if "tax_summary" in ocr_result:
                self._save_tax_summary(receipt_id, processing_log_id, ocr_result["tax_summary"], items_with_tax)

            print(f"[TransactionProcessor] OK: {receipt_id}, {len(transaction_ids)} items, {file_name}")
            return {"success": True, "receipt_id": receipt_id, "transaction_ids": transaction_ids}

        except Exception as e:
            print(f"[TransactionProcessor] ERROR: {file_name}: {e}")
            self._log_error(file_name, drive_file_id, {"error": str(e)}, model_name, None)
            return {"error": "processing_failed", "message": str(e)}

    # ── 商品正規化 ────────────────────────────────────────────

    def _normalize_item(self, item: Dict, shop_name: str, tax_summary: Dict = None) -> Dict:
        product_name = (
            item.get("product_name") or item.get("line_text")
            or item.get("ocr_raw_text") or "不明"
        )
        if not product_name or not product_name.strip():
            product_name = "不明"

        gemini_tax_rate = item.get("tax_rate", 10)

        # レシート全体が単一税率の場合は最優先
        receipt_level_tax_rate = None
        if tax_summary:
            tax_8  = tax_summary.get("tax_8_amount") or 0
            tax_10 = tax_summary.get("tax_10_amount") or 0
            if tax_8 > 0 and tax_10 == 0:
                receipt_level_tax_rate = 8
            elif tax_10 > 0 and tax_8 == 0:
                receipt_level_tax_rate = 10

        if receipt_level_tax_rate is not None:
            return {
                "product_name": product_name,
                "category_id": None,
                "tax_rate": receipt_level_tax_rate,
                "tax_rate_fixed": True,
            }

        # tax_mark から税率判定
        tax_mark = item.get("tax_mark")
        tax_rate_from_mark = None

        for pat, rate in [("外8", 8), ("内8", 8), ("外 8", 8), ("内 8", 8),
                          ("外10", 10), ("内10", 10), ("外 10", 10), ("内 10", 10)]:
            if pat in product_name:
                tax_rate_from_mark = rate
                product_name = product_name.replace(pat, "").strip() or product_name
                break

        if tax_mark and tax_rate_from_mark is None:
            if any(x in str(tax_mark) for x in ["*", "※", "◆", "8%", "(軽)", "外8", "内8"]) or str(tax_mark) == "8":
                tax_rate_from_mark = 8
            elif any(x in str(tax_mark) for x in ["★", "☆", "10%", "外10", "内10"]) or str(tax_mark) == "10":
                tax_rate_from_mark = 10

        # エイリアス変換
        product_name = self.aliases.get(product_name.lower(), product_name)

        # 商品辞書マッチング
        for entry in self.product_dict:
            if entry["raw_keyword"].lower() in product_name.lower():
                return {
                    "product_name": entry["official_name"],
                    "general_name": None,
                    "category_id": entry["category_id"],
                    "tax_rate": tax_rate_from_mark if tax_rate_from_mark else entry.get("tax_rate", 10),
                    "tax_rate_fixed": True,
                }

        return {
            "product_name": product_name,
            "general_name": None,
            "category_id": None,
            "tax_rate": tax_rate_from_mark if tax_rate_from_mark else gemini_tax_rate,
            "tax_rate_fixed": bool(tax_rate_from_mark),
        }

    # ── シチュエーション判定 ──────────────────────────────────

    def _determine_situation(self, trans_date: date) -> Optional[str]:
        default = next((s for s in self.situations if s["name"] == "日常"), None)
        if default:
            return default["id"]
        return self.situations[0]["id"] if self.situations else None

    # ── DB 登録 ───────────────────────────────────────────────

    def _insert_receipt(self, ocr_result, file_name, drive_file_id, model_name, source_folder) -> str:
        trans_date   = datetime.strptime(ocr_result["transaction_date"], "%Y-%m-%d").date()
        total_amount = sum(
            item.get("total_amount") or item.get("amount") or item.get("displayed_amount") or 0
            for item in ocr_result.get("items", [])
        )
        tax_summary     = ocr_result.get("tax_summary", {})
        subtotal_amount = None
        if tax_summary:
            s8  = (tax_summary.get("tax_8_subtotal", 0) or 0)
            s10 = (tax_summary.get("tax_10_subtotal", 0) or 0)
            if s8 or s10:
                subtotal_amount = s8 + s10

        data = {
            "transaction_date":    ocr_result["transaction_date"],
            "shop_name":           ocr_result["shop_name"],
            "total_amount_check":  ocr_result.get("total") or total_amount or 0,
            "subtotal_amount":     subtotal_amount,
            "image_path":          f"99_Archive/{trans_date.strftime('%Y-%m')}/{file_name}",
            "drive_file_id":       drive_file_id,
            "source_folder":       source_folder,
            "ocr_model":           model_name,
            "workspace":           "household",
            "is_verified":         False,
            "owner_id":            DEFAULT_OWNER_ID,
        }
        result = self.db.table("Rawdata_RECEIPT_shops").insert(data).execute()
        return result.data[0]["id"]

    def _insert_transaction(
        self, receipt_id, line_number, ocr_raw_text, ocr_confidence,
        product_name, item_name, unit_price, quantity,
        marks_text=None, discount_text=None, normalized=None,
        situation_id=None, total_amount=None, tax_amount=None, needs_review=False,
    ) -> str:
        data = {
            "receipt_id":    receipt_id,
            "line_number":   line_number,
            "line_type":     "ITEM",
            "ocr_raw_text":  ocr_raw_text,
            "ocr_confidence": ocr_confidence,
            "product_name":  product_name,
            "item_name":     item_name,
            "unit_price":    unit_price,
            "quantity":      quantity,
            "marks_text":    marks_text,
            "discount_text": discount_text,
            # "owner_id":      DEFAULT_OWNER_ID,  # Stale schema cache
        }
        if normalized:
            base_price     = normalized.get("base_price")
            std_unit_price = (base_price // quantity) if (base_price and quantity and quantity > 0) else None
            data.update({
                "official_name":  normalized.get("official_name"),
                "general_name":   normalized.get("general_name"),
                "category_id":    normalized.get("category_id"),
                "situation_id":   situation_id,
                "tax_rate":       normalized["tax_rate"],
                "std_amount":     total_amount,
                "std_unit_price": std_unit_price,
                "tax_amount":     tax_amount,
                "needs_review":   needs_review,
            })
        # url = f"{SUPABASE_URL}/rest/v1/Rawdata_RECEIPT_items"
        # with httpx.Client() as client:
        #     resp = client.post(url, headers=self.headers, json=data)
        #     resp.raise_for_status()
        url = f"{SUPABASE_URL}/rest/v1/Rawdata_RECEIPT_items"
        with httpx.Client() as client:
            resp = client.post(url, headers=self.headers, json=data)
            resp.raise_for_status()
        return "synthetic-id"  # We don't need the ID for items in the current flow

    def _log_success(self, file_name, drive_file_id, receipt_id, transaction_ids, ocr_result=None, model_name=None) -> str:
        data = {
            "file_name":       file_name,
            "drive_file_id":   drive_file_id,
            "receipt_id":      receipt_id,
            "status":          "success",
            "transaction_ids": transaction_ids,
            # "owner_id":        DEFAULT_OWNER_ID,  # Stale schema cache
        }
        if model_name:  data["ocr_model"]  = model_name
        if ocr_result:  data["ocr_result"] = ocr_result
        url = f"{SUPABASE_URL}/rest/v1/99_lg_image_proc_log"
        with httpx.Client() as client:
            resp = client.post(url, headers=self.headers, json=data)
            resp.raise_for_status()
        return "synthetic-log-id"

    def _log_error(self, file_name, drive_file_id, error_info, model_name=None, receipt_id=None):
        data = {
            "file_name":     file_name,
            "drive_file_id": drive_file_id,
            "status":        "failed",
            "error_message": error_info.get("message", error_info.get("error")),
            # "owner_id":      DEFAULT_OWNER_ID,  # Stale schema cache
        }
        if model_name:  data["ocr_model"]  = model_name
        if receipt_id:  data["receipt_id"] = receipt_id
        # self.db.table("99_lg_image_proc_log").insert(data).execute()
        url = f"{SUPABASE_URL}/rest/v1/99_lg_image_proc_log"
        with httpx.Client() as client:
            client.post(url, headers=self.headers, json=data)

    # ── 税額按分 ──────────────────────────────────────────────

    def _calculate_and_distribute_tax(self, normalized_items: List[Dict], tax_summary: Dict) -> List[Dict]:
        items_8, items_10 = [], []
        for item_data in normalized_items:
            if item_data["raw_item"].get("line_type", "ITEM") in ["SUBTOTAL", "TOTAL"]:
                continue
            if item_data["normalized"]["tax_rate"] == 8:
                items_8.append(item_data)
            else:
                items_10.append(item_data)

        total_8  = sum((i["raw_item"].get("total_amount") or i["raw_item"].get("amount") or 0) for i in items_8)
        total_10 = sum((i["raw_item"].get("total_amount") or i["raw_item"].get("amount") or 0) for i in items_10)
        needs_review = False

        if tax_summary:
            r8  = (tax_summary.get("tax_8_subtotal", 0) or 0) + (tax_summary.get("tax_8_amount", 0) or 0)
            r10 = (tax_summary.get("tax_10_subtotal", 0) or 0) + (tax_summary.get("tax_10_amount", 0) or 0)
            if abs(total_8  - r8)  > 5: needs_review = True
            if abs(total_10 - r10) > 5: needs_review = True
            if r8  > 0: total_8  = r8
            if r10 > 0: total_10 = r10

        tax_8_total  = round(total_8  * 8  / 108) if total_8  > 0 else 0
        tax_10_total = round(total_10 * 10 / 110) if total_10 > 0 else 0

        self._distribute_tax(items_8,  tax_8_total)
        self._distribute_tax(items_10, tax_10_total)

        if needs_review:
            for item in normalized_items:
                item["needs_review"] = True

        return normalized_items

    def _distribute_tax(self, items: List[Dict], total_tax: int):
        if not items or total_tax == 0:
            for item in items:
                item["normalized"]["tax_amount"] = 0
            return
        grand = sum((i["raw_item"].get("total_amount") or i["raw_item"].get("amount") or 0) for i in items)
        if grand == 0:
            for item in items:
                item["normalized"]["tax_amount"] = 0
            return
        distributed = [int(total_tax * (i["raw_item"].get("total_amount") or i["raw_item"].get("amount") or 0) / grand) for i in items]
        remainder   = total_tax - sum(distributed)
        if remainder and distributed:
            distributed[0] += remainder
        for i, item in enumerate(items):
            item["normalized"]["tax_amount"] = distributed[i]

    def _save_tax_summary(self, receipt_id, processing_log_id, tax_summary, items_with_tax):
        calc_8  = sum(i["normalized"]["tax_amount"] for i in items_with_tax if i["normalized"]["tax_rate"] == 8)
        calc_10 = sum(i["normalized"]["tax_amount"] for i in items_with_tax if i["normalized"]["tax_rate"] == 10)
        act_8   = tax_summary.get("tax_8_amount", 0) or 0
        act_10  = tax_summary.get("tax_10_amount", 0) or 0
        matches = abs(calc_8 - act_8) <= 1 and abs(calc_10 - act_10) <= 1
        data = {
            "receipt_id":                 receipt_id,
            "tax_8_subtotal":             tax_summary.get("tax_8_subtotal"),
            "tax_8_amount":               act_8,
            "tax_10_subtotal":            tax_summary.get("tax_10_subtotal"),
            "tax_10_amount":              act_10,
            "total_amount":               tax_summary.get("total_amount"),
            "calculated_tax_8_amount":    calc_8,
            "calculated_tax_10_amount":   calc_10,
            "calculated_matches_actual":  matches,
            "tax_8_diff":                 calc_8  - act_8,
            "tax_10_diff":                calc_10 - act_10,
            # "owner_id":                   DEFAULT_OWNER_ID,  # Stale schema cache
        }
        url = f"{SUPABASE_URL}/rest/v1/60_ag_receipt_summary"
        with httpx.Client() as client:
            client.post(url, headers=self.headers, json=data)


    # ── マスタ読み込み ────────────────────────────────────────

    def _load_aliases(self) -> Dict[str, str]:
        rows = self.db.table("MASTER_Rules_transaction_dict").select("*").execute().data
        return {r["product_name"].lower(): r["official_name"]
                for r in rows if r.get("product_name") and r.get("official_name")}

    def _load_product_dictionary(self) -> List[Dict]:
        return self.db.table("MASTER_Product_classify").select("*").execute().data

    def _load_product_generalize(self) -> Dict[str, str]:
        rows = self.db.table("MASTER_Product_generalize").select("*").execute().data
        return {r["raw_keyword"].lower(): r["general_name"]
                for r in rows if r.get("raw_keyword") and r.get("general_name")}

    def _load_situations(self) -> List[Dict]:
        return self.db.table("MASTER_Categories_purpose").select("*").execute().data

    def _load_categories(self) -> List[Dict]:
        return self.db.table("MASTER_Categories_product").select("*").execute().data

    # ── 商品名一般化（AI補助）────────────────────────────────

    def _extract_general_name_with_ai(self, product_name: str) -> Optional[Dict]:
        prompt = f"Product: {product_name}\nExtract general name and keywords in JSON format: {{\"general_name\": \"...\", \"keywords\": [...]}}"
        try:
            response = self.gemini.call_model(prompt=prompt, model_name="gemini-2.5-flash", max_output_tokens=256)
            if not response.get("success"):
                return None
            content = response["content"].strip()
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
                content = content.strip()
            import json
            result = json.loads(content)
            if "general_name" in result and isinstance(result.get("keywords"), list):
                return result
        except Exception as e:
            print(f"[TransactionProcessor] AI general_name error: {e}")
        return None
