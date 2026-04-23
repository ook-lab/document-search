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

# 3ページ目（職務経歴書 Part 1）の検証用HTML
html_verify = """
<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;500;700&display=swap" rel="stylesheet">
<style>
  @page { size: A4; margin: 0; }
  body { margin: 0; padding: 0; font-family: 'Noto Sans JP', sans-serif; color: #222; background: #fff; }
  .page {
    width: 210mm;
    height: 297mm;
    padding: 15mm 20mm;
    box-sizing: border-box;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }
  .cv-header { text-align: center; font-size: 22px; font-weight: 700; letter-spacing: 4px; margin-bottom: 10px; }
  .cv-date-name { display: flex; justify-content: space-between; font-size: 12px; margin-bottom: 20px; border-bottom: 2px solid #000; padding-bottom: 5px;}
  .cv-section-title { border-left: 5px solid #222; border-bottom: 1px solid #ccc; padding: 4px 10px; font-size: 16px; font-weight: 700; margin-top: 18px; margin-bottom: 12px; }
  .cv-text { font-size: 13px; line-height: 1.65; margin-bottom: 10px; text-align: justify; }
  .company-info { font-size: 13px; margin-bottom: 12px; font-weight: 700; }
  .cv-project-table { width: 100%; border-collapse: collapse; font-size: 12px; margin-top: 10px; }
  .cv-project-table th, .cv-project-table td { border: 1px solid #999; padding: 8px 10px; vertical-align: top; line-height: 1.45; }
  .cv-project-table th { background-color: #f2f2f2; }
  .cv-list { margin: 0; padding-left: 18px; }
  .cv-list li { margin-bottom: 4px; }
  .bold { font-weight: 700; }
</style>
</head>
<body>
  <div class="page">
    <div class="cv-header">職 務 経 歴 書</div>
    <div class="cv-date-name"><span>2026年 4月 22日 現在</span><span class="bold">氏名：大久保 宜紀</span></div>
    <div class="cv-section-title">■ 職務要約</div>
    <div class="cv-text">
      株式会社祥伝社に新卒入社し、雑誌編集部（約10ヶ月）と業務部（約26年8ヶ月）の双方で出版業務に一貫して携わってまいりました。<br><br>
      編集部では読者視点を意識した企画・取材・原稿作成を行いました。1999年に業務部へ異動してからは、出版物の原価管理・資材購買・納期調整・精算処理など、製造工程を企画段階から納品まで管理。年間最大250点以上の出版物の進行管理を担当し、印刷会社・製紙メーカーとの価格交渉や、社内各部署（編集・営業・経理）との折衝を主導しました。現在は課長（マネージャー）として、年齢・経験の異なる部下をまとめ、部署運営の中核を担っています。<br><br>
      私の最大の特徴は、単なる工程管理者にとどまらず、「改善策を実装してきた点」です。手作業だったバーコード生成のシステム化、発注先の分散化による人為ミス削減、発注システム刷新によるトラブル削減など、コスト最適化と事故の削減で成果を上げました。
    </div>
    <div class="cv-section-title">■ 職務経歴詳細</div>
    <div class="company-info"><strong>株式会社祥伝社</strong> （1998年4月〜現在 / 在籍期間：約28年）</div>
    <table class="cv-project-table">
      <tr><th style="width: 25%;">期間 / 所属</th><th style="width: 75%;">担当業務・役割</th></tr>
      <tr>
        <td><div class="bold">1999年8月〜現在</div><div style="margin: 5px 0;">業務部（制作部）</div><div class="bold">【役職】</div><div>課長 / マネージャー</div></td>
        <td>
          出版物の製造に関する進行管理・原価管理・資材調達・発注・精算を統合的に実施し、品質・コスト・納期の最適化を図る。
          <br><br>
          <div class="bold">1. 購買・資材調達および原価管理</div>
          <ul class="cv-list">
            <li>文庫、コミック、雑誌など様々な出版物の原価計算。</li>
            <li>予算制約下での代替仕様を提案し、品質・コストを両立。</li>
            <li>印刷会社・製紙メーカーとの直接交渉による最適化。</li>
          </ul>
        </td>
      </tr>
    </table>
  </div>
</body>
</html>
"""

# PDFと同じエンジンで、私が直接「見る」ためのPNGを出力
html = HTML(string=html_verify, encoding="utf-8")
html.write_png("p3_visual_check.png")
print("Visual verification image generated.")
