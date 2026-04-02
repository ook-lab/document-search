from flask import Flask, render_template, request
import os

app = Flask(__name__)

# 印刷時の「見切れ」「はみ出し」を一層させ、機能を追加する統合注入用コード
INJECTED_PRINT_CSS = """
<style>
@media print {
    @page { 
        size: A4 portrait; 
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
    * { 
        box-sizing: border-box !important;
        max-width: 100% !important; 
        word-wrap: break-word !important; 
        overflow-wrap: break-word !important; 
    }
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

    /* ▼ ご要望があった、特定クラスやIDへの「強制的な改ページ」追加 ▼ */
    .explanation-box, .guide-box, #answer-sheet {
        page-break-before: always !important;
        break-before: page !important;
    }

    /* 印刷時にボタン類を非表示にする */
    .a4-floating-print-btn, button, .edit-buttons { 
        display: none !important; 
    }
}

/* 画面プレビュー時のフローティングボタン装飾 */
@media screen {
    .a4-floating-print-btn {
        position: fixed;
        bottom: 30px;
        right: 30px;
        padding: 16px 32px;
        background-color: #e91e63;
        color: white;
        font-size: 18px;
        font-weight: bold;
        border: none;
        border-radius: 50px;
        cursor: pointer;
        z-index: 2147483647; /* 確実に最前面へ */
        box-shadow: 0 5px 15px rgba(233, 30, 99, 0.4);
        transition: transform 0.2s, background-color 0.2s;
    }
    .a4-floating-print-btn:hover {
        background-color: #d81b60;
        transform: scale(1.05);
    }
}
</style>

<!-- どんなHTMLが来ても画面右下に「PDF化」ボタンを自動生成するスクリプト -->
<script>
document.addEventListener("DOMContentLoaded", function() {
    if (!document.getElementById("a4-injected-print-btn")) {
        var btn = document.createElement("button");
        btn.id = "a4-injected-print-btn";
        btn.className = "a4-floating-print-btn";
        btn.innerText = "📄 A4でPDF化する";
        btn.onclick = function() { window.print(); };
        document.body.appendChild(btn);
    }
});
</script>
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
