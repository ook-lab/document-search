import os


def japanese_font_candidates():
    return [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-jp-Regular.otf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        r"C:\Windows\Fonts\meiryo.ttc",
        r"C:\Windows\Fonts\YuGothM.ttc",
        r"C:\Windows\Fonts\msgothic.ttc",
        "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
    ]


def find_japanese_font():
    for path in japanese_font_candidates():
        if os.path.exists(path):
            return path
    return None


def require_japanese_font():
    font_path = find_japanese_font()
    if font_path:
        return font_path

    raise RuntimeError(
        "日本語フォントファイルが見つかりません。"
        "Cloud Run の Docker イメージには fonts-noto-cjk を含めてください。"
    )
