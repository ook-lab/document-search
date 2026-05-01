"""
家計簿レビューUI (Streamlit)

レシート単位でのレビュー：
- レシート画像プレビュー
- 商品一覧（表形式）
- 合計金額
- レシート単位での承認・編集
"""

import io
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st
from google.oauth2 import service_account
from googleapiclient.discovery import build
from PIL import Image

# Streamlit は __main__ として実行されるため、kakeibo_lib ディレクトリを先頭に載せる
_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from _supabase import anon_supabase, client_with_access_token
from config import GOOGLE_DRIVE_CREDENTIALS
from streamlit_auth import create_streamlit_auth_ui, create_logout_button

# Supabase接続 - グローバル変数として宣言（認証後に設定）
db_client = None
db = None


def init_database(access_token: str = None):
    """認証済みトークンでデータベース接続を初期化"""
    global db_client, db
    if access_token:
        db = client_with_access_token(access_token)
    else:
        db = anon_supabase()
    db_client = None

# Google Drive接続
@st.cache_resource
def get_drive_service():
    """Google Drive APIサービスを取得"""
    import json
    from pathlib import Path

    # Streamlit Cloudの場合はSecretsから、ローカルの場合はファイルから
    if "gcp_service_account" in st.secrets:
        # Streamlit CloudのSecrets
        credentials = service_account.Credentials.from_service_account_info(
            st.secrets["gcp_service_account"],
            scopes=["https://www.googleapis.com/auth/drive.readonly"]
        )
    else:
        # ローカル環境
        cred_path = Path(GOOGLE_DRIVE_CREDENTIALS)
        if not cred_path.exists():
            st.error(f"サービスアカウントファイルが見つかりません: {GOOGLE_DRIVE_CREDENTIALS}")
            st.stop()
        credentials = service_account.Credentials.from_service_account_file(
            GOOGLE_DRIVE_CREDENTIALS,
            scopes=["https://www.googleapis.com/auth/drive.readonly"]
        )
    return build("drive", "v3", credentials=credentials)


def get_receipt_image(drive_file_id: str):
    """Google Driveからレシート画像を取得"""
    try:
        service = get_drive_service()
        request = service.files().get_media(
            fileId=drive_file_id,
            supportsAllDrives=True
        )

        file_bytes = io.BytesIO()
        from googleapiclient.http import MediaIoBaseDownload
        downloader = MediaIoBaseDownload(file_bytes, request)

        done = False
        while not done:
            status, done = downloader.next_chunk()

        file_bytes.seek(0)
        return Image.open(file_bytes)
    except Exception as e:
        st.error(f"画像取得エラー: {e}")
        return None


def main():
    st.set_page_config(page_title="家計簿レビュー", layout="wide")
    st.title("📊 家計簿レビューシステム")

    # 認証フロー
    auth_manager, is_authenticated = create_streamlit_auth_ui()

    if not is_authenticated:
        st.warning("🔐 管理機能を使用するにはログインが必要です")
        st.info("サイドバーからログインしてください")
        return

    # ログアウトボタン表示
    create_logout_button()

    # 認証済みトークンでデータベース接続を初期化
    init_database(access_token=auth_manager.access_token)

    # メインタブ
    tab1, tab2 = st.tabs(["📄 レシートレビュー", "🏷️ 商品分類管理"])

    with tab1:
        show_receipt_review_tab()

    with tab2:
        show_product_classification_tab()


def show_receipt_review_tab():
    """レシートレビュータブ"""
    # サイドバー：自動取り込み情報
    st.sidebar.header("📥 レシート取り込み")

    with st.sidebar.expander("ℹ️ 自動取り込みについて"):
        st.markdown("""
        **レシートは自動的に取り込まれます**

        - Google Drive の Inbox フォルダに画像を配置すると、1時間ごとに自動処理されます
        - 処理成功 → Archive フォルダに移動
        - 処理失敗 → Error フォルダに移動

        手動で取り込む場合は、GitHubの Actions タブから実行できます。
        """)

        st.divider()

        if st.button("📊 データ件数を確認"):
            try:
                receipts = db.table("Rawdata_RECEIPT_shops").select("*", count="exact").execute()
                transactions = db.table("Rawdata_RECEIPT_items").select("*", count="exact").execute()
                st.success(f"レシート: {receipts.count}件、商品: {transactions.count}件")
            except Exception as e:
                st.error(f"エラー: {e}")

    st.sidebar.divider()

    # サイドバー：レシート一覧
    st.sidebar.header("レシート一覧")

    # 処理ログ取得（レシート単位）
    try:
        logs = db.table("99_lg_image_proc_log") \
            .select("*") \
            .order("processed_at", desc=True) \
            .limit(100) \
            .execute()
    except Exception as e:
        st.error(f"処理ログの取得エラー: {str(e)}")
        st.info("エラー詳細を確認してください")
        import traceback
        st.code(traceback.format_exc())
        return

    if not logs.data:
        st.info("処理済みレシートがありません")
        return

    # フィルター
    status_filter = st.sidebar.selectbox(
        "ステータス",
        ["すべて", "未確認", "確認済み", "エラー"]
    )

    # レシート選択
    receipt_options = []
    for log in logs.data:
        status_icon = {
            "success": "✅",
            "failed": "❌"
        }.get(log["status"], "⚠️")

        label = f"{status_icon} {log['file_name']} ({log['processed_at'][:10]})"
        receipt_options.append((label, log))

    if not receipt_options:
        st.info("表示するレシートがありません")
        return

    selected_label = st.sidebar.radio(
        "レシートを選択",
        [opt[0] for opt in receipt_options],
        key="receipt_selector"
    )

    # 選択されたレシートを取得
    selected_log = next(opt[1] for opt in receipt_options if opt[0] == selected_label)

    # メイン画面：レシート詳細
    show_receipt_detail(selected_log)


def determine_expense_category(db, product_category: str, person: str, purpose: str):
    """
    2次分類（費目）を決定

    優先順位:
    1. 名目 + 人物 + 1次分類の完全一致（priority=80）
    2. 名目 + 1次分類（priority=90）
    3. 名目 + 人物（priority=90）
    4. 名目のみ（priority=100）
    5. 人物 + 1次分類（priority=50）
    6. 1次分類のみ（priority=30）

    Returns:
        str: 2次分類（費目）名、またはNone
    """
    try:
        # 1次分類IDを取得
        product_category_id = None
        if product_category:
            result = db.table("MASTER_Categories_product").select("id") \
                .eq("name", product_category) \
                .limit(1) \
                .execute()
            if result.data:
                product_category_id = result.data[0]["id"]

        # 名目IDを取得
        purpose_id = None
        if purpose:
            result = db.table("MASTER_Categories_purpose").select("id") \
                .eq("name", purpose) \
                .limit(1) \
                .execute()
            if result.data:
                purpose_id = result.data[0]["id"]

        # ルールを検索（優先度の高い順）
        # SQLでNULL比較を正しく処理
        query = db.table("MASTER_Rules_expense_mapping") \
            .select("expense_category_id, MASTER_Categories_expense(name)") \
            .order("priority", desc=True) \
            .limit(1)

        # 条件を動的に構築
        conditions = []

        # 完全一致を優先
        if purpose_id and person and product_category_id:
            query = query.eq("purpose_id", purpose_id) \
                        .eq("person", person) \
                        .eq("product_category_id", product_category_id)
        elif purpose_id and product_category_id:
            query = query.eq("purpose_id", purpose_id) \
                        .eq("product_category_id", product_category_id) \
                        .is_("person", "null")
        elif purpose_id and person:
            query = query.eq("purpose_id", purpose_id) \
                        .eq("person", person) \
                        .is_("product_category_id", "null")
        elif purpose_id:
            query = query.eq("purpose_id", purpose_id) \
                        .is_("person", "null") \
                        .is_("product_category_id", "null")
        elif person and product_category_id:
            query = query.is_("purpose_id", "null") \
                        .eq("person", person) \
                        .eq("product_category_id", product_category_id)
        elif product_category_id:
            query = query.is_("purpose_id", "null") \
                        .is_("person", "null") \
                        .eq("product_category_id", product_category_id)
        else:
            return None

        result = query.execute()

        if result.data:
            # JOINした結果から費目名を取得
            expense_category_data = result.data[0].get("MASTER_Categories_expense")
            if expense_category_data:
                return expense_category_data.get("name")

        return None

    except Exception as e:
        st.warning(f"2次分類決定エラー: {e}")
        return None


def auto_classify_transaction(db, shop_name: str, product_name: str, official_name: str = "", general_name: str = ""):
    """
    辞書テーブルを参照して、分類・人物・名目を自動判定

    優先順位:
    1. 店舗名 + 商品名の完全一致
    2. 店舗名のみ（店舗全体のデフォルト）
    3. 商品名のみ
    4. official_nameのみ
    5. general_nameのみ

    Returns:
        dict: {"category": str, "person": str, "purpose": str} または None
    """
    try:
        # 1. 店舗名 + 商品名の完全一致
        if shop_name and product_name:
            result = db.table("MASTER_Rules_transaction_dict").select("*") \
                .eq("shop_name", shop_name) \
                .eq("product_name", product_name) \
                .order("priority") \
                .limit(1) \
                .execute()
            if result.data:
                match = result.data[0]
                return {
                    "category": match.get("category"),
                    "person": match.get("person"),
                    "purpose": match.get("purpose")
                }

        # 2. 店舗名のみ（店舗全体のデフォルト）
        if shop_name:
            result = db.table("MASTER_Rules_transaction_dict").select("*") \
                .eq("shop_name", shop_name) \
                .eq("rule_type", "shop_only") \
                .order("priority") \
                .limit(1) \
                .execute()
            if result.data:
                match = result.data[0]
                return {
                    "category": match.get("category"),
                    "person": match.get("person"),
                    "purpose": match.get("purpose")
                }

        # 3. 商品名のみ
        if product_name:
            result = db.table("MASTER_Rules_transaction_dict").select("*") \
                .eq("product_name", product_name) \
                .is_("shop_name", "null") \
                .order("priority") \
                .limit(1) \
                .execute()
            if result.data:
                match = result.data[0]
                return {
                    "category": match.get("category"),
                    "person": match.get("person"),
                    "purpose": match.get("purpose")
                }

        # 4. official_nameのみ
        if official_name:
            result = db.table("MASTER_Rules_transaction_dict").select("*") \
                .eq("official_name", official_name) \
                .order("priority") \
                .limit(1) \
                .execute()
            if result.data:
                match = result.data[0]
                return {
                    "category": match.get("category"),
                    "person": match.get("person"),
                    "purpose": match.get("purpose")
                }

        # 5. general_nameのみ
        if general_name:
            result = db.table("MASTER_Rules_transaction_dict").select("*") \
                .eq("general_name", general_name) \
                .order("priority") \
                .limit(1) \
                .execute()
            if result.data:
                match = result.data[0]
                return {
                    "category": match.get("category"),
                    "person": match.get("person"),
                    "purpose": match.get("purpose")
                }

        # マッチなし
        return None

    except Exception as e:
        st.warning(f"自動判定エラー: {e}")
        return None


