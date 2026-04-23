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

# 職務経歴書（3・4ページ）のみのHTML
html_cv_only = """
<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;500;700&display=swap" rel="stylesheet">
<style>
  body { margin: 0; padding: 0; font-family: 'Noto Sans JP', sans-serif; color: #222; }
  .page {
    display: flex !important;
    flex-direction: column !important;
    justify-content: flex-start !important;
    height: 297mm !important;
    width: 210mm !important;
    padding: 15mm 20mm !important;
    box-sizing: border-box !important;
    background: #fff !important;
    break-after: page !important;
    overflow: hidden !important;
  }
  .page > .cv-section-title, .page > table, .page > .achievement-block {
    margin-top: auto !important;
  }
  .page > *:first-child { margin-top: 0 !important; }
  
  .cv-header { text-align: center; font-size: 20px; font-weight: 700; letter-spacing: 2px; margin-bottom: 5px; }
  .cv-date-name { display: flex; justify-content: space-between; font-size: 12px; margin-bottom: 15px; border-bottom: 2px solid #000; padding-bottom: 5px;}
  .cv-section-title { border-left: 5px solid #222; border-bottom: 1px solid #ccc; padding: 4px 10px; font-size: 16px; font-weight: 700; margin-top: 25px; margin-bottom: 15px; }
  .cv-text { font-size: 13px; line-height: 1.6; margin-bottom: 0; text-align: justify; }
  .cv-highlight { font-weight: 700; background: linear-gradient(transparent 60%, #ffff99 60%); }
  .cv-project-table { width: 100%; border-collapse: collapse; font-size: 12px; margin-bottom: 20px; }
  .cv-project-table th, .cv-project-table td { border: 1px solid #999; padding: 10px; vertical-align: top; line-height: 1.4; }
  .cv-project-table th { background-color: #f2f2f2; }
  .cv-list { margin: 0; padding-left: 20px; } .cv-list li { margin-bottom: 6px; }
  .company-info { font-size: 12px; margin-bottom: 10px; color: #444; }
  .achievement-block { margin-bottom: 20px; border: 1px solid #ddd; padding: 12px; border-radius: 4px; }
  .achievement-title { font-size: 14px; font-weight: 700; color: #333; margin-bottom: 8px; display: flex; align-items: center; }
  .achievement-title span { background-color: #444; color: #fff; padding: 2px 6px; font-size: 11px; margin-right: 8px; border-radius: 3px; }
  .achievement-content dt { font-weight: 700; font-size: 12px; float: left; clear: left; width: 50px; color: #555; }
  .achievement-content dd { font-size: 12px; margin-left: 50px; margin-bottom: 8px; line-height: 1.6; }
  .bold { font-weight: 700; }
  .page:last-child { justify-content: flex-start !important; }
  .page:last-child > * { margin-top: 15px !important; }
  .page:last-child > *:first-child { margin-top: 0 !important; }
</style>
</head>
<body>
  <!-- ==================== 職務経歴書 ページ1 (元の3ページ目) ==================== -->
  <div class="page">
    <div class="cv-header">職務経歴書</div>
    <div class="cv-date-name">
      <span>2026年 4月 22日 現在</span>
      <span style="font-size: 16px; font-weight: bold;">氏名：大久保 宜紀</span>
    </div>
    <div class="cv-section-title">■ 職務要約</div>
    <div class="cv-text">
      株式会社祥伝社に新卒入社し、雑誌編集部（約10ヶ月）と業務部（約26年8ヶ月）の双方で出版業務に一貫して携わってまいりました。<br><br>
      編集部では読者視点を意識した企画・取材・原稿作成を行いました。1999年に業務部へ異動してからは、出版物の原価管理・資材購買・納期調整・精算処理など、製造工程を企画段階から納品まで管理。年間最大250点以上の出版物の進行管理を担当し、印刷会社・製紙メーカーとの価格交渉や、社内各部署（編集・営業・経理）との折衝を主導しました。現在は課長（マネージャー）として、年齢・経験の異なる部下をまとめ、部署運営の中核を担っています。<br><br>
      私の最大の特徴は、単なる工程管理者にとどまらず、「改善策を実装してきた点」です。手作業だったバーコード生成のシステム化、発注先の分散化による人為ミス削減、発注システム刷新によるトラブル削減など、コスト最適化と事故の削減で成果を上げました。
    </div>
    <div class="cv-section-title">■ 職務経歴詳細</div>
    <div class="company-info">
      <strong>株式会社祥伝社</strong> （1998年4月〜現在 / 在籍期間：約28年）
    </div>
    <table class="cv-project-table">
      <tr><th style="width: 25%;">期間 / 所属</th><th style="width: 75%;">担当業務・役割</th></tr>
      <tr>
        <td><div class="bold">1999年8月〜現在</div><div>業務部（制作部）</div></td>
        <td>
          出版物の製造に関する進行管理・原価管理・資材調達・発注・精算を統合的に実施。
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

  <!-- ==================== 職務経歴書 ページ2 (元の4ページ目) ==================== -->
  <div class="page">
    <div class="cv-section-title">■ 業務改善実績</div>
    <div class="achievement-block">
      <div class="achievement-title"><span>DX</span>バーコード自動生成システムの導入</div>
      <dl class="achievement-content"><dd>外注費月額約5万円 → 0円。工数削減を実現。転記ミスをゼロにしました。</dd></dl>
    </div>
    <div class="cv-section-title">■ 保有スキル・知識</div>
    <table class="cv-project-table">
      <tr><th style="width: 25%;">カテゴリ</th><th style="width: 75%;">詳細</th></tr>
      <tr><td class="bold">購買・調達</td><td>用紙、印刷資材の価格交渉、調達先選定、契約条件設計。簿記2級。</td></tr>
      <tr><td class="bold">IT・自動化</td><td>GASやPythonを用いたツール開発。生成AIを活用した業務効率化。</td></tr>
    </table>
  </div>
</body>
</html>
"""

font_config = FontConfiguration()
html = HTML(string=html_cv_only, encoding="utf-8")
html.write_pdf(
    "job_experience_perfect.pdf",
    stylesheets=[], # インラインにすべて記述
    font_config=font_config,
    presentational_hints=True
)
print("CV PDF generated successfully.")
