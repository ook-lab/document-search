"""PDF 1 ページの単語を読み順（y, x）でダンプする。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pdfplumber

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", type=Path)
    ap.add_argument("--page", type=int, default=0)
    ap.add_argument("--filter", type=str, default="")
    args = ap.parse_args()
    if not args.pdf.is_file():
        print("not found", args.pdf, file=sys.stderr)
        return 2
    with pdfplumber.open(args.pdf) as doc:
        p = doc.pages[args.page]
        ph, pw = float(p.height), float(p.width)
        print(f"page {pw:.0f}x{ph:.0f} pt")
        rows = []
        for w in p.extract_words() or []:
            t = (w.get("text") or "").strip()
            if not t:
                continue
            if args.filter and args.filter not in t:
                continue
            rows.append((float(w["top"]), float(w["x0"]), t))
        rows.sort(key=lambda r: (round(r[0], 1), r[1]))
        for top, x0, t in rows:
            print(f"y={top:6.1f} x={x0:6.1f} ny={top/ph:.5f} | {t}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
