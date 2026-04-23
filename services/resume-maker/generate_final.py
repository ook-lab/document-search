import os
import sys

# GTK環境のセットアップ
if sys.platform == 'win32':
    GTK_PATHS = [r"C:\Program Files\GTK3-Runtime-Win64\bin", r"C:\Program Files (x86)\GTK3-Runtime-Win64\bin"]
    for path in GTK_PATHS:
        if os.path.exists(path):
            os.add_dll_directory(path)
            os.environ['PATH'] = path + os.pathsep + os.environ['PATH']
            break

from weasyprint import HTML, CSS
from weasyprint.text.fonts import FontConfiguration

# お客様の最新HTML
html_content = """
<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>履歴書・職務経歴書_大久保宜紀</title>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;500;700&display=swap" rel="stylesheet">
<style>
  :root { --border-color: #000; }
  body { margin: 0; padding: 20px; background-color: #525659; display: flex; flex-direction: column; align-items: center; font-family: 'Noto Sans JP', sans-serif; color: #222; }
  .page { background-color: #fff; width: 210mm; min-height: 297mm; padding: 15mm 20mm; margin-bottom: 20px; box-shadow: 0 4px 10px rgba(0,0,0,0.3); box-sizing: border-box; position: relative; page-break-after: always; }
  @media print { body { background-color: #fff; padding: 0; } .page { margin: 0; box-shadow: none; padding: 15mm 20mm; page-break-after: always; min-height: 297mm; } }
  .text-center { text-align: center; } .text-right { text-align: right; } .bold { font-weight: 700; }
  .w-year { width: 12%; } .w-month { width: 6%; }
  .resume-title { font-size: 24px; font-weight: 700; letter-spacing: 4px; margin-bottom: 5px; }
  .date-right { font-size: 11px; margin-bottom: 5px; text-align: right; }
  table.cv-table { width: 100%; border-collapse: collapse; font-size: 13px; line-height: 1.4; table-layout: fixed; }
  table.cv-table th, table.cv-table td { border: 1px solid var(--border-color); padding: 6px 8px; vertical-align: middle; }
  .photo-box { width: 30mm; height: 40mm; border: 1px solid var(--border-color); display: flex; align-items: center; justify-content: center; font-size: 10px; color: #666; text-align: center; float: right; margin-left: 10px; margin-bottom: 10px; }
  .name-furigana { font-size: 10px; border-bottom: 1px dashed var(--border-color); padding-bottom: 2px; margin-bottom: 5px; display: block; }
  .name-text { font-size: 22px; font-weight: 700; letter-spacing: 2px; }
  .empty-row td { height: 26px; }
  .cv-header { text-align: center; font-size: 20px; font-weight: 700; letter-spacing: 2px; margin-bottom: 5px; }
  .cv-date-name { display: flex; justify-content: space-between; font-size: 12px; margin-bottom: 15px; border-bottom: 2px solid #000; padding-bottom: 5px;}
  .cv-section-title { border-left: 5px solid #222; border-bottom: 1px solid #ccc; padding: 4px 10px; font-size: 16px; font-weight: 700; margin-top: 25px; margin-bottom: 15px; }
  .cv-text { font-size: 13px; line-height: 1.7; margin-bottom: 15px; text-align: justify; }
  .cv-highlight { font-weight: 700; background: linear-gradient(transparent 60%, #ffff99 60%); }
  .cv-project-table { width: 100%; border-collapse: collapse; font-size: 12px; margin-bottom: 20px; }
  .cv-project-table th, .cv-project-table td { border: 1px solid #999; padding: 10px; vertical-align: top; line-height: 1.5; }
  .cv-project-table th { background-color: #f2f2f2; }
  .cv-list { margin: 0; padding-left: 20px; } .cv-list li { margin-bottom: 6px; }
  .company-info { font-size: 12px; margin-bottom: 10px; color: #444; }
  .achievement-block { margin-bottom: 20px; border: 1px solid #ddd; padding: 12px; border-radius: 4px; }
  .achievement-title { font-size: 14px; font-weight: 700; color: #333; margin-bottom: 8px; display: flex; align-items: center; }
  .achievement-title span { background-color: #444; color: #fff; padding: 2px 6px; font-size: 11px; margin-right: 8px; border-radius: 3px; }
  .achievement-content dt { font-weight: 700; font-size: 12px; float: left; clear: left; width: 50px; color: #555; }
  .achievement-content dd { font-size: 12px; margin-left: 50px; margin-bottom: 8px; line-height: 1.6; }
</style>
</head>
<body>
  <div class="page" id="resume-p1">
    <div style="display: flex; justify-content: space-between; align-items: flex-end; margin-bottom: 5px;">
      <div class="resume-title">履 歴 書</div>
      <div class="date-right">2026年 4月 22日 現在</div>
    </div>
    <div style="display: flex; width: 100%;">
      <div style="flex-grow: 1;">
        <table class="cv-table" style="margin-bottom: 0; border-bottom: none;">
          <tr>
            <td colspan="4" style="height: 60px;">
              <span class="name-furigana">フリガナ　オオクボ ヨシノリ</span>
              <span style="font-size: 12px;">氏名</span>　<span class="name-text">大久保 宜紀</span>
            </td>
          </tr>
          <tr>
            <td style="width: 15%; font-size: 11px;">生年月日</td>
            <td style="width: 45%;">1974年 9月 10日生 （満 51歳）</td>
            <td style="width: 10%; font-size: 11px;">性別</td>
            <td style="width: 30%;" class="text-center">男</td>
          </tr>
        </table>
      </div>
      <div class="photo-box" style="margin-top: 0; margin-right: 0;">
        写真を貼る位置<br><br>(縦40mm×横30mm)
      </div>
    </div>
    <table class="cv-table" style="margin-top: -1px; margin-bottom: 20px;">
      <tr><td style="width: 15%; font-size: 11px;">フリガナ</td><td colspan="3"></td><td style="width: 15%; font-size: 11px;">電話番号</td><td style="width: 35%;">090-7401-0367</td></tr>
      <tr><td rowspan="2" style="font-size: 11px;">現住所</td><td colspan="3" rowspan="2">〒 211-0004<br>神奈川県川崎市中原区新丸子東3-1100-12-M3309</td><td style="font-size: 11px;">E-mail</td><td>ookubo.yoshinori@gmail.com</td></tr>
      <tr><td colspan="2" style="background-color:#f9f9f9;"></td></tr>
    </table>
    <table class="cv-table" style="margin-bottom: 0;">
      <tr><th class="w-year">年</th><th class="w-month">月</th><th>学歴・職歴</th></tr>
      <tr><td class="text-center"></td><td class="text-center"></td><td class="text-center bold">学歴</td></tr>
      <tr><td class="text-center">1990</td><td class="text-center">4</td><td>桐蔭学園高等学校 入学</td></tr>
      <tr><td class="text-center">1993</td><td class="text-center">3</td><td>桐蔭学園高等学校 卒業</td></tr>
      <tr><td class="text-center">1994</td><td class="text-center">4</td><td>慶應義塾大学 文学部 入学</td></tr>
      <tr><td class="text-center">1998</td><td class="text-center">3</td><td>慶應義塾大学 文学部 卒業</td></tr>
      <tr><td class="text-center"></td><td class="text-center"></td><td class="text-center bold">職歴</td></tr>
      <tr><td class="text-center">1998</td><td class="text-center">4</td><td>株式会社 祥伝社 入社</td></tr>
      <tr><td class="text-center">1998</td><td class="text-center">10</td><td>同社 雑誌編集部 配属</td></tr>
      <tr><td class="text-center">1999</td><td class="text-center">8</td><td>同社 業務部（制作部） 異動</td></tr>
      <tr><td class="text-center"></td><td class="text-center"></td><td>現在に至る</td></tr>
      <tr><td class="text-center"></td><td class="text-center"></td><td class="text-right">以上</td></tr>
      <tr class="empty-row"><td></td><td></td><td></td></tr>
      <tr class="empty-row"><td></td><td></td><td></td></tr>
      <tr class="empty-row"><td></td><td></td><td></td></tr>
      <tr class="empty-row"><td></td><td></td><td></td></tr>
      <tr class="empty-row"><td></td><td></td><td></td></tr>
      <tr class="empty-row"><td></td><td></td><td></td></tr>
      <tr class="empty-row"><td></td><td></td><td></td></tr>
      <tr class="empty-row"><td></td><td></td><td></td></tr>
    </table>
  </div>
  <div class="page" id="resume-p2">
    <table class="cv-table">
      <tr><th class="w-year">年</th><th class="w-month">月</th><th>免許・資格</th></tr>
      <tr><td class="text-center">1995</td><td class="text-center">10</td><td>普通自動車第一種運転免許 取得（AT限定）</td></tr>
      <tr><td class="text-center">2017</td><td class="text-center">9</td><td>TOEIC 公開テスト 905点 取得</td></tr>
      <tr><td class="text-center">2023</td><td class="text-center">7</td><td>日商簿記検定2級 取得</td></tr>
    </table>
    <table class="cv-table">
      <tr><td style="width: 15%;">通勤時間</td><td>約　　時間　　分</td><td style="width: 20%;">扶養家族数</td><td>2 人</td></tr>
      <tr><td>最寄り駅</td><td>　　　線<br>　　　駅</td><td>配偶者</td><td>有</td></tr>
    </table>
    <table class="cv-table" style="height: 300px;">
      <tr><th style="text-align: left; padding: 10px; vertical-align: top;">本人希望記入欄</th></tr>
      <tr><td style="vertical-align: top; padding: 15px; line-height: 2;">希望職種：購買・調達、DX推進・業務改善<br>希望勤務地：東京都内、神奈川県内</td></tr>
    </table>
  </div>
  <div class="page" id="cv-p1">
    <div class="cv-header">職務経歴書</div>
    <div class="cv-date-name"><span>2026年 4月 22日 現在</span><span style="font-size: 16px; font-weight: bold;">氏名：大久保 宜紀</span></div>
    <div class="cv-section-title">■ 職務要約</div>
    <div class="cv-text">株式会社祥伝社に新卒入社し...（中略）</div>
    <div class="cv-section-title">■ 職務経歴詳細</div>
    <table class="cv-project-table">
      <tr><th style="width: 25%;">期間 / 所属</th><th style="width: 75%;">担当業務・役割</th></tr>
      <tr><td><div class="bold">1999年8月〜現在</div><div>業務部（制作部）</div></td><td>出版物の製造に関する進行管理・原価管理・資材調達・発注・精算...（中略）</td></tr>
    </table>
  </div>
  <div class="page" id="cv-p2">
    <div class="cv-section-title">■ 業務改善実績</div>
    <div class="achievement-block">
      <div class="achievement-title"><span>DX</span>バーコード自動生成システムの導入</div>
      <dl class="achievement-content"><dd>外注費月額約5万円 → 0円。工数削減を実現。</dd></dl>
    </div>
  </div>
</body>
</html>
"""