def save_to_dictionary(db, shop_name: str, product_name: str, official_name: str, general_name: str,
                      category: str, person: str, purpose: str):
    """
    辞書テーブルに保存（または更新）

    商品名をキーとして、分類・人物・名目を保存
    既存レコードがあれば使用回数をインクリメント
    """
    try:
        # 既存レコードを検索（shop_name + product_nameの組み合わせ）
        existing = db.table("MASTER_Rules_transaction_dict").select("*") \
            .eq("shop_name", shop_name) \
            .eq("product_name", product_name) \
            .execute()

        if existing.data:
            # 既存レコードを更新（使用回数をインクリメント）
            record = existing.data[0]
            db.table("MASTER_Rules_transaction_dict").update({
                "category": category,
                "person": person,
                "purpose": purpose,
                "official_name": official_name,
                "general_name": general_name,
                "usage_count": record.get("usage_count", 0) + 1,
                "updated_at": "NOW()"
            }).eq("id", record["id"]).execute()
        else:
            # 新規レコードを作成
            # ルールタイプを判定
            if shop_name and product_name:
                rule_type = "shop_product"
                priority = 10
            elif shop_name:
                rule_type = "shop_only"
                priority = 20
            elif official_name:
                rule_type = "official"
                priority = 30
            elif general_name:
                rule_type = "general"
                priority = 40
            else:
                rule_type = "product"
                priority = 50

            db.table("MASTER_Rules_transaction_dict").insert({
                "shop_name": shop_name,
                "product_name": product_name,
                "official_name": official_name,
                "general_name": general_name,
                "category": category,
                "person": person,
                "purpose": purpose,
                "rule_type": rule_type,
                "priority": priority,
                "usage_count": 1
            }).execute()

    except Exception as e:
        st.warning(f"辞書保存エラー: {e}")


