"""
Kakeibo 設定
環境変数からすべての設定を読み込む（Streamlit不要、Flask専用）
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ── Google Drive フォルダID ─────────────────────────────────
INBOX_EASY_FOLDER_ID      = os.getenv("KAKEIBO_INBOX_EASY_FOLDER_ID")       # 読みやすいレシート
INBOX_HARD_FOLDER_ID      = os.getenv("KAKEIBO_INBOX_HARD_FOLDER_ID")       # 読みづらいレシート
ARCHIVE_FOLDER_ID         = os.getenv("KAKEIBO_ARCHIVE_FOLDER_ID")           # 処理済みアーカイブ
ERROR_FOLDER_ID           = os.getenv("KAKEIBO_ERROR_FOLDER_ID")             # エラーフォルダ
MONEYFORWARD_FOLDER_ID    = os.getenv("KAKEIBO_MONEYFORWARD_FOLDER_ID")      # MF CSV取込元
MONEYFORWARD_PROCESSED_ID = os.getenv("KAKEIBO_MONEYFORWARD_PROCESSED_ID")  # MF CSV処理済み

# ── Gemini モデル設定 ──────────────────────────────────────
# EASY: flash-lite（低コスト・高速、きれいなレシート向け）
# HARD: flash  （高精度、かすれ・複雑・手書きレシート向け）
GEMINI_MODEL_EASY   = "gemini-2.5-flash-lite"
GEMINI_MODEL_HARD   = "gemini-2.5-flash"
GEMINI_TEMPERATURE  = 0.1

# ── Supabase ─────────────────────────────────────────────
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")
DEFAULT_OWNER_ID = os.getenv("DEFAULT_OWNER_ID")

# ── OCR プロンプト ─────────────────────────────────────────
# flash-lite でも確実に動作するよう、シンプル・具体的・出力形式固定で設計。
# flash（HARD）は同じプロンプトでより高精度に読み取る。
GEMINI_PROMPT = """
このレシート画像から家計簿に必要な情報を抽出し、JSON形式で返してください。

【最初に確認】
- レシートではない画像 → error: "not_a_receipt"
- 複数のレシートが写っている → error: "multiple_receipts"
- 判読不能 → error: "unreadable"

【抽出ルール】

1. **外税/内税の判定**
   - 商品行に「外8」「外10」がある、または合計前に「外税額」の加算がある → tax_type: "外税"
   - 商品行に「内8」「内10」がある → tax_type: "内税"
   - **重要：** 合計欄の括弧書き `(内消費税等)` `(税率8%対象額)` は支払総額の内訳再掲であり、内税の証拠ではない

2. **合計金額の優先順位**
   - 「小計（税抜）」と「合計（税込）」を混同しない
   - 最も下部に大きく印字されている最終支払額を amounts.total に格納する
   - 印字されている数値をそのまま抽出し、計算・推測は行わない

3. **商品名のクレンジング**
   - 「R」「*」「★」「※」「外8」「外10」などの記号・税区分表示は product_name から除外し tax_mark に格納する
   - 複数行にわたる商品名は適切に結合する

4. **items 配列に含めるもの・含めないもの**
   - **含める**: 実際の商品行（line_type: "ITEM"）と割引行（line_type: "DISCOUNT"）のみ
   - **含めない**: 小計、合計、外税8%対象額、外税10%対象額、外税8%、外税10%、消費税 などの税集計行や合計行 → これらは amounts に入れる
   - 割引行の amount は負の値（例: -26）で格納する
   - 割引行の tax_rate は **直前の商品行と必ず同じ税率** を設定する（割引は対象商品の税率を引き継ぐ）
   - 例: ニベアBWソープ替（10%）の次の会員様割引は tax_rate: 10
   - **小計後の値引き**（定額値引き・定率値引き・割引クーポン等）も line_type: "DISCOUNT" として含める。その場合の tax_rate は 10（不明な場合のデフォルト）

4. **店舗名**
   - ヘッダーに複数の店名がある場合はすべて抽出し「 / 」で連結する（例：魚力 / かつゐ / 九州屋）
   - フッターのショッピングモール名等は shop_info.address または notes に格納する

5. **数値の網羅**
   - 8%対象額・8%税額・10%対象額・10%税額が記載されていれば必ず抽出する
   - フッターの定型文（軽減税率の説明など）は notes にまとめる

【出力JSON形式】
{
  "raw_text": "レシート全体のテキスト",
  "shop_info": {
    "name": "店舗名（複数ある場合は / で連結）",
    "address": "住所",
    "phone": "電話番号",
    "store_code": "店舗コード"
  },
  "transaction_info": {
    "date": "YYYY-MM-DD",
    "time": "HH:MM:SS",
    "register_number": "レジ番号",
    "receipt_number": "レシート番号",
    "transaction_number": "取引番号"
  },
  "items": [
    {
      "line_type": "ITEM または DISCOUNT または SUBTOTAL または TOTAL",
      "line_text": "行全体のテキスト",
      "product_name": "記号を除いた純粋な商品名（割引行は割引名）",
      "quantity": 1,
      "unit_price": 100,
      "amount": 100,
      "tax_mark": "R, ※, ★ などの記号（なければ null）",
      "tax_rate": "8 または 10 または null（割引行は直前商品と同じ税率）"
    }
  ],
  "amounts": {
    "tax_type": "外税 or 内税",
    "subtotal": "小計（税抜）",
    "tax_8_base": "8%対象額（税抜）",
    "tax_8_amount": "8%消費税額",
    "tax_10_base": "10%対象額（税抜）",
    "tax_10_amount": "10%消費税額",
    "total_tax": "消費税合計",
    "total": "合計（税込の最終支払額。小計・外税対象額ではなく消費税加算後の金額）",
    "received": "お預かり",
    "change": "お釣り"
  },
  "payment": {
    "method": "現金/カード/電子マネー等",
    "card_info": "カード情報"
  },
  "other_info": {
    "notes": "不鮮明箇所の補足・定型文など"
  }
}

※記載がない項目は null を設定してください。
※raw_text は最大300文字で打ち切り、超過分は省略してください（区切り線・繰り返し記号は短縮可）。

エラーの場合:
{
  "error": "multiple_receipts" または "not_a_receipt" または "unreadable",
  "message": "エラーの詳細"
}
"""