# 究極の「呼吸する」フィッティングCSS
perfect_css = """
@page { size: A4; margin: 0; }
@media print {
    html, body { margin: 0 !important; padding: 0 !important; background: #fff !important; display: block !important; }
    .page {
        display: flex !important;
        flex-direction: column !important;
        justify-content: flex-start !important;
        height: 297mm !important;
        width: 210mm !important;
        overflow: hidden !important; 
        margin: 0 !important;
        padding: 15mm 20mm !important;
        background: #fff !important;
        break-after: page !important;
        box-sizing: border-box !important;
    }
    .page > .cv-section-title, .page > table, .page > .achievement-block {
        margin-top: auto !important;
    }
    .page > *:first-child { margin-top: 0 !important; }
    .cv-text { line-height: 1.6 !important; margin-bottom: 0 !important; }
    .page:last-child { justify-content: flex-start !important; }
    .page:last-child > * { margin-top: 15px !important; }
    .page:last-child > *:first-child { margin-top: 0 !important; }
}
"""

font_config = FontConfiguration()
html = HTML(string=html_content, encoding="utf-8")
html.write_pdf(
    "resume_final_okubo_perfect.pdf",
    stylesheets=[CSS(string=perfect_css)],
    font_config=font_config,
    presentational_hints=True
)
print("PDF generated successfully.")