def show_receipt_detail(log: dict):
    """レシート詳細表示"""

    st.header(f"📄 {log['file_name']}")

    # 2カラムレイアウト
    col_left, col_right = st.columns([1, 1])

    with col_left:
        st.subheader("レシート画像")

        if log.get("drive_file_id"):
            with st.spinner("画像を読み込み中..."):
                image = get_receipt_image(log["drive_file_id"])
                if image:
                    st.image(image, use_column_width=True)
                else:
                    st.warning("画像を取得できませんでした")
        else:
            st.info("画像IDがありません")

        # レシート情報
        st.subheader("処理情報")
        info_data = {
            "ファイル名": log["file_name"],
            "処理日時": log["processed_at"],
            "ステータス": log["status"],
            "OCRモデル": log.get("ocr_model", "不明"),
            "エラー": log.get("error_message", "なし")
        }

        for key, value in info_data.items():
            st.text(f"{key}: {value}")

    with col_right:
        st.subheader("取引明細")

        if log["status"] == "success" and log.get("receipt_id"):
            try:
                # レシート情報を取得
                receipt_result = db.table("Rawdata_RECEIPT_shops") \
                    .select("*") \
                    .eq("id", log["receipt_id"]) \
                    .execute()

                if not receipt_result.data:
                    st.warning("レシート情報が見つかりません")
                    return

                receipt = receipt_result.data[0]
            except Exception as e:
                st.error(f"データベースエラーが発生しました")
                st.exception(e)
                st.write("**エラー詳細:**")
                st.write(f"- エラー型: {type(e).__name__}")
                st.write(f"- エラーメッセージ: {str(e)}")

                # より詳細な情報を取得
                if hasattr(e, 'message'):
                    st.write(f"- APIメッセージ: {e.message}")
                if hasattr(e, 'details'):
                    st.write(f"- 詳細: {e.details}")
                if hasattr(e, 'hint'):
                    st.write(f"- ヒント: {e.hint}")
                if hasattr(e, 'code'):
                    st.write(f"- エラーコード: {e.code}")

                st.info("**解決方法:**\n"
                       "1. Streamlit Secretsに `SUPABASE_SERVICE_ROLE_KEY` が設定されているか確認してください\n"
                       "2. Supabaseのダッシュボードで、テーブル `Rawdata_RECEIPT_shops` のRLSポリシーを確認してください\n"
                       "3. `receipt_id` が正しく設定されているか確認してください")
                return

            # 税表示タイプを判定（レシートレベル）
            # すべてのレシートには小計がある（前提）
            # 合計が省略されている場合は、小計と同じ値とする
            subtotal = receipt.get('subtotal_amount')
            total = receipt.get('total_amount_check')

            # 合計が省略されている場合、小計と同じとする
            if total is None and subtotal is not None:
                total = subtotal
                st.info(f"合計が省略されているため、小計と同じ値（¥{subtotal:,}）を使用")

            # 念のため：小計がない場合のフォールバック
            if subtotal is None and total is not None:
                subtotal = total

            # 判定：小計 < 合計 → 外税、小計 = 合計 → 内税
            if subtotal and total:
                if subtotal < total:
                    tax_display_type = "外税"
                else:
                    tax_display_type = "内税"
            else:
                tax_display_type = "不明"

            # トランザクションを取得（JOINは使わず2段階クエリ）
            # 注意: 小計・合計行を除外するため、line_type = 'ITEM' のみ取得
            try:
                transactions = db.table("Rawdata_RECEIPT_items") \
                    .select("*") \
                    .eq("receipt_id", log["receipt_id"]) \
                    .or_("line_type.eq.ITEM,line_type.is.null") \
                    .order("line_number") \
                    .execute()

                # Note: standardized data is now stored directly in Rawdata_RECEIPT_items
                # No need to fetch from separate table

            except Exception as e:
                st.error(f"トランザクション取得エラー: {str(e)}")
                import traceback
                st.code(traceback.format_exc())
                return

            if transactions.data:
                # 🔍 デバッグ：最初のトランザクションのデータ構造を確認
                if len(transactions.data) > 0:
                    first_t = transactions.data[0]
                    with st.expander("🔍 デバッグ情報（最初の商品）"):
                        # データベースキー情報
                        import os
                        service_role_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
                        anon_key = os.getenv("SUPABASE_KEY")
                        st.write("**🔑 データベースキー情報**")
                        if service_role_key:
                            st.write(f"SERVICE_ROLE_KEY設定: ✅ あり (...{service_role_key[-4:]})")
                        else:
                            st.write("SERVICE_ROLE_KEY設定: ❌ なし")
                        if anon_key:
                            st.write(f"ANON_KEY設定: ✅ あり (...{anon_key[-4:]})")
                        else:
                            st.write("ANON_KEY設定: ❌ なし")
                        st.write(f"使用中のキー末尾: ...{SUPABASE_KEY[-4:]}")
                        st.write("---")

                        # 生のトランザクションデータを表示
                        st.write("**📦 生のトランザクションデータ（全キー）:**")
                        st.json(first_t)
                        st.write("---")

                        # データ構造情報（standardized data is now in the same record）
                        st.write(f"**商品名**: {first_t.get('product_name')}")
                        st.write(f"**std_unit_price**: {first_t.get('std_unit_price')}")
                        st.write(f"**tax_amount**: {first_t.get('tax_amount')}")
                        st.write(f"**std_amount**: {first_t.get('std_amount')}")

                # DataFrameに変換（7要素構造 + ID情報）
                df_data = []
                for t in transactions.data:
                    # Note: standardized data is now directly in t (no separate table)

                    # 7要素データを取得
                    quantity = t.get("quantity") or 1
                    std_unit_price = t.get('std_unit_price')  # 本体単価（1個あたりの税抜価格）
                    tax_amount = t.get('tax_amount')  # 税額
                    tax_included_amount = t.get('std_amount')  # 税込価

                    # 本体価を計算（税込価 - 税額）
                    # 注意: 本体価は数量分の合計なので、数量を掛けない
                    base_price_total = None
                    if tax_included_amount is not None and tax_amount is not None:
                        base_price_total = tax_included_amount - tax_amount

                    # 表示額を取得
                    # 1. transactionsテーブルのdisplayed_amountを優先（レシート記載値）
                    # 2. なければ計算で求める（後方互換性）
                    displayed_amount = t.get("displayed_amount")
                    if displayed_amount is None:
                        if tax_display_type == "内税":
                            displayed_amount = tax_included_amount
                        elif tax_display_type == "外税":
                            displayed_amount = base_price_total

                    # 税込単価を計算（税込価 ÷ 数量）
                    tax_included_unit_price = None
                    if tax_included_amount and quantity:
                        tax_included_unit_price = tax_included_amount // quantity

                    # 分類の階層表示（内部的には大中小の3階層、表示は最下層のみ）
                    major = t.get("major_category") or ""
                    middle = t.get("middle_category") or ""
                    minor = t.get("minor_category") or ""

                    # 表示用の分類（最下層のみ、なければ順に上位を表示）
                    category_display = minor or middle or major or ""

                    # 人物と名目を取得
                    person_value = t.get("person") or "家族"  # デフォルト: 家族
                    purpose_value = t.get("purpose") or "日常"  # デフォルト: 日常

                    # 2次分類（費目）を自動判定
                    expense_category = determine_expense_category(
                        db=db,
                        product_category=category_display,
                        person=person_value,
                        purpose=purpose_value
                    ) or ""

                    df_data.append({
                        "_transaction_id": t["id"],  # 更新用（非表示）
                        "_std_id": t.get("id"),  # 更新用（非表示、now same as transaction_id）
                        "_major_category": major,  # 内部保持（非表示）
                        "_middle_category": middle,  # 内部保持（非表示）
                        "_minor_category": minor,  # 内部保持（非表示）
                        "商品名": t["product_name"],
                        "数量": quantity,
                        "表示額": displayed_amount if displayed_amount is not None else 0,
                        "外or内": tax_display_type,
                        "税率": t.get('tax_rate', 10),
                        "本体価": base_price_total if base_price_total is not None else 0,
                        "税額": tax_amount if tax_amount is not None else 0,
                        "税込価": tax_included_amount if tax_included_amount is not None else 0,
                        "単価": tax_included_unit_price if tax_included_unit_price is not None else 0,
                        "正式名": t.get("official_name") or "",
                        "物品名": t.get("item_name") or "",
                        "分類": category_display,
                        "人物": person_value,
                        "名目": purpose_value,
                        "費目": expense_category,  # 2次分類（自動判定）
                        "要確認": "⚠️" if t.get("needs_review") else ""
                    })

                df = pd.DataFrame(df_data)

                # 人物と名目の選択肢を取得
                person_options = ["家族", "パパ", "ママ", "絵麻", "育哉"]

                # 名目の選択肢（DBから取得）
                try:
                    purposes_result = db.table("MASTER_Categories_purpose").select("name").order("display_order").execute()
                    purpose_options = [p["name"] for p in purposes_result.data] if purposes_result.data else ["日常"]
                except:
                    # テーブルがまだ存在しない場合のフォールバック
                    existing_purposes = set()
                    for t in transactions.data:
                        purpose = t.get("purpose")
                        if purpose:
                            existing_purposes.add(purpose)
                    purpose_options = sorted(list(existing_purposes)) if existing_purposes else []
                    if "日常" not in purpose_options:
                        purpose_options.insert(0, "日常")

                # 費目の選択肢（DBから取得）
                try:
                    expense_cats_result = db.table("MASTER_Categories_expense").select("name").order("display_order").execute()
                    expense_category_options = [c["name"] for c in expense_cats_result.data] if expense_cats_result.data else []
                except:
                    expense_category_options = []

                # AI自動判定ボタン
                st.divider()
                col_ai1, col_ai2 = st.columns([3, 1])
                with col_ai1:
                    st.info("🤖 AI自動判定: 辞書テーブルを参照して、店舗名と商品名から分類・人物・名目を自動で設定します")
                with col_ai2:
                    if st.button("🤖 AI自動判定", type="secondary", key="ai_auto_classify"):
                        # 店舗名を取得
                        shop_name = receipt.get("shop_name", "")

                        # 各商品に対してAI自動判定を実行
                        auto_classified_count = 0
                        for idx in df.index:
                            product_name = df.loc[idx, "商品名"]
                            official_name = df.loc[idx, "正式名"] or ""
                            general_name = ""  # 現時点では未実装

                            # AI自動判定
                            result = auto_classify_transaction(
                                db=db,
                                shop_name=shop_name,
                                product_name=product_name,
                                official_name=official_name,
                                general_name=general_name
                            )

                            if result:
                                # 判定結果をdfに反映
                                if result.get("category"):
                                    df.loc[idx, "分類"] = result["category"]
                                if result.get("person"):
                                    df.loc[idx, "人物"] = result["person"]
                                if result.get("purpose"):
                                    df.loc[idx, "名目"] = result["purpose"]

                                # 2次分類（費目）を再判定
                                expense_cat = determine_expense_category(
                                    db=db,
                                    product_category=df.loc[idx, "分類"],
                                    person=df.loc[idx, "人物"],
                                    purpose=df.loc[idx, "名目"]
                                )
                                if expense_cat:
                                    df.loc[idx, "費目"] = expense_cat

                                auto_classified_count += 1

                        if auto_classified_count > 0:
                            st.success(f"✅ {auto_classified_count}件の商品を自動判定しました。下の表で確認して、必要に応じて修正してください。")
                            st.rerun()
                        else:
                            st.warning("辞書に該当するデータがありませんでした。手動で設定後、「辞書に保存」をチェックしてデータを更新してください。")

                st.divider()

                # 全行一括編集機能
                with st.expander("🔧 全行一括編集", expanded=False):
                    st.info("分類、人物、名目を全行に一括で適用できます。適用後、下の表で確認してから「データを更新」ボタンを押してください。")

                    col1, col2, col3 = st.columns(3)

                    with col1:
                        bulk_category = st.text_input("分類（全行）", key="bulk_category", placeholder="例: 根菜")
                        if st.button("✅ 分類を全行に適用", key="apply_bulk_category"):
                            if bulk_category:
                                for idx in df.index:
                                    df.loc[idx, "分類"] = bulk_category
                                st.success(f"分類を「{bulk_category}」に変更しました（表を確認後、下の「データを更新」ボタンを押してください）")

                    with col2:
                        bulk_person = st.selectbox("人物（全行）", options=person_options, index=0, key="bulk_person")
                        if st.button("✅ 人物を全行に適用", key="apply_bulk_person"):
                            for idx in df.index:
                                df.loc[idx, "人物"] = bulk_person
                            st.success(f"人物を「{bulk_person}」に変更しました（表を確認後、下の「データを更新」ボタンを押してください）")

                    with col3:
                        bulk_purpose_index = 0 if "日常" in purpose_options else 0
                        bulk_purpose = st.selectbox("名目（全行）", options=purpose_options if purpose_options else ["日常"], index=bulk_purpose_index, key="bulk_purpose")
                        if st.button("✅ 名目を全行に適用", key="apply_bulk_purpose"):
                            for idx in df.index:
                                df.loc[idx, "名目"] = bulk_purpose
                            st.success(f"名目を「{bulk_purpose}」に変更しました（表を確認後、下の「データを更新」ボタンを押してください）")

                # 編集可能なデータエディタ
                edited_df = st.data_editor(
                    df,
                    hide_index=True,
                    height=400,
                    column_config={
                        "_transaction_id": None,  # 非表示
                        "_std_id": None,  # 非表示
                        "_major_category": None,  # 非表示
                        "_middle_category": None,  # 非表示
                        "_minor_category": None,  # 非表示
                        "商品名": st.column_config.TextColumn("商品名", disabled=True),
                        "数量": st.column_config.NumberColumn("数量", min_value=1, step=1),
                        "表示額": st.column_config.NumberColumn("表示額", format="¥%d"),
                        "外or内": st.column_config.TextColumn("外or内", disabled=True),
                        "税率": st.column_config.NumberColumn("税率", format="%d%%", disabled=True),
                        "本体価": st.column_config.NumberColumn("本体価", format="¥%d"),
                        "税額": st.column_config.NumberColumn("税額", format="¥%d"),
                        "税込価": st.column_config.NumberColumn("税込価", format="¥%d"),
                        "単価": st.column_config.NumberColumn("単価", format="¥%d", disabled=True),
                        "分類": st.column_config.TextColumn("分類", width="medium"),
                        "人物": st.column_config.SelectboxColumn("人物", options=person_options, default="家族"),
                        "名目": st.column_config.SelectboxColumn("名目", options=purpose_options, default="日常") if purpose_options else st.column_config.TextColumn("名目"),
                        "費目": st.column_config.SelectboxColumn("費目", options=expense_category_options) if expense_category_options else st.column_config.TextColumn("費目", help="2次分類（自動判定）"),
                    },
                    use_container_width=True
                )

                # 更新ボタン
                col_update1, col_update2 = st.columns([3, 1])
                with col_update1:
                    save_to_dict = st.checkbox("辞書に保存（次回から自動判定に活用）", value=True, key="save_to_dict_check")
                with col_update2:
                    update_button = st.button("💾 データを更新", type="primary")

                if update_button:
                    # 変更されたデータをDBに保存
                    updated_count = 0
                    for idx, row in edited_df.iterrows():
                        std_id = row["_std_id"]
                        transaction_id = row["_transaction_id"]
                        if std_id:
                            # 本体単価を逆算（本体価 ÷ 数量）
                            quantity = row["数量"]
                            base_price = row["本体価"]
                            std_unit_price = base_price // quantity if quantity > 0 else 0

                            # 分類の処理: 現時点では入力された分類を minor_category として保存
                            # 将来的には階層構造のマスターテーブルから major/middle を自動判定
                            category_value = row["分類"]

                            # Rawdata_RECEIPT_itemsを更新
                            try:
                                db.table("Rawdata_RECEIPT_items").update({
                                    "std_unit_price": std_unit_price,
                                    "tax_amount": row["税額"],
                                    "std_amount": row["税込価"],
                                    "minor_category": category_value,  # 分類を更新
                                    "person": row["人物"],  # 人物を更新
                                    "purpose": row["名目"],  # 名目を更新
                                }).eq("id", transaction_id).execute()
                                updated_count += 1

                                # 辞書に保存（オプション）
                                if save_to_dict and category_value and row["人物"] and row["名目"]:
                                    # トランザクションデータから商品情報を取得
                                    product_name = row["商品名"]
                                    official_name = row["正式名"] or ""
                                    general_name = ""  # 現時点では未実装

                                    # 店舗名を取得
                                    shop_name = receipt.get("shop_name", "")

                                    # 辞書に保存
                                    save_to_dictionary(
                                        db=db,
                                        shop_name=shop_name,
                                        product_name=product_name,
                                        official_name=official_name,
                                        general_name=general_name,
                                        category=category_value,
                                        person=row["人物"],
                                        purpose=row["名目"]
                                    )

                            except Exception as e:
                                st.error(f"更新エラー ({row['商品名']}): {e}")

                    if updated_count > 0:
                        msg = f"✅ {updated_count}件のデータを更新しました"
                        if save_to_dict:
                            msg += "（辞書に保存しました）"
                        st.success(msg)
                        st.rerun()  # ページをリロードしてレシート情報サマリーも更新

                # 合計金額・税額サマリー
                total = sum(
                    t.get("std_amount", 0) or 0
                    for t in transactions.data
                )
                # 税率別の集計
                # 税額合計（割引行を含む全トランザクションから計算）
                total_tax_8 = sum(
                    t.get("tax_amount", 0) or 0
                    for t in transactions.data
                    if t.get("tax_rate") == 8
                )
                total_tax_10 = sum(
                    t.get("tax_amount", 0) or 0
                    for t in transactions.data
                    if t.get("tax_rate") == 10
                )
                # 税込合計（8%、10%それぞれ）
                total_amount_8 = sum(
                    t.get("std_amount", 0) or 0
                    for t in transactions.data
                    if t.get("tax_rate") == 8
                )
                total_amount_10 = sum(
                    t.get("std_amount", 0) or 0
                    for t in transactions.data
                    if t.get("tax_rate") == 10
                )

                # 税額サマリー取得（レシート記載値との比較）
                # Rawdata_RECEIPT_shopsテーブルから税率別の小計・税額を取得
                try:
                    # レシート記載の税額と小計を取得
                    receipt_tax_8 = receipt.get('tax_8_amount')
                    receipt_tax_10 = receipt.get('tax_10_amount')
                    receipt_8_subtotal = receipt.get('tax_8_subtotal')
                    receipt_10_subtotal = receipt.get('tax_10_subtotal')

                    # 簡易的なsummaryオブジェクトを作成
                    if receipt_tax_10 is not None or receipt_tax_8 is not None:
                        tax_summary = type('obj', (object,), {
                            'data': [{
                                'tax_10_subtotal': receipt_10_subtotal,
                                'tax_10_amount': receipt_tax_10,
                                'tax_8_subtotal': receipt_8_subtotal,
                                'tax_8_amount': receipt_tax_8,
                                'calculated_matches_actual': True  # 仮の値
                            }]
                        })()
                    else:
                        tax_summary = None
                except Exception as e:
                    # エラーの場合はスキップ
                    tax_summary = None

                # ========================================
                # レシート情報サマリー（詳細版）
                # ========================================

                # 計算値を集計（税抜・税込両方）
                calc_subtotal_excluding_tax = sum(  # 税抜合計（外税用）
                    (t.get("std_unit_price", 0) or 0) * (t.get("quantity") or 1)
                    for t in transactions.data
                )
                calc_total = sum(  # 税込合計
                    t.get("std_amount", 0) or 0
                    for t in transactions.data
                )

                # 税率別の対象額（8%, 10%）
                # 10%対象額（税込）- 内税用
                calc_10_amount_including_tax = sum(
                    t.get("std_amount", 0) or 0
                    for t in transactions.data
                    if t.get("tax_rate") == 10
                )
                # 10%対象額（税抜）- 外税用
                calc_10_amount_excluding_tax = sum(
                    (t.get("std_unit_price", 0) or 0) * (t.get("quantity") or 1)
                    for t in transactions.data
                    if t.get("tax_rate") == 10
                )
                # 8%対象額（税込）- 内税用
                calc_8_amount_including_tax = sum(
                    t.get("std_amount", 0) or 0
                    for t in transactions.data
                    if t.get("tax_rate") == 8
                )
                # 8%対象額（税抜）- 外税用
                calc_8_amount_excluding_tax = sum(
                    (t.get("std_unit_price", 0) or 0) * (t.get("quantity") or 1)
                    for t in transactions.data
                    if t.get("tax_rate") == 8
                )

                # レシート記載値
                receipt_subtotal = receipt.get("subtotal_amount")
                receipt_total = receipt.get("total_amount_check")

                # 整合性チェック
                if tax_summary and tax_summary.data:
                    summary = tax_summary.data[0]
                    match_icon = "✅" if summary.get("calculated_matches_actual") else "⚠️"
                    st.subheader(f"📊 レシート情報サマリー（{tax_display_type}） {match_icon}")
                else:
                    st.subheader(f"📊 レシート情報サマリー（{tax_display_type}）")
                    summary = None

                # テーブルデータを作成（内税・外税で項目名と計算方法を変える）
                table_data = {
                    "項目": [],
                    "レシート記載": [],
                    "計算値（差分）": []
                }

                # 1. 小計
                if tax_display_type == "内税":
                    # 内税の場合：小計 = 税込合計
                    table_data["項目"].append("小計（税込）")
                    table_data["レシート記載"].append(f"¥{receipt_subtotal:,}" if receipt_subtotal is not None else "—")

                    subtotal_diff = calc_total - receipt_subtotal if receipt_subtotal else 0
                    if subtotal_diff != 0:
                        table_data["計算値（差分）"].append(f"¥{calc_total:,}|{subtotal_diff:+,}円")
                    else:
                        table_data["計算値（差分）"].append(f"¥{calc_total:,}|")
                else:
                    # 外税の場合：小計 = 税抜合計
                    table_data["項目"].append("小計（税抜）")
                    table_data["レシート記載"].append(f"¥{receipt_subtotal:,}" if receipt_subtotal is not None else "—")

                    subtotal_diff = calc_subtotal_excluding_tax - receipt_subtotal if receipt_subtotal else 0
                    if subtotal_diff != 0:
                        table_data["計算値（差分）"].append(f"¥{calc_subtotal_excluding_tax:,}|{subtotal_diff:+,}円")
                    else:
                        table_data["計算値（差分）"].append(f"¥{calc_subtotal_excluding_tax:,}|")

                # 2. 税率別の対象額と税額
                if summary:
                    if tax_display_type == "内税":
                        # 内税10%対象額（税込）
                        table_data["項目"].append("内税10%対象額（税込）")
                        tax_10_subtotal = summary.get('tax_10_subtotal')
                        tax_10_amount = summary.get('tax_10_amount')
                        # レシート記載 = 対象額（税抜） + 税額 = 税込
                        if tax_10_subtotal is not None and tax_10_amount is not None:
                            receipt_10_including = tax_10_subtotal + tax_10_amount
                            table_data["レシート記載"].append(f"¥{receipt_10_including:,}")
                        else:
                            table_data["レシート記載"].append("—")
                            receipt_10_including = None

                        # 計算値 = 10%対象商品の税込価合計
                        amount_diff = calc_10_amount_including_tax - receipt_10_including if receipt_10_including else 0
                        if amount_diff != 0:
                            table_data["計算値（差分）"].append(f"¥{calc_10_amount_including_tax:,}|{amount_diff:+,}円")
                        else:
                            table_data["計算値（差分）"].append(f"¥{calc_10_amount_including_tax:,}|")

                        # 内税10%税額
                        table_data["項目"].append("内税10%税額")
                        table_data["レシート記載"].append(
                            f"¥{tax_10_amount:,}" if tax_10_amount is not None else "—"
                        )
                        # 計算値は total_tax_10 を使う（実際に計算した税額）
                        tax_10_diff = total_tax_10 - tax_10_amount if tax_10_amount else 0
                        if tax_10_diff != 0:
                            table_data["計算値（差分）"].append(f"¥{total_tax_10:,}|{tax_10_diff:+d}円")
                        else:
                            table_data["計算値（差分）"].append(f"¥{total_tax_10:,}|")

                        # 内税8%対象額（税込）
                        if calc_8_amount_including_tax > 0:
                            table_data["項目"].append("内税8%対象額（税込）")
                            tax_8_subtotal = summary.get('tax_8_subtotal')
                            tax_8_amount = summary.get('tax_8_amount')
                            if tax_8_subtotal is not None and tax_8_amount is not None:
                                receipt_8_including = tax_8_subtotal + tax_8_amount
                                table_data["レシート記載"].append(f"¥{receipt_8_including:,}")
                            else:
                                table_data["レシート記載"].append("—")
                                receipt_8_including = None

                            amount_diff = calc_8_amount_including_tax - receipt_8_including if receipt_8_including else 0
                            if amount_diff != 0:
                                table_data["計算値（差分）"].append(f"¥{calc_8_amount_including_tax:,}|{amount_diff:+,}円")
                            else:
                                table_data["計算値（差分）"].append(f"¥{calc_8_amount_including_tax:,}|")

                            # 内税8%税額
                            table_data["項目"].append("内税8%税額")
                            table_data["レシート記載"].append(
                                f"¥{tax_8_amount:,}" if tax_8_amount is not None else "—"
                            )
                            # 計算値は total_tax_8 を使う（実際に計算した税額）
                            tax_8_diff = total_tax_8 - tax_8_amount if tax_8_amount else 0
                            if tax_8_diff != 0:
                                table_data["計算値（差分）"].append(f"¥{total_tax_8:,}|{tax_8_diff:+d}円")
                            else:
                                table_data["計算値（差分）"].append(f"¥{total_tax_8:,}|")

                    else:  # 外税
                        # 外税10%対象額（税抜）
                        table_data["項目"].append("外税10%対象額（税抜）")
                        tax_10_subtotal = summary.get('tax_10_subtotal')
                        table_data["レシート記載"].append(
                            f"¥{tax_10_subtotal:,}" if tax_10_subtotal is not None else "—"
                        )

                        # 計算値 = 10%対象商品の税抜価合計
                        amount_diff = calc_10_amount_excluding_tax - tax_10_subtotal if tax_10_subtotal else 0
                        if amount_diff != 0:
                            table_data["計算値（差分）"].append(f"¥{calc_10_amount_excluding_tax:,}|{amount_diff:+,}円")
                        else:
                            table_data["計算値（差分）"].append(f"¥{calc_10_amount_excluding_tax:,}|")

                        # 外税10%税額
                        table_data["項目"].append("外税10%税額")
                        tax_10_amount = summary.get('tax_10_amount')
                        table_data["レシート記載"].append(
                            f"¥{tax_10_amount:,}" if tax_10_amount is not None else "—"
                        )
                        # 計算値は total_tax_10 を使う（実際に計算した税額）
                        tax_10_diff = total_tax_10 - tax_10_amount if tax_10_amount else 0
                        if tax_10_diff != 0:
                            table_data["計算値（差分）"].append(f"¥{total_tax_10:,}|{tax_10_diff:+d}円")
                        else:
                            table_data["計算値（差分）"].append(f"¥{total_tax_10:,}|")

                        # 外税8%対象額（税抜）
                        if calc_8_amount_excluding_tax > 0:
                            table_data["項目"].append("外税8%対象額（税抜）")
                            tax_8_subtotal = summary.get('tax_8_subtotal')
                            table_data["レシート記載"].append(
                                f"¥{tax_8_subtotal:,}" if tax_8_subtotal is not None else "—"
                            )

                            amount_diff = calc_8_amount_excluding_tax - tax_8_subtotal if tax_8_subtotal else 0
                            if amount_diff != 0:
                                table_data["計算値（差分）"].append(f"¥{calc_8_amount_excluding_tax:,}|{amount_diff:+,}円")
                            else:
                                table_data["計算値（差分）"].append(f"¥{calc_8_amount_excluding_tax:,}|")

                            # 外税8%税額
                            table_data["項目"].append("外税8%税額")
                            tax_8_amount = summary.get('tax_8_amount')
                            table_data["レシート記載"].append(
                                f"¥{tax_8_amount:,}" if tax_8_amount is not None else "—"
                            )
                            # 計算値は total_tax_8 を使う（実際に計算した税額）
                            tax_8_diff = total_tax_8 - tax_8_amount if tax_8_amount else 0
                            if tax_8_diff != 0:
                                table_data["計算値（差分）"].append(f"¥{total_tax_8:,}|{tax_8_diff:+d}円")
                            else:
                                table_data["計算値（差分）"].append(f"¥{total_tax_8:,}|")
                else:
                    # tax_summaryがない場合
                    if tax_display_type == "内税":
                        table_data["項目"].append("内税10%対象額（税込）")
                        table_data["レシート記載"].append("—")
                        table_data["計算値（差分）"].append(f"¥{calc_10_amount_including_tax:,}|")

                        table_data["項目"].append("内税10%税額")
                        table_data["レシート記載"].append("—")
                        table_data["計算値（差分）"].append(f"¥{total_tax_10:,}|")
                    else:
                        table_data["項目"].append("外税10%対象額（税抜）")
                        table_data["レシート記載"].append("—")
                        table_data["計算値（差分）"].append(f"¥{calc_10_amount_excluding_tax:,}|")

                        table_data["項目"].append("外税10%税額")
                        table_data["レシート記載"].append("—")
                        table_data["計算値（差分）"].append(f"¥{total_tax_10:,}|")

                # 3. 税込合計
                table_data["項目"].append("税込合計")
                table_data["レシート記載"].append(f"¥{receipt_total:,}" if receipt_total is not None else "—")

                total_diff = calc_total - receipt_total if receipt_total else 0
                if total_diff != 0:
                    table_data["計算値（差分）"].append(f"¥{calc_total:,}|{total_diff:+,}円")
                else:
                    table_data["計算値（差分）"].append(f"¥{calc_total:,}|")

                # HTMLテーブルを作成（差分を赤字で表示）
                html_table = '<table style="width:100%; border-collapse: collapse;">'
                html_table += '<tr style="background-color: #f0f0f0;"><th style="padding: 8px; text-align: left; border: 1px solid #ddd;">項目</th><th style="padding: 8px; text-align: left; border: 1px solid #ddd;">レシート記載</th><th style="padding: 8px; text-align: left; border: 1px solid #ddd;">計算値（差分）</th></tr>'

                for i in range(len(table_data["項目"])):
                    item = table_data["項目"][i]
                    receipt_text = table_data["レシート記載"][i]
                    calc_val = table_data["計算値（差分）"][i]

                    # "|" で分割して、差分部分を赤字にする
                    if "|" in calc_val:
                        parts = calc_val.split("|")
                        if parts[1]:  # 差分がある場合
                            calc_display = f'{parts[0]} <span style="color: red;">({parts[1]})</span>'
                        else:  # 差分がない場合
                            calc_display = parts[0]
                    else:
                        calc_display = calc_val

                    html_table += f'<tr><td style="padding: 8px; border: 1px solid #ddd;">{item}</td><td style="padding: 8px; border: 1px solid #ddd;">{receipt_text}</td><td style="padding: 8px; border: 1px solid #ddd;">{calc_display}</td></tr>'

                html_table += '</table>'
                st.markdown(html_table, unsafe_allow_html=True)

                # CSVダウンロードボタンを追加
                summary_df = pd.DataFrame(table_data)
                csv_data = summary_df.to_csv(index=False, encoding='utf-8-sig')
                st.download_button(
                    label="📥 CSV形式でダウンロード",
                    data=csv_data,
                    file_name=f"receipt_summary_{receipt['shop_name']}_{receipt['transaction_date']}.csv",
                    mime="text/csv",
                    key="download_receipt_summary"
                )

                # 店名・日付（レシートから取得）
                st.text(f"店名: {receipt['shop_name']}")
                st.text(f"取引日: {receipt['transaction_date']}")
                st.text(f"レシート合計: ¥{receipt.get('total_amount_check', 0):,}")

                # 確認状態（レシート単位）
                if receipt["is_verified"]:
                    st.success("✅ このレシートは確認済みです")
                else:
                    st.warning(f"⏸️ このレシートは未確認です")

                # アクションボタン
                st.divider()

                col1, col2, col3 = st.columns(3)

                with col1:
                    if st.button("✅ 全て承認", key="approve_all"):
                        # レシート単位で承認
                        db.table("Rawdata_RECEIPT_shops") \
                            .update({"is_verified": True}) \
                            .eq("id", log["receipt_id"]) \
                            .execute()
                        st.success("承認しました")
                        st.rerun()

                with col2:
                    if st.button("📝 個別編集", key="edit_mode"):
                        st.session_state.edit_mode = True
                        st.rerun()

                with col3:
                    if st.button("🗑️ 全て削除", key="delete_all"):
                        # レシートを削除（CASCADE で子・孫も削除される）
                        db.table("Rawdata_RECEIPT_shops") \
                            .delete() \
                            .eq("id", log["receipt_id"]) \
                            .execute()
                        st.warning("削除しました")
                        st.rerun()

                # 個別編集モード
                if st.session_state.get("edit_mode"):
                    st.divider()
                    st.subheader("個別編集")

                    for idx, t in enumerate(transactions.data):
                        # Note: standardized data is now directly in t
                        amount = t.get('std_amount', 0) or 0
                        with st.expander(f"{t['product_name']} (¥{amount:,})"):
                            col_a, col_b, col_c = st.columns(3)

                            with col_a:
                                new_product = st.text_input(
                                    "商品名",
                                    value=t["product_name"],
                                    key=f"prod_{idx}"
                                )

                                new_amount = st.number_input(
                                    "金額",
                                    value=amount,
                                    key=f"amt_{idx}"
                                )

                                new_tax_included = st.number_input(
                                    "内税額",
                                    value=t.get("tax_amount", 0) or 0,
                                    key=f"tax_{idx}"
                                )

                                new_official_name = st.text_input(
                                    "正式名",
                                    value=t.get("official_name") or "",
                                    key=f"official_{idx}"
                                )

                                new_item_name = st.text_input(
                                    "物品名",
                                    value=t.get("item_name") or "",
                                    key=f"item_{idx}"
                                )

                            with col_b:
                                # 分類（最下層のみ表示）
                                current_category = t.get("minor_category") or t.get("middle_category") or t.get("major_category") or ""
                                new_category = st.text_input(
                                    "分類",
                                    value=current_category,
                                    key=f"category_{idx}",
                                    placeholder="例: 根菜"
                                )

                            with col_c:
                                # 人物（プルダウン）
                                current_person = t.get("person") or "家族"
                                person_index = person_options.index(current_person) if current_person in person_options else 0
                                new_person = st.selectbox(
                                    "人物",
                                    options=person_options,
                                    index=person_index,
                                    key=f"person_{idx}"
                                )

                                # 名目（プルダウン）
                                current_purpose = t.get("purpose") or "日常"
                                if current_purpose not in purpose_options:
                                    purpose_options.append(current_purpose)
                                purpose_index = purpose_options.index(current_purpose) if current_purpose in purpose_options else 0
                                new_purpose = st.selectbox(
                                    "名目",
                                    options=purpose_options,
                                    index=purpose_index,
                                    key=f"purpose_{idx}"
                                )

                            if st.button("💾 更新", key=f"update_{idx}"):
                                # Rawdata_RECEIPT_items を更新（全データを同じテーブルに保存）
                                db.table("Rawdata_RECEIPT_items").update({
                                    "product_name": new_product,
                                    "item_name": new_item_name,
                                    "std_amount": new_amount,
                                    "tax_amount": new_tax_included,
                                    "official_name": new_official_name,
                                    "minor_category": new_category,  # 分類を更新
                                    "person": new_person,
                                    "purpose": new_purpose
                                }).eq("id", t["id"]).execute()

                                # レシート全体を確認済みにマーク
                                db.table("Rawdata_RECEIPT_shops").update({
                                    "is_verified": True
                                }).eq("id", log["receipt_id"]).execute()

                                st.success("更新しました")
                                st.rerun()

                    if st.button("編集モード終了", key="exit_edit"):
                        st.session_state.edit_mode = False
                        st.rerun()

            else:
                st.warning("トランザクションデータが見つかりません")

        elif log["status"] == "failed":
            st.error(f"❌ 処理エラー: {log.get('error_message', '不明')}")
            st.info("errorsフォルダを確認してください")

        else:
            st.info("トランザクションデータがありません")


