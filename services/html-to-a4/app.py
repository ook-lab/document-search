from flask import Flask, render_template, request
import os

app = Flask(__name__)

# 印刷時の「見切れ」「はみ出し」を一切許さない絶対的かつ汎用的なCSS
INJECTED_PRINT_CSS = """
<style>
@media print {
    @page { 
        size: A4; 
        margin: 15mm !important; 
    }
    html, body { 
        margin: 0 !important; 
        padding: 0 !important; 
        width: 100% !important; 
        max-width: 100% !important;
        background: transparent !important;
        overflow-x: hidden !important; 
    }
    /* 全ての要素に強制的に適用し、独自のwidth指定（794pxなど）を無効化 */
    * { 
        box-sizing: border-box !important;
        max-width: 100% !important; 
        word-wrap: break-word !important; 
        overflow-wrap: break-word !important; 
    }
    /* テーブル、画像、SVGの幅も強制的に収める */
    table {
        width: 100% !important;
        max-width: 100% !important;
        table-layout: fixed !important;
        border-collapse: collapse !important;
    }
    img, svg, canvas, iframe {
        max-width: 100% !important;
        height: auto !important;
    }
}
</style>
"""

@app.route('/', methods=['GET'])
def index():
    # 入力画面を表示
    return render_template('index.html')

@app.route('/preview', methods=['POST'])
def preview():
    # 入力されたHTMLを受け取る
    html_content = request.form.get('html_content', '')
    
    # 完全なHTML（<head>がある場合）は、閉じタグの直前に最適化CSSを自動注入
    if '</head>' in html_content:
        # これにより、ユーザーの元のスタイルは崩さず、印刷用のガードだけを追加します
        html_content = html_content.replace('</head>', f'{INJECTED_PRINT_CSS}\n</head>')
    else:
        # 単純なテキストや部分的なHTMLしか送られなかった場合は、完全なHTMLの形にする
        html_content = f"<!DOCTYPE html><html><head><meta charset='utf-8'>{INJECTED_PRINT_CSS}</head><body>{html_content}</body></html>"

    # プレビュー兼印刷用の画面として直接完全なHTMLを返す
    return html_content

if __name__ == '__main__':
    app.run(debug=True, port=5051)
