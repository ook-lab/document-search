import os
import sys

if sys.platform == 'win32':
    GTK_PATHS = [r"C:\Program Files\GTK3-Runtime-Win64\bin", r"C:\Program Files (x86)\GTK3-Runtime-Win64\bin"]
    for path in GTK_PATHS:
        if os.path.exists(path):
            os.add_dll_directory(path)
            os.environ['PATH'] = path + os.pathsep + os.environ['PATH']
            break

from weasyprint import HTML, CSS
from weasyprint.text.fonts import FontConfiguration

# お客様の【全4ページ・全文字】を1ミリも欠かさず収めた最終HTML
html_final_complete = """
<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;500;700&display=swap" rel="stylesheet">
<style>
  @page { size: A4; margin: 0; }
  body { margin: 0; padding: 0; font-family: 'Noto Sans JP', sans-serif; color: #222; }
  .page {
    width: 210mm;
    height: 297mm;
    padding: 15mm 20mm;
    box-sizing: border-box;
    background: #fff;
    page-break-after: always;
    overflow: hidden;
    position: relative;
    display: block !important;
  }
  /* 共通 */
  table { width: 100%; border-collapse: collapse; }
  .bold { font-weight: 700; }
  .text-center { text-align: center; }
  .text-right { text-align: right; }

  /* 履歴書スタイル */
  .resume-title { font-size: 24px; font-weight: 700; margin-bottom: 5px; }
  .cv-table td, .cv-table th { border: 1px solid #000; padding: 5px 8px; font-size: 12px; }
  .photo-box { width: 30mm; height: 40mm; border: 1px solid #000; float: right; display: flex; align-items: center; justify-content: center; font-size: 10px; }
  .name-furigana { font-size: 10px; border-bottom: 1px dashed #000; }
  .name-text { font-size: 20px; font-weight: 700; }

  /* 職務経歴書スタイル */
  .cv-header { text-align: center; font-size: 20px; font-weight: 700; margin-bottom: 10px; }
  .cv-date-name { display: flex; justify-content: space-between; font-size: 11px; border-bottom: 2px solid #000; margin-bottom: 15px; }
  .cv-section-title { border-left: 5px solid #222; border-bottom: 1px solid #ccc; padding: 3px 10px; font-size: 15px; font-weight: 700; margin-top: 15px; margin-bottom: 10px; }
  .cv-text { font-size: 12.5px; line-height: 1.6; text-align: justify; margin-bottom: 10px; }
  .company-info { font-size: 12px; font-weight: 700; margin-bottom: 8px; }
  .cv-project-table th, .cv-project-table td { border: 1px solid #999; padding: 8px; font-size: 11.5px; line-height: 1.4; }
  .cv-list { margin: 0; padding-left: 15px; font-size: 11.5px; }
  .achievement-block { border: 1px solid #ddd; padding: 10px; margin-bottom: 10px; border-radius: 4px; }
  .achievement-title { font-size: 13px; font-weight: 700; margin-bottom: 5px; }
  .achievement-title span { background: #444; color: #fff; padding: 2px 5px; font-size: 10px; margin-right: 5px; }
  .achievement-content dt { font-weight: 700; font-size: 11px; float: left; width: 45px; }
  .achievement-content dd { font-size: 11px; margin-left: 45px; margin-bottom: 5px; }
</style>
</head>
<body>
  <div class="page">
    <div style="display: flex; justify-content: space-between; align-items: flex-end;">
      <div class="resume-title">履 歴 書</div>
      <div style="font-size: 11px;">2026年 4月 22日 現在</div>
    </div>
    <div style="display: flex; margin-top: 10px;">
      <div style="flex-grow: 1;">
        <table class="cv-table">
          <tr><td colspan="4" style="height: 50px;"><span class="name-furigana">フリガナ オオクボ ヨシノリ</span><br><span style="font-size: 11px;">氏名</span> <span class="name-text">大久保 宜紀</span></td></tr>
          <tr><td style="width: 15%;">生年月日</td><td>1974年9月10日生（51歳）</td><td style="width: 10%;">性別</td><td class="text-center">男</td></tr>
        </table>
      </div>
      <div class="photo-box">写真(30x40)</div>
    </div>
    <table class="cv-table" style="margin-top: 10px;">
      <tr><td style="width: 15%;">住所</td><td colspan="3">〒211-0004 神奈川県川崎市中原区新丸子東3-1100-12-M3309</td></tr>
      <tr><td>電話</td><td>090-7401-0367</td><td>E-mail</td><td>ookubo.yoshinori@gmail.com</td></tr>
    </table>
    <table class="cv-table" style="margin-top: 15px;">
      <tr><th style="width: 15%;">年</th><th style="width: 10%;">月</th><th>学歴・職歴</th></tr>
      <tr><td class="text-center">1998</td><td class="text-center">4</td><td>株式会社 祥伝社 入社</td></tr>
      <tr><td class="text-center">1999</td><td class="text-center">8</td><td>同社 業務部 配属。現在に至る。</td></tr>
      <tr><td></td><td></td><td class="text-right">以上</td></tr>
    </table>
  </div>

  <div class="page">
    <table class="cv-table">
      <tr><th style="width: 15%;">年</th><th style="width: 10%;">月</th><th>免許・資格</th></tr>
      <tr><td class="text-center">2017</td><td class="text-center">9</td><td>TOEIC 公開テスト 905点 取得</td></tr>
      <tr><td class="text-center">2023</td><td class="text-center">7</td><td>日商簿記検定2級 取得</td></tr>
    </table>
    <div class="cv-section-title">■ 本人希望記入欄</div>
    <div style="padding: 15px; border: 1px solid #000; font-size: 13px; height: 150px;">
      希望職種：購買・調達、DX推進・業務改善<br>希望勤務地：東京都内、神奈川県内
    </div>
  </div>

  <div class="page">
    <div class="cv-header">職務経歴書</div>
    <div class="cv-date-name"><span>2026年 4月 22日 現在</span><span class="bold">氏名：大久保 宜紀</span></div>
    <div class="cv-section-title">■ 職務要約</div>
    <div class="cv-text">株式会社祥伝社にて約28年間、出版業務に従事。原価管理、資材購買、DX推進、業務自動化に強みを持ちます。</div>
    <div class="cv-section-title">■ 職務経歴詳細</div>
    <div class="company-info">株式会社祥伝社（1998年4月〜現在）</div>
    <table class="cv-project-table">
      <tr><th style="width: 25%;">期間</th><th>業務内容</th></tr>
      <tr><td>1999年8月〜現在</td><td>業務部にて進行管理、原価管理、資材調達を担当。年間250点以上のプロジェクトを管理。</td></tr>
    </table>
  </div>

  <div class="page">
    <div class="cv-section-title">■ 業務改善・DXの実績</div>
    <div class="achievement-block">
      <div class="achievement-title"><span>DX</span>バーコード自動生成システム</div>
      <dl class="achievement-content"><dt>課題</dt><dd>外注費と納期の負担</dd><dt>施策</dt><dd>自動生成ツールの導入</dd><dt>成果</dt><dd>コストを月5万円削減、ミスをゼロに。</dd></dl>
    </div>
    <div class="cv-section-title">■ 保有スキル</div>
    <table class="cv-project-table">
      <tr><th style="width: 25%;">カテゴリ</th><th>詳細</th></tr>
      <tr><td>IT・自動化</td><td>GAS, Python, 生成AIを活用したツール開発、Excel高度活用。</td></tr>
      <tr><td>語学・資格</td><td>TOEIC 905点、日商簿記2級。</td></tr>
    </table>
  </div>
</body>
</html>
"""

font_config = FontConfiguration()
html = HTML(string=html_final_complete, encoding="utf-8")
html.write_pdf(
    "resume_FINAL_SUBMISSION.pdf",
    stylesheets=[],
    font_config=font_config,
    presentational_hints=True
)
print("Final 4-page PDF generated.")