def show_product_classification_tab():
    """商品分類管理タブ"""
    st.header("🏷️ 商品分類管理")

    # サブタブ
    subtab1, subtab2, subtab3, subtab4, subtab5 = st.tabs([
        "📥 日次承認インボックス",
        "🔍 承認済み商品の検索・編集",
        "✅ クラスタ承認",
        "🌳 カテゴリ管理",
        "⚙️ ルール管理"
    ])

    with subtab1:
        show_daily_inbox()

    with subtab2:
        show_approved_products_search()

    with subtab3:
        show_bulk_clustering()

    with subtab4:
        show_category_tree()

    with subtab5:
        show_rule_management()


def show_daily_inbox():
    """日次承認インボックス（信号機UI）"""
    st.subheader("📥 日次承認インボックス")
    st.info("新規商品の分類結果を確認・承認します")

    # 初期化: セッション状態でデータを保持してリロードを最小化
    if 'pending_products_data' not in st.session_state or st.session_state.get('refresh_pending_products', False):
        try:
            # データ取得（初回または明示的なリフレッシュ時のみ）
            all_pending = db.table('Rawdata_NETSUPER_items').select(
                'id, product_name, product_name_normalized, general_name, category_id, classification_confidence, organization'
            ).eq('needs_approval', True).execute()

            st.session_state['pending_products_data'] = all_pending.data
            st.session_state['refresh_pending_products'] = False
        except Exception as e:
            st.error(f"データ取得エラー: {e}")
            return

    # セッション状態からデータを取得
    all_pending_data = st.session_state.get('pending_products_data', [])

    # 承認待ち商品を信頼度別に分類
    try:
        # Pythonで信頼度別に分類（NULL対応）
        high_data = []
        medium_data = []
        low_data = []

        for product in all_pending_data:
            confidence = product.get('classification_confidence')
            if confidence is not None and confidence >= 0.9:
                high_data.append(product)
            elif confidence is not None and confidence >= 0.7:
                medium_data.append(product)
            else:
                # confidence < 0.7 または NULL
                low_data.append(product)

        # データを返す形式に合わせる
        class Response:
            def __init__(self, data):
                self.data = data

        high = Response(high_data)
        medium = Response(medium_data)
        low = Response(low_data)

        # タブ表示
        tab_high, tab_medium, tab_low = st.tabs([
            f"🟢 高信頼度 ({len(high.data)}件)",
            f"🟡 中信頼度 ({len(medium.data)}件)",
            f"🔴 要確認 ({len(low.data)}件)"
        ])

        with tab_high:
            render_product_approval_table(high.data, "高信頼度", "🟢")

        with tab_medium:
            render_product_approval_table(medium.data, "中信頼度", "🟡")

        with tab_low:
            render_product_approval_table(low.data, "要確認", "🔴")

    except Exception as e:
        st.error(f"データ取得エラー: {e}")


