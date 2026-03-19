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


def _n(v, default=0):
    """文字列・数値・None を int に変換。'1,706' や '¥500' も対応。"""
    if v is None or v == "" or v == "null":
        return default
    try:
        return int(str(v).replace(',', '').replace('¥', '').replace('￥', '').strip())
    except (ValueError, TypeError):
        return default


def _ni(v):
    """None を返す版の _n（DBカラムに NULL を入れたい場合）"""
    if v is None or v == "" or v == "null":
        return None
    try:
        return int(str(v).replace(',', '').replace('¥', '').replace('￥', '').strip())
    except (ValueError, TypeError):
        return None


_to_int = _ni  # 後方互換エイリアス（None保持版）


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
        existing_receipt_id: str = None,
    ) -> Dict:
        try:
            if "error" in ocr_result:
                self._log_error(file_name, drive_file_id, ocr_result, model_name, None)
                return ocr_result

            trans_date = datetime.strptime(ocr_result["transaction_date"], "%Y-%m-%d").date()

            # drive_file_id で既存レコードを検索（existing_receipt_id 未指定時）
            if not existing_receipt_id and drive_file_id:
                existing_res = self.db.table("Rawdata_RECEIPT_shops") \
                    .select("id").eq("drive_file_id", drive_file_id).execute()
                if existing_res.data:
                    existing_receipt_id = existing_res.data[0]["id"]
                    # 重複レコードを削除
                    for dup in existing_res.data[1:]:
                        self.db.table("Rawdata_RECEIPT_items").delete().eq("receipt_id", dup["id"]).execute()
                        self.db.table("Rawdata_RECEIPT_shops").delete().eq("id", dup["id"]).execute()

            if existing_receipt_id:
                receipt_id = existing_receipt_id
                self._update_receipt(ocr_result, existing_receipt_id)
                self.db.table("Rawdata_RECEIPT_items").delete().eq("receipt_id", existing_receipt_id).execute()
            else:
                receipt_id = self._insert_receipt(ocr_result, file_name, drive_file_id, model_name, source_folder)
            situation_id = self._determine_situation(trans_date)

            # 税集計行として除外するキーワード（レシートの税明細行をGeminiがitemsに含めてしまった場合の防衛）
            _TAX_SUMMARY_KEYWORDS = ("外税8%", "外税10%", "内税8%", "内税10%", "対象額", "消費税", "税額合計")

            normalized_items = []
            for item in ocr_result["items"]:
                line_type = item.get("line_type", "ITEM")
                # 税集計行・合計行はスキップ（amountsに含まれるべき情報）
                product_name_raw = (item.get("product_name") or item.get("line_text") or "")
                if any(kw in product_name_raw for kw in _TAX_SUMMARY_KEYWORDS):
                    continue
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
                # ITEM と DISCOUNT は同じ正規化処理（DISCOUNT は負の amount を持つ）
                normalized_items.append({
                    "raw_item": item,
                    "normalized": self._normalize_item(item, ocr_result["shop_name"], ocr_result.get("tax_summary")),
                    "line_type": line_type,  # ITEM or DISCOUNT
                })

            items_with_tax = self._calculate_and_distribute_tax(
                normalized_items, ocr_result.get("tax_summary")
            )

            tax_type = (ocr_result.get("tax_summary") or {}).get("tax_type", "内税")
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
                    tax_type=tax_type,
                    line_type=item_data.get("line_type", "ITEM"),
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
            tax_8  = _n(tax_summary.get("tax_8_amount"))
            tax_10 = _n(tax_summary.get("tax_10_amount"))
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

    def _update_receipt(self, ocr_result, receipt_id):
        tax_summary = ocr_result.get("tax_summary", {})
        subtotal_amount = None
        if tax_summary:
            s8  = _n(tax_summary.get("tax_8_subtotal"))
            s10 = _n(tax_summary.get("tax_10_subtotal"))
            if s8 or s10:
                subtotal_amount = s8 + s10
        total_amount = sum(
            _n(item.get("total_amount") or item.get("amount") or item.get("displayed_amount"))
            for item in ocr_result.get("items", [])
        )
        tax_s = ocr_result.get("tax_summary", {}) or {}
        data = {
            "shop_name":          ocr_result["shop_name"],
            "transaction_date":   ocr_result["transaction_date"],
            "total_amount_check": _n(ocr_result.get("total_amount_check")) or total_amount or 0,
            "subtotal_amount":    subtotal_amount,
            "tax_8_amount":       _ni(tax_s.get("tax_8_amount")),
            "tax_10_amount":      _ni(tax_s.get("tax_10_amount")),
            "tax_8_subtotal":     _ni(tax_s.get("tax_8_subtotal")),
            "tax_10_subtotal":    _ni(tax_s.get("tax_10_subtotal")),
            "tax_type":           tax_s.get("tax_type", "内税"),
        }
        self.db.table("Rawdata_RECEIPT_shops").update(data).eq("id", receipt_id).execute()

    def _insert_receipt(self, ocr_result, file_name, drive_file_id, model_name, source_folder) -> str:
        trans_date   = datetime.strptime(ocr_result["transaction_date"], "%Y-%m-%d").date()
        total_amount = sum(
            _n(item.get("total_amount") or item.get("amount") or item.get("displayed_amount"))
            for item in ocr_result.get("items", [])
        )
        tax_summary     = ocr_result.get("tax_summary", {})
        subtotal_amount = None
        if tax_summary:
            s8  = _n(tax_summary.get("tax_8_subtotal"))
            s10 = _n(tax_summary.get("tax_10_subtotal"))
            if s8 or s10:
                subtotal_amount = s8 + s10

        data = {
            "transaction_date":    ocr_result["transaction_date"],
            "shop_name":           ocr_result["shop_name"],
            "total_amount_check":  _n(ocr_result.get("total_amount_check")) or total_amount or 0,
            "subtotal_amount":     subtotal_amount,
            "tax_8_amount":        _ni((tax_summary or {}).get("tax_8_amount")),
            "tax_10_amount":       _ni((tax_summary or {}).get("tax_10_amount")),
            "tax_8_subtotal":      _ni((tax_summary or {}).get("tax_8_subtotal")),
            "tax_10_subtotal":     _ni((tax_summary or {}).get("tax_10_subtotal")),
            "tax_type":            (tax_summary or {}).get("tax_type", "内税"),
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
        tax_type="内税", line_type="ITEM",
    ) -> str:
        data = {
            "receipt_id":    receipt_id,
            "line_number":   line_number,
            "line_type":     line_type,
            "ocr_raw_text":  ocr_raw_text,
            "ocr_confidence": ocr_confidence,
            "product_name":  product_name,
            "item_name":     item_name,
            "unit_price":    unit_price,
            "quantity":      quantity,
            "marks_text":    marks_text,
            "discount_text": discount_text,
            "owner_id":      DEFAULT_OWNER_ID,
        }
        data["unit_price"] = _ni(data["unit_price"])
        data["quantity"]   = _ni(data["quantity"])

        if normalized:
            printed   = _to_int(total_amount)
            tax_amt   = _to_int(tax_amount) or 0
            qty       = _to_int(quantity) or 1
            if tax_type == "外税":
                displayed_amount    = printed
                base_price          = printed
                tax_included_amount = (printed + tax_amt) if printed is not None else None
            else:  # 内税
                displayed_amount    = printed
                base_price          = (printed - tax_amt) if printed is not None else None
                tax_included_amount = printed
            std_unit_price = (base_price // qty) if (base_price is not None and qty > 0) else None
            data.update({
                "official_name":      normalized.get("official_name"),
                "general_name":       normalized.get("general_name"),
                "category_id":        normalized.get("category_id") or None,
                "situation_id":       situation_id,
                "tax_rate":           _to_int(normalized["tax_rate"]),
                "displayed_amount":   displayed_amount,
                "base_price":         base_price,
                "tax_included_amount": tax_included_amount,
                "std_unit_price":     _to_int(std_unit_price),
                "tax_amount":         _to_int(tax_amount),
                "needs_review":       needs_review,
            })
        # url = f"{SUPABASE_URL}/rest/v1/Rawdata_RECEIPT_items"
        # with httpx.Client() as client:
        #     resp = client.post(url, headers=self.headers, json=data)
        #     resp.raise_for_status()
        url = f"{SUPABASE_URL}/rest/v1/Rawdata_RECEIPT_items"
        with httpx.Client() as client:
            resp = client.post(url, headers=self.headers, json=data)
            if resp.status_code >= 400:
                raise Exception(f"Rawdata_RECEIPT_items insert failed ({resp.status_code}): {resp.text}")
        return "synthetic-id"

    def _log_success(self, file_name, drive_file_id, receipt_id, transaction_ids, ocr_result=None, model_name=None) -> str:
        data = {
            "file_name":     file_name,
            "drive_file_id": drive_file_id,
            "receipt_id":    receipt_id,
            "status":        "success",
            "owner_id":      DEFAULT_OWNER_ID,
        }
        if model_name: data["ocr_model"] = model_name
        res = self.db.table("99_lg_image_proc_log").upsert(data, on_conflict="file_name").execute()
        return res.data[0]["id"] if res.data else "unknown"

    def _log_error(self, file_name, drive_file_id, error_info, model_name=None, receipt_id=None):
        data = {
            "file_name":     file_name,
            "drive_file_id": drive_file_id,
            "status":        "failed",
            "error_message": error_info.get("message", error_info.get("error")),
            "owner_id":      DEFAULT_OWNER_ID,
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

        total_8  = sum(_n(i["raw_item"].get("total_amount") or i["raw_item"].get("amount") or 0) for i in items_8)
        total_10 = sum(_n(i["raw_item"].get("total_amount") or i["raw_item"].get("amount") or 0) for i in items_10)
        needs_review = False

        tax_type = (tax_summary or {}).get("tax_type", "内税")
        is_exclusive = (tax_type == "外税")  # 外税=税抜価格表示、内税=税込価格表示

        if tax_summary:
            sub8  = _n(tax_summary.get("tax_8_subtotal",  0) or 0)
            tax8  = _n(tax_summary.get("tax_8_amount",    0) or 0)
            sub10 = _n(tax_summary.get("tax_10_subtotal", 0) or 0)
            tax10 = _n(tax_summary.get("tax_10_amount",   0) or 0)
            # 内税: tax_10_subtotal は税込小計 ≈ total_10 と比較
            # 外税: tax_10_subtotal は税抜小計 ≈ total_10（商品の表示価格も税抜）と比較
            r8  = sub8
            r10 = sub10
            if abs(total_8  - r8)  > 5: needs_review = True
            if abs(total_10 - r10) > 5: needs_review = True
            # 外税の場合は税額を直接使用（レシート記載値が正確）
            if is_exclusive:
                if tax8  > 0: return self._distribute_from_known_tax(normalized_items, items_8,  tax8,  items_10, tax10,  needs_review)
            else:
                if r8  > 0: total_8  = r8
                if r10 > 0: total_10 = r10

        if is_exclusive:
            tax_8_total  = int(total_8  * 8  / 100) if total_8  > 0 else 0
            tax_10_total = int(total_10 * 10 / 100) if total_10 > 0 else 0
        else:
            tax_8_total  = int(total_8  * 8  / 108) if total_8  > 0 else 0
            tax_10_total = int(total_10 * 10 / 110) if total_10 > 0 else 0

        self._distribute_tax(items_8,  tax_8_total)
        self._distribute_tax(items_10, tax_10_total)

        if needs_review:
            for item in normalized_items:
                item["needs_review"] = True

        return normalized_items

    def _distribute_from_known_tax(self, all_items, items_8, tax8, items_10, tax10, needs_review):
        """外税でレシートに税額が明記されている場合、その値を直接按分する"""
        self._distribute_tax(items_8,  int(tax8))
        self._distribute_tax(items_10, int(tax10))
        if needs_review:
            for item in all_items:
                item["needs_review"] = True
        return all_items

    def _distribute_tax(self, items: List[Dict], total_tax: int):
        if not items or total_tax == 0:
            for item in items:
                item["normalized"]["tax_amount"] = 0
            return
        grand = sum(_n(i["raw_item"].get("total_amount") or i["raw_item"].get("amount") or 0) for i in items)
        if grand == 0:
            for item in items:
                item["normalized"]["tax_amount"] = 0
            return
        distributed = [int(total_tax * _n(i["raw_item"].get("total_amount") or i["raw_item"].get("amount") or 0) / grand) for i in items]
        remainder   = total_tax - sum(distributed)
        if remainder and distributed:
            distributed[0] += remainder
        for i, item in enumerate(items):
            item["normalized"]["tax_amount"] = distributed[i]

    def _save_tax_summary(self, receipt_id, processing_log_id, tax_summary, items_with_tax):
        calc_8  = sum(i["normalized"]["tax_amount"] for i in items_with_tax if i["normalized"]["tax_rate"] == 8)
        calc_10 = sum(i["normalized"]["tax_amount"] for i in items_with_tax if i["normalized"]["tax_rate"] == 10)
        act_8   = _n(tax_summary.get("tax_8_amount", 0) or 0)
        act_10  = _n(tax_summary.get("tax_10_amount", 0) or 0)
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
            "owner_id":                   DEFAULT_OWNER_ID,
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
        response = self.gemini.call_model(prompt=prompt, model_name="gemini-2.5-flash", max_output_tokens=256)
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
        return None
