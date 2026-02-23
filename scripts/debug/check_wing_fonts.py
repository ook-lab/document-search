"""
WINGフォント検証スクリプト

WINGフォントが含まれるPDFを指定ディレクトリから収集し、
フォント名・ページ内容を出力して目視確認できるようにする。

使い方:
    python scripts/debug/check_wing_fonts.py <ディレクトリ>
    python scripts/debug/check_wing_fonts.py <ディレクトリ> --all  # WINGなし含む全PDF
"""

import sys
import re
from pathlib import Path

# パス設定
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

try:
    import pdfplumber
except ImportError:
    print("ERROR: pdfplumber が必要です。pip install pdfplumber")
    sys.exit(1)


WING_PATTERN = re.compile(r'wing', re.IGNORECASE)
SUBSET_RE = re.compile(r'^[A-Z]{6}\+')


def strip_subset(fontname: str) -> str:
    """ABCDEF+FontName → FontName"""
    return SUBSET_RE.sub('', fontname)


def analyze_pdf(pdf_path: Path) -> dict:
    """1つのPDFを解析してページ別情報を返す"""
    pages = []
    has_wing = False

    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            creator = pdf.metadata.get('Creator', '') or ''
            producer = pdf.metadata.get('Producer', '') or ''

            for i, page in enumerate(pdf.pages):
                chars = page.chars or []
                images = page.images or []
                char_count = len(chars)
                image_count = len(images)

                # フォント収集
                fonts = set()
                for c in chars:
                    fn = strip_subset(c.get('fontname', ''))
                    if fn:
                        fonts.add(fn)

                # WINGフォント検出
                wing_fonts = [f for f in fonts if WING_PATTERN.search(f)]
                if wing_fonts:
                    has_wing = True

                # テキスト取得（先頭80文字）
                text = page.extract_text() or ''
                text_preview = text[:80].replace('\n', ' ')

                pages.append({
                    'page': i + 1,
                    'chars': char_count,
                    'images': image_count,
                    'fonts': sorted(fonts),
                    'wing_fonts': wing_fonts,
                    'text_preview': text_preview,
                })

        return {
            'path': pdf_path,
            'creator': creator,
            'producer': producer,
            'has_wing': has_wing,
            'pages': pages,
            'error': None,
        }

    except Exception as e:
        return {
            'path': pdf_path,
            'creator': '',
            'producer': '',
            'has_wing': False,
            'pages': [],
            'error': str(e),
        }


def print_result(result: dict, show_all_pages: bool = False):
    """結果を目視確認しやすい形式で出力"""
    path = result['path']
    sep = '=' * 80

    print(sep)
    print(f"FILE: {path.name}")
    print(f"PATH: {path}")
    if result['error']:
        print(f"ERROR: {result['error']}")
        return

    print(f"Creator : {result['creator'] or '（空）'}")
    print(f"Producer: {result['producer'] or '（空）'}")
    print(f"WINGフォント: {'あり' if result['has_wing'] else 'なし'}")
    print()

    for p in result['pages']:
        wing_marker = ' ← WING' if p['wing_fonts'] else ''
        print(f"  P{p['page']:02d}  chars={p['chars']:4d}  images={p['images']}{wing_marker}")

        if p['wing_fonts']:
            print(f"       WINGフォント: {p['wing_fonts']}")

        if show_all_pages or p['wing_fonts']:
            if p['fonts']:
                print(f"       全フォント: {p['fonts']}")
            if p['text_preview']:
                print(f"       テキスト: {p['text_preview']}")

    print()


def main():
    import argparse
    parser = argparse.ArgumentParser(description='WINGフォント検証スクリプト')
    parser.add_argument('directory', help='PDFを検索するディレクトリ')
    parser.add_argument('--all', action='store_true', help='WINGなし含む全PDFを表示')
    parser.add_argument('--pages', action='store_true', help='全ページの詳細を表示')
    args = parser.parse_args()

    target_dir = Path(args.directory)
    if not target_dir.exists():
        print(f"ERROR: ディレクトリが存在しません: {target_dir}")
        sys.exit(1)

    pdf_files = sorted(target_dir.rglob('*.pdf'))
    if not pdf_files:
        print(f"PDFが見つかりません: {target_dir}")
        sys.exit(0)

    print(f"対象: {target_dir}")
    print(f"PDF数: {len(pdf_files)}")
    print()

    wing_count = 0
    for pdf_path in pdf_files:
        result = analyze_pdf(pdf_path)
        if result['has_wing'] or args.all:
            print_result(result, show_all_pages=args.pages)
            if result['has_wing']:
                wing_count += 1

    print('=' * 80)
    print(f"WINGフォントあり: {wing_count} / {len(pdf_files)} ファイル")


if __name__ == '__main__':
    main()