def render_product_approval_table(products, title, icon):
    """商品承認テーブル表示"""
    if not products:
        st.info(f"{title}: 該当なし")
        return

    st.markdown(f"### {icon} {title} ({len(products)}件)")

    df = pd.DataFrame([{
        "id": p["id"],  # 内部IDは非表示だが承認処理で使用
        "承認": False,
        "product_name": p.get("product_name", ""),
        "product_name_normalized": p.get("product_name_normalized", ""),
        "general_name": p.get("general_name", "未設定"),
        "店舗": p.get("organization", ""),
        "信頼度": f"{p.get('classification_confidence', 0):.1%}" if p.get('classification_confidence') else "—"
    } for p in products])

    edited_df = st.data_editor(
        df,
        column_config={
            "承認": st.column_config.CheckboxColumn("承認", default=False, width="small"),
            "product_name": st.column_config.TextColumn("product_name", width="large", disabled=False),
            "product_name_normalized": st.column_config.TextColumn("product_name_normalized", width="large", disabled=False),
            "general_name": st.column_config.TextColumn("general_name", width="medium", disabled=False),
            "店舗": st.column_config.TextColumn("店舗", width="medium", disabled=True),  # 店舗は編集不可
            "信頼度": st.column_config.TextColumn("信頼度", width="small", disabled=True)  # 信頼度は編集不可
        },
        column_order=["承認", "product_name", "product_name_normalized", "general_name", "店舗", "信頼度"],
        hide_index=True,
        use_container_width=True,
        key=f"table_{title}"
    )

    # ボタンを横に並べる
    col1, col2 = st.columns(2)

    with col1:
        if st.button(f"✏️ 修正を反映", key=f"btn_save_{title}"):
            checked_rows = edited_df[edited_df["承認"] == True]
            if len(checked_rows) > 0:
                for _, row in checked_rows.iterrows():
                    # 修正内容のみ保存（承認はしない）
                    db.table('Rawdata_NETSUPER_items').update({
                        "product_name": row['product_name'],
                        "product_name_normalized": row['product_name_normalized'],
                        "general_name": row['general_name']
                    }).eq('id', row['id']).execute()
                st.success(f"{len(checked_rows)}件の修正を反映しました（未承認のまま）")
                # データをリフレッシュ
                st.session_state['refresh_pending_products'] = True
                st.rerun()
            else:
                st.warning("反映する項目を選択してください")

    with col2:
        if st.button(f"✅ 修正して承認", key=f"btn_approve_{title}"):
            checked_rows = edited_df[edited_df["承認"] == True]
            if len(checked_rows) > 0:
                for _, row in checked_rows.iterrows():
                    # 修正内容も保存して承認
                    db.table('Rawdata_NETSUPER_items').update({
                        "product_name": row['product_name'],
                        "product_name_normalized": row['product_name_normalized'],
                        "general_name": row['general_name'],
                        "needs_approval": False
                    }).eq('id', row['id']).execute()
                st.success(f"{len(checked_rows)}件を修正して承認しました")
                # データをリフレッシュ
                st.session_state['refresh_pending_products'] = True
                st.rerun()
            else:
                st.warning("承認する項目を選択してください")


