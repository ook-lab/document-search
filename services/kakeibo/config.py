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
このレシート画像からすべてのテキスト情報を抽出し、JSON形式で返してください。

【最重要原則】
このタスクの目的は「目の代わり」として機能することです。
人間がレシートを見て読み取れるすべての情報を、一字一句漏らさず抽出してください。

【抽出ルール】
1. **すべての文字・数字・記号を抽出**
   - 店名、住所、電話番号、営業時間
   - すべての商品名（略語もそのまま）
   - すべての価格、数量、金額
   - 小計、消費税、合計
   - レジ番号、取引番号、店舗コード
   - レシート番号、会員番号
   - 日付、時刻
   - バーコード番号
   - その他、印字されているすべてのテキスト

2. **レイアウト・位置関係を保持**
   - 上から順に読み取る
   - 左右の配置（左寄せ、右寄せ）も記録
   - 行の区切りを保持

3. **数値は必ずレシート記載のまま抽出**
   - 推測・計算は一切しない
   - 記載されている通りの数値を抽出
   - 8%対象額、8%税額、10%対象額、10%税額が記載されていれば必ず抽出

4. **税率の扱い**
   - レシートに税率の記載（※、★、8%、10%などのマーク）があればそのまま抽出
   - 記載がない場合は「税率記載なし」と明記

【出力JSON形式】
{
  "raw_text": "レシート全体のテキスト（改行・スペースを含む）",
  "shop_info": {
    "name": "店舗名（レシート記載のまま）",
    "address": "住所（記載されていれば）",
    "phone": "電話番号（記載されていれば）",
    "store_code": "店舗コード（記載されていれば）"
  },
  "transaction_info": {
    "date": "YYYY-MM-DD",
    "time": "HH:MM:SS（記載されていれば）",
    "register_number": "レジ番号（記載されていれば）",
    "receipt_number": "レシート番号（記載されていれば）",
    "transaction_number": "取引番号（記載されていれば）"
  },
  "items": [
    {
      "line_text": "この行のテキストそのまま",
      "product_name": "商品名（レシート記載のまま）",
      "quantity": 1,
      "unit_price": 100,
      "amount": 100,
      "tax_mark": "※または★またはなし",
      "tax_rate": "8または10またはnull"
    }
  ],
  "amounts": {
    "tax_type": "外税 or 内税（外税＝商品価格が税抜き表示でレジで税額を加算、内税＝商品価格に税込み）",
    "subtotal": "小計（記載されていれば）",
    "tax_8_base": "8%対象額（税抜）",
    "tax_8_amount": "8%消費税額",
    "tax_10_base": "10%対象額（税抜）",
    "tax_10_amount": "10%消費税額",
    "total_tax": "消費税合計",
    "total": "合計（支払額）",
    "received": "お預かり（記載されていれば）",
    "change": "お釣り（記載されていれば）"
  },
  "payment": {
    "method": "現金/カード/電子マネー等",
    "card_info": "カード情報（記載されていれば）"
  },
  "other_info": {
    "barcode": "バーコード番号（記載されていれば）",
    "points": "ポイント情報（記載されていれば）",
    "campaign": "キャンペーン情報（記載されていれば）",
    "notes": "その他の情報"
  }
}

【重要な注意】
- 「記載されていない」項目は null を設定してください
- 推測や計算は一切行わないでください
- レシート画像が不鮮明で読み取れない部分があれば、その旨を notes に記載してください
- 複数のレシートが写っている場合は error: "multiple_receipts" を返してください
- レシートではない画像の場合は error: "not_a_receipt" を返してください

エラーの場合:
{
  "error": "multiple_receipts" または "not_a_receipt" または "unreadable",
  "message": "エラーの詳細"
}
"""
