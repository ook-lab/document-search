from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import japanize_matplotlib  # 日本語フォント対応

from shared.common.database.client import DatabaseClient


OUTPUT_DIR = Path("reports")


def fetch_agg(start_date: str, end_date: str, group_by: str) -> pd.DataFrame:
    # バッチ処理なので service_role を使用
    db = DatabaseClient(use_service_role=True).client

    # DB側のRPC (fn_kakeibo_report_agg) を呼び出す
    resp = db.rpc(
        "fn_kakeibo_report_agg",
        {"p_start_date": start_date, "p_end_date": end_date, "p_group_by": group_by},
    ).execute()

    rows = resp.data or []
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="start_date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--to", dest="end_date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--group-by", dest="group_by", default="category_major",
                        choices=["category_major", "category_minor", "institution", "merchant", "month"])
    parser.add_argument("--top", dest="top_n", type=int, default=15)
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(exist_ok=True)

    print(f"Fetching report: {args.start_date} ~ {args.end_date} (by {args.group_by})")
    df = fetch_agg(args.start_date, args.end_date, args.group_by)

    if df.empty:
        print("集計結果が空です。期間や取り込み状況を確認してください。")
        return

    df = df.sort_values("amount_sum", ascending=False).reset_index(drop=True)

    # Excel出力
    excel_path = OUTPUT_DIR / f"kakeibo_report_{args.start_date}_{args.end_date}_{args.group_by}.xlsx"
    with pd.ExcelWriter(excel_path) as w:
        df.to_excel(w, sheet_name="集計", index=False)

    # 円グラフ生成（上位のみ）
    top = df.head(args.top_n)
    plt.figure(figsize=(10, 6))
    plt.pie(top["amount_sum"], labels=top["group_key"], autopct="%1.1f%%", startangle=90, counterclock=False)
    plt.title(f"支出内訳 {args.group_by} ({args.start_date} - {args.end_date})")

    png_path = OUTPUT_DIR / f"kakeibo_pie_{args.start_date}_{args.end_date}_{args.group_by}.png"
    plt.savefig(png_path)
    plt.close()

    print(f"OK: {excel_path}")
    print(f"OK: {png_path}")


if __name__ == "__main__":
    main()