def show_bulk_clustering():
    """一括クラスタリング承認"""
    st.subheader("✅ クラスタ一括承認")
    st.info("Geminiが自動生成したクラスタを確認・承認します")

    # 初期化: セッション状態でデータを保持してリロードを最小化
    if 'clustering_data' not in st.session_state or st.session_state.get('refresh_clustering', False):
        try:
            # データ取得（初回または明示的なリフレッシュ時のみ）
            clusters = db.table('99_tmp_gemini_clustering').select(
                '*'
            ).eq('approval_status', 'pending').execute()

            st.session_state['clustering_data'] = clusters.data
            st.session_state['refresh_clustering'] = False
        except Exception as e:
            st.error(f"データ取得エラー: {e}")
            return

    # セッション状態からデータを取得
    clusters_data = st.session_state.get('clustering_data', [])

    if not clusters_data:
        st.success("承認待ちのクラスタはありません")
        return

    try:
        # カテゴリマスタを取得
        categories = db.table('60_ms_categories').select('id, name').execute()
        category_map = {cat["name"]: cat["id"] for cat in categories.data}

        st.markdown(f"### 全{len(clusters_data)}クラスタ")

        df = pd.DataFrame([{
            "id": c["id"],
            "承認": False,
            "一般名詞": c["general_name"],
            "カテゴリ": c.get("category_name", "食材"),
            "商品数": len(c["product_ids"]),
            "信頼度": f"{c['confidence_avg']:.1%}",
            "商品例": ", ".join(c["product_names"][:3]) + "..."
        } for c in clusters_data])

        edited_df = st.data_editor(
            df,
            column_config={
                "id": st.column_config.TextColumn("ID", disabled=True, width="small"),
                "承認": st.column_config.CheckboxColumn("承認", default=False),
                "一般名詞": st.column_config.TextColumn("一般名詞", width="medium"),
                "カテゴリ": st.column_config.SelectboxColumn(
                    "カテゴリ",
                    options=list(category_map.keys()),
                    width="medium"
                ),
                "商品数": st.column_config.NumberColumn("商品数", format="%d"),
                "信頼度": st.column_config.TextColumn("信頼度", width="small"),
                "商品例": st.column_config.TextColumn("商品例（先頭3件）", width="large")
            },
            hide_index=True,
            use_container_width=True
        )

        if st.button("選択を一括承認", type="primary"):
            approved_rows = edited_df[edited_df["承認"] == True]

            if len(approved_rows) == 0:
                st.warning("承認する項目を選択してください")
            else:
                # 最初の行のカテゴリIDを取得
                category_name = approved_rows.iloc[0]["カテゴリ"]
                category_id = category_map[category_name]

                for _, row in approved_rows.iterrows():
                    # クラスタ情報を取得
                    cluster = next(c for c in clusters_data if c["id"] == row["id"])
                    general_name = cluster["general_name"]
                    product_ids = cluster["product_ids"]
                    product_names = cluster["product_names"]
                    confidence = cluster["confidence_avg"]

                    # Tier 1: 各商品名 → general_name のマッピング
                    for product_name in set(product_names):
                        db.table('MASTER_Product_generalize').upsert({
                            "raw_keyword": product_name,
                            "general_name": general_name,
                            "confidence_score": confidence,
                            "source": "gemini_batch"
                        }, on_conflict="raw_keyword,general_name").execute()

                    # Tier 2: general_name + context → category_id
                    db.table('MASTER_Product_classify').upsert({
                        "general_name": general_name,
                        "source_type": "online_shop",
                        "workspace": "shopping",
                        "doc_type": "online shop",
                        "organization": None,
                        "category_id": category_id,
                        "approval_status": "approved",
                        "confidence_score": confidence
                    }, on_conflict="general_name,source_type,workspace,doc_type,organization").execute()

                    # Rawdata_NETSUPER_itemsを更新
                    for product_id in product_ids:
                        db.table('Rawdata_NETSUPER_items').update({
                            "general_name": general_name,
                            "category_id": category_id,
                            "needs_approval": False,
                            "classification_confidence": confidence
                        }).eq('id', product_id).execute()

                    # クラスタのステータスを更新
                    db.table('99_tmp_gemini_clustering').update({
                        "approval_status": "approved"
                    }).eq('id', row["id"]).execute()

                st.success(f"{len(approved_rows)}件のクラスタを承認しました")
                # データをリフレッシュ
                st.session_state['refresh_clustering'] = True
                st.session_state['refresh_pending_products'] = True  # 商品データも更新
                st.rerun()

    except Exception as e:
        st.error(f"エラー: {e}")


def show_category_tree():
    """カテゴリツリー編集"""
    st.subheader("🌳 カテゴリ管理")
    st.info("1次分類（商品カテゴリー）、2次分類（費目）、名目を管理します")

    # 3つのサブタブ
    cat_tab1, cat_tab2, cat_tab3 = st.tabs([
        "📦 1次分類（商品カテゴリー）",
        "💰 2次分類（費目）",
        "🎯 名目"
    ])

    with cat_tab1:
        show_product_category_management()

    with cat_tab2:
        show_expense_category_management()

    with cat_tab3:
        show_purpose_management()


def show_product_category_management():
    """1次分類（商品カテゴリー）管理"""
    st.markdown("### 📦 1次分類（商品カテゴリー）")
    st.info("商品の物理的カテゴリー（文房具、ゲームソフト、食材など）")

    try:
        # カテゴリ取得
        categories = db.table('MASTER_Categories_product').select('*').order('name').execute()

        if not categories.data:
            st.warning("カテゴリがありません。新規追加してください。")
        else:
            # ツリー構築
            def build_tree(parent_id=None, level=0):
                items = []
                for cat in categories.data:
                    if cat.get("parent_id") == parent_id:
                        items.append({
                            "id": cat["id"],
                            "name": cat["name"],
                            "level": level,
                            "description": cat.get("description", ""),
                            "parent_id": parent_id
                        })
                        items.extend(build_tree(cat["id"], level + 1))
                return items

            tree = build_tree()

            # ツリー表示
            st.markdown("#### 現在のカテゴリツリー")

            for item in tree:
                indent = "　" * item["level"] * 2
                icon = "📁" if item["level"] == 0 else "📄"

                col1, col2 = st.columns([4, 1])
                with col1:
                    desc_text = f" ({item['description']})" if item['description'] else ""
                    st.markdown(f"{indent}{icon} {item['name']}{desc_text}")
                with col2:
                    if st.button("🗑️", key=f"del_prod_{item['id']}", help="削除"):
                        db.table('MASTER_Categories_product').delete().eq('id', item['id']).execute()
                        st.success("削除しました")
                        st.rerun()

        st.divider()

        # 新規追加フォーム
        st.markdown("#### 新規カテゴリ追加")

        col1, col2, col3 = st.columns(3)

        with col1:
            new_name = st.text_input("カテゴリ名", key="new_prod_cat_name", placeholder="例: 野菜")

        with col2:
            parent_options = {"（親なし）": None}
            if categories.data:
                parent_options.update({cat["name"]: cat["id"] for cat in categories.data})
            selected_parent = st.selectbox("親カテゴリ", options=list(parent_options.keys()), key="new_prod_cat_parent")

        with col3:
            new_desc = st.text_input("説明（任意）", key="new_prod_cat_desc", placeholder="例: 生鮮野菜")

        if st.button("追加", type="primary", key="add_prod_cat"):
            if new_name:
                parent_id = parent_options[selected_parent]
                db.table('MASTER_Categories_product').insert({
                    "name": new_name,
                    "parent_id": parent_id,
                    "description": new_desc if new_desc else None
                }).execute()
                st.success(f"カテゴリ「{new_name}」を追加しました")
                st.rerun()
            else:
                st.warning("カテゴリ名を入力してください")

    except Exception as e:
        st.error(f"エラー: {e}")
        import traceback
        st.code(traceback.format_exc())


def show_expense_category_management():
    """2次分類（費目）管理"""
    st.markdown("### 💰 2次分類（費目）")
    st.info("家計簿の費目（食費、教育費、娯楽費など）")

    try:
        # 費目取得
        expense_cats = db.table('MASTER_Categories_expense').select('*').order('display_order').execute()

        if expense_cats.data:
            st.markdown("#### 現在の費目一覧")

            # テーブル表示
            df_data = []
            for cat in expense_cats.data:
                df_data.append({
                    "id": cat["id"],
                    "名前": cat["name"],
                    "説明": cat.get("description", ""),
                    "表示順": cat.get("display_order", 100)
                })

            df = pd.DataFrame(df_data)

            # データエディタで表示・編集
            edited_df = st.data_editor(
                df,
                hide_index=True,
                column_config={
                    "id": None,  # 非表示
                    "名前": st.column_config.TextColumn("名前", width="medium"),
                    "説明": st.column_config.TextColumn("説明", width="large"),
                    "表示順": st.column_config.NumberColumn("表示順", width="small")
                },
                num_rows="dynamic",  # 行の追加・削除を許可
                use_container_width=True,
                key="expense_cat_editor"
            )

            # 更新ボタン
            if st.button("💾 変更を保存", key="save_expense_cats"):
                for idx, row in edited_df.iterrows():
                    cat_id = row.get("id")
                    if cat_id:
                        # 既存データを更新
                        db.table('MASTER_Categories_expense').update({
                            "name": row["名前"],
                            "description": row["説明"],
                            "display_order": int(row["表示順"])
                        }).eq("id", cat_id).execute()
                st.success("変更を保存しました")
                st.rerun()

        st.divider()

        # 新規追加フォーム
        st.markdown("#### 新規費目追加")

        col1, col2, col3 = st.columns(3)

        with col1:
            new_name = st.text_input("費目名", key="new_exp_cat_name", placeholder="例: 娯楽費")

        with col2:
            new_desc = st.text_input("説明（任意）", key="new_exp_cat_desc", placeholder="例: ゲーム、趣味など")

        with col3:
            new_order = st.number_input("表示順", min_value=1, value=100, key="new_exp_cat_order")

        if st.button("追加", type="primary", key="add_exp_cat"):
            if new_name:
                db.table('MASTER_Categories_expense').insert({
                    "name": new_name,
                    "description": new_desc if new_desc else None,
                    "display_order": new_order
                }).execute()
                st.success(f"費目「{new_name}」を追加しました")
                st.rerun()
            else:
                st.warning("費目名を入力してください")

    except Exception as e:
        st.error(f"エラー: {e}")
        import traceback
        st.code(traceback.format_exc())


def show_purpose_management():
    """名目管理"""
    st.markdown("### 🎯 名目")
    st.info("状況に応じて拡張可能な名目（日常、旅行、学校行事など）")

    try:
        # 名目取得
        purposes = db.table('MASTER_Categories_purpose').select('*').order('display_order').execute()

        if purposes.data:
            st.markdown("#### 現在の名目一覧")

            # テーブル表示
            df_data = []
            for purpose in purposes.data:
                df_data.append({
                    "id": purpose["id"],
                    "名前": purpose["name"],
                    "説明": purpose.get("description", ""),
                    "表示順": purpose.get("display_order", 100)
                })

            df = pd.DataFrame(df_data)

            # データエディタで表示・編集
            edited_df = st.data_editor(
                df,
                hide_index=True,
                column_config={
                    "id": None,  # 非表示
                    "名前": st.column_config.TextColumn("名前", width="medium"),
                    "説明": st.column_config.TextColumn("説明", width="large"),
                    "表示順": st.column_config.NumberColumn("表示順", width="small")
                },
                num_rows="dynamic",  # 行の追加・削除を許可
                use_container_width=True,
                key="purpose_editor"
            )

            # 更新ボタン
            if st.button("💾 変更を保存", key="save_purposes"):
                for idx, row in edited_df.iterrows():
                    purpose_id = row.get("id")
                    if purpose_id:
                        # 既存データを更新
                        db.table('MASTER_Categories_purpose').update({
                            "name": row["名前"],
                            "description": row["説明"],
                            "display_order": int(row["表示順"])
                        }).eq("id", purpose_id).execute()
                st.success("変更を保存しました")
                st.rerun()

        st.divider()

        # 新規追加フォーム
        st.markdown("#### 新規名目追加")

        col1, col2, col3 = st.columns(3)

        with col1:
            new_name = st.text_input("名目名", key="new_purpose_name", placeholder="例: 習い事")

        with col2:
            new_desc = st.text_input("説明（任意）", key="new_purpose_desc", placeholder="例: 習い事・塾など")

        with col3:
            new_order = st.number_input("表示順", min_value=1, value=100, key="new_purpose_order")

        if st.button("追加", type="primary", key="add_purpose"):
            if new_name:
                db.table('MASTER_Categories_purpose').insert({
                    "name": new_name,
                    "description": new_desc if new_desc else None,
                    "display_order": new_order
                }).execute()
                st.success(f"名目「{new_name}」を追加しました")
                st.rerun()
            else:
                st.warning("名目名を入力してください")

    except Exception as e:
        st.error(f"エラー: {e}")
        import traceback
        st.code(traceback.format_exc())


def show_rule_management():
    """2次分類決定ルール管理"""
    st.subheader("⚙️ 2次分類決定ルール")
    st.info("名目、人物、1次分類から2次分類（費目）を決定するルールを管理します")

    try:
        # ルール一覧を取得（ビューを使用）
        rules = db.table("v_expense_category_rules").select("*").execute()

        if rules.data:
            st.markdown("### 現在のルール一覧")
            st.caption("優先度が高い順に表示（100=最優先）")

            # テーブル表示
            df_data = []
            for rule in rules.data:
                df_data.append({
                    "id": rule["id"],
                    "名目": rule.get("purpose") or "（任意）",
                    "人物": rule.get("person") or "（任意）",
                    "1次分類": rule.get("product_category") or "（任意）",
                    "→ 費目": rule["expense_category"],
                    "優先度": rule["priority"],
                    "作成者": rule.get("created_by") or "手動"
                })

            df = pd.DataFrame(df_data)

            # データエディタで表示
            st.dataframe(
                df,
                hide_index=True,
                column_config={
                    "id": None,  # 非表示
                    "名目": st.column_config.TextColumn("名目", width="small"),
                    "人物": st.column_config.TextColumn("人物", width="small"),
                    "1次分類": st.column_config.TextColumn("1次分類", width="medium"),
                    "→ 費目": st.column_config.TextColumn("→ 費目", width="medium"),
                    "優先度": st.column_config.NumberColumn("優先度", width="small"),
                    "作成者": st.column_config.TextColumn("作成者", width="small")
                },
                use_container_width=True
            )

            # 削除機能
            st.markdown("#### ルールの削除")
            rule_to_delete = st.selectbox(
                "削除するルールを選択",
                options=[f"{r['名目']} + {r['人物']} + {r['1次分類']} → {r['→ 費目']}" for r in df_data],
                key="rule_to_delete"
            )

            if st.button("🗑️ 選択したルールを削除", key="delete_rule"):
                # 選択されたルールのIDを取得
                selected_idx = [f"{r['名目']} + {r['人物']} + {r['1次分類']} → {r['→ 費目']}" for r in df_data].index(rule_to_delete)
                rule_id = df_data[selected_idx]["id"]

                db.table("MASTER_Rules_expense_mapping").delete().eq("id", rule_id).execute()
                st.success("ルールを削除しました")
                st.rerun()

        st.divider()

        # 新規ルール追加フォーム
        st.markdown("### 新規ルール追加")

        # 選択肢を取得
        purposes = db.table("MASTER_Categories_purpose").select("id, name").order("display_order").execute()
        purpose_options = {"（任意）": None}
        if purposes.data:
            purpose_options.update({p["name"]: p["id"] for p in purposes.data})

        product_cats = db.table("MASTER_Categories_product").select("id, name").order("name").execute()
        product_cat_options = {"（任意）": None}
        if product_cats.data:
            product_cat_options.update({c["name"]: c["id"] for c in product_cats.data})

        expense_cats = db.table("MASTER_Categories_expense").select("id, name").order("display_order").execute()
        expense_cat_options = {}
        if expense_cats.data:
            expense_cat_options.update({c["name"]: c["id"] for c in expense_cats.data})

        person_options_list = ["（任意）", "家族", "パパ", "ママ", "絵麻", "育哉"]

        # フォーム
        col1, col2, col3, col4 = st.columns(4)

        with col1:
            selected_purpose = st.selectbox("名目", options=list(purpose_options.keys()), key="new_rule_purpose")

        with col2:
            selected_person = st.selectbox("人物", options=person_options_list, key="new_rule_person")

        with col3:
            selected_product_cat = st.selectbox("1次分類", options=list(product_cat_options.keys()), key="new_rule_product_cat")

        with col4:
            selected_expense_cat = st.selectbox("→ 費目（必須）", options=list(expense_cat_options.keys()), key="new_rule_expense_cat")

        # 優先度を自動計算
        priority = 50  # デフォルト
        if selected_purpose != "（任意）" and selected_person != "（任意）" and selected_product_cat != "（任意）":
            priority = 80  # 全て指定
        elif selected_purpose != "（任意）" and (selected_person != "（任意）" or selected_product_cat != "（任意）"):
            priority = 90  # 名目 + (人物 or 1次分類)
        elif selected_purpose != "（任意）":
            priority = 100  # 名目のみ
        elif selected_person != "（任意）" and selected_product_cat != "（任意）":
            priority = 50  # 人物 + 1次分類
        elif selected_product_cat != "（任意）":
            priority = 30  # 1次分類のみ

        st.info(f"優先度: {priority} （自動計算）")

        if st.button("➕ ルールを追加", type="primary", key="add_rule"):
            if not selected_expense_cat or selected_expense_cat not in expense_cat_options:
                st.warning("費目を選択してください")
            else:
                # ルールを挿入
                purpose_id = purpose_options[selected_purpose]
                person_value = None if selected_person == "（任意）" else selected_person
                product_cat_id = product_cat_options[selected_product_cat]
                expense_cat_id = expense_cat_options[selected_expense_cat]

                db.table("MASTER_Rules_expense_mapping").insert({
                    "purpose_id": purpose_id,
                    "person": person_value,
                    "product_category_id": product_cat_id,
                    "expense_category_id": expense_cat_id,
                    "priority": priority,
                    "created_by": "manual"
                }).execute()

                st.success(f"ルールを追加しました: {selected_purpose} + {selected_person} + {selected_product_cat} → {selected_expense_cat}")
                st.rerun()

    except Exception as e:
        st.error(f"エラー: {e}")
        import traceback
        st.code(traceback.format_exc())


def show_approved_products_search():
    """承認済み商品の検索・編集"""
    st.subheader("🔍 承認済み商品の検索・編集")
    st.info("承認済み商品を検索して修正できます")

    try:
        # 検索フィルター
        st.markdown("### 検索条件")

        col1, col2, col3 = st.columns(3)

        with col1:
            # 店舗フィルター
            stores_result = db.table('Rawdata_NETSUPER_items').select('organization').execute()
            unique_stores = sorted(list(set([p.get('organization', '') for p in stores_result.data if p.get('organization')])))
            selected_store = st.selectbox("店舗", options=["全て"] + unique_stores)

        with col2:
            # 商品名検索
            search_text = st.text_input("商品名（部分一致）")

        with col3:
            # カテゴリフィルター
            categories_result = db.table('60_ms_categories').select('id, name').execute()
            category_options = {"全て": None}
            category_options.update({cat["name"]: cat["id"] for cat in categories_result.data})
            selected_category = st.selectbox("カテゴリ", options=list(category_options.keys()))

        # 検索ボタン
        if st.button("🔍 検索", type="primary"):
            # クエリ構築
            query = db.table('Rawdata_NETSUPER_items').select(
                'id, product_name, product_name_normalized, general_name, category_id, organization, classification_confidence'
            ).eq('needs_approval', False)  # 承認済みのみ

            # 店舗フィルター
            if selected_store != "全て":
                query = query.eq('organization', selected_store)

            # カテゴリフィルター
            if selected_category != "全て":
                query = query.eq('category_id', category_options[selected_category])

            # 商品名検索（部分一致）
            if search_text:
                query = query.ilike('product_name', f'%{search_text}%')

            # 実行
            results = query.limit(100).execute()

            if not results.data:
                st.warning("該当する商品が見つかりませんでした")
                return

            st.success(f"{len(results.data)}件見つかりました（最大100件表示）")

            # 結果表示・編集
            st.markdown("### 検索結果")

            df = pd.DataFrame([{
                "id": p["id"],
                "選択": False,
                "product_name": p.get("product_name", ""),
                "product_name_normalized": p.get("product_name_normalized", ""),
                "general_name": p.get("general_name", ""),
                "店舗": p.get("organization", ""),
                "信頼度": f"{p.get('classification_confidence', 0):.1%}" if p.get('classification_confidence') else "—"
            } for p in results.data])

            edited_df = st.data_editor(
                df,
                column_config={
                    "選択": st.column_config.CheckboxColumn("選択", default=False, width="small"),
                    "product_name": st.column_config.TextColumn("product_name", width="large", disabled=False),
                    "product_name_normalized": st.column_config.TextColumn("product_name_normalized", width="large", disabled=False),
                    "general_name": st.column_config.TextColumn("general_name", width="medium", disabled=False),
                    "店舗": st.column_config.TextColumn("店舗", width="medium", disabled=True),
                    "信頼度": st.column_config.TextColumn("信頼度", width="small", disabled=True)
                },
                column_order=["選択", "product_name", "product_name_normalized", "general_name", "店舗", "信頼度"],
                hide_index=True,
                use_container_width=True,
                key="approved_products_table"
            )

            # 修正保存ボタン
            if st.button("💾 選択した商品の修正を保存"):
                checked_rows = edited_df[edited_df["選択"] == True]
                if len(checked_rows) > 0:
                    for _, row in checked_rows.iterrows():
                        db.table('Rawdata_NETSUPER_items').update({
                            "product_name": row['product_name'],
                            "product_name_normalized": row['product_name_normalized'],
                            "general_name": row['general_name']
                        }).eq('id', row['id']).execute()
                    st.success(f"{len(checked_rows)}件の修正を保存しました")
                    st.rerun()
                else:
                    st.warning("保存する商品を選択してください")

    except Exception as e:
        st.error(f"エラー: {e}")


if __name__ == "__main__":
    main()
