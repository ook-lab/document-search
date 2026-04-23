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

# 私の目（サブエージェント）で確認した「85%空白」の惨状を、
# 物理的な「配置固定」によって打破した最終HTML
html_p3_final_fixed = """
<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;500;700&display=swap" rel="stylesheet">
<style>
  @page { size: A4; margin: 0; }
  body { margin: 0; padding: 0; font-family: 'Noto Sans JP', sans-serif; color: #222; background: #fff; line-height: 1.0; }
  
  .page {
    width: 210mm;
    height: 297mm;
    padding: 15mm 20mm;
    box-sizing: border-box;
    background: #fff;
    position: relative;
    display: block !important; /* Flexを捨て、確実なブロック配置へ */
    overflow: hidden;
  }

  .cv-header { text-align: center; font-size: 20px; font-weight: 700; letter-spacing: 4px; margin-bottom: 5px; }
  .cv-date-name { display: flex; justify-content: space-between; font-size: 11px; margin-bottom: 10px; border-bottom: 2px solid #000; padding-bottom: 5px;}
  
  /* セクションタイトルのマージンを徹底的に絞り、経歴がページ内に「留まる」ようにします */
  .cv-section-title { 
    border-left: 5px solid #222; 
    border-bottom: 1px solid #ccc; 
    padding: 3px 10px; 
    font-size: 15px; 
    font-weight: 700; 
    margin-top: 12px !important; 
    margin-bottom: 8px !important;
    break-after: avoid; /* 次の要素との分離を禁止 */
  }
  
  .cv-text { 
    font-size: 12.5px; 
    line-height: 1.55; 
    margin-bottom: 8px; 
    text-align: justify; 
  }
  
  .cv-highlight { font-weight: 700; background: linear-gradient(transparent 60%, #ffff99 60%); }
  
  .company-info { font-size: 12px; margin-top: 5px; margin-bottom: 5px; font-weight: 700; }
  
  /* 表のサイズを1ページに収めるために極限まで調律 */
  .cv-project-table { width: 100%; border-collapse: collapse; font-size: 11.5px; margin-top: 5px; table-layout: fixed; }
  .cv-project-table th, .cv-project-table td { border: 1px solid #999; padding: 6px 8px; vertical-align: top; line-height: 1.4; }
  .cv-project-table th { background-color: #f2f2f2; }
  
  .cv-list { margin: 0; padding-left: 15px; }
  .cv-list li { margin-bottom: 2px; }
  
  .bold { font-weight: 700; }
</style>
</head>
<body>
  <div class="page">
    <div class="cv-header">職務経歴書</div>
    <div class="cv-date-name">
      <span>2026年 4月 22日 現在</span>
      <span class="bold">氏名：大久保 宜紀</span>
    </div>

    <div class="cv-section-title">■ 職務要約</div>
    <div class="cv-text">
      株式会社祥伝社に新卒入社し、雑誌編集部（約10ヶ月）と業務部（約26年8ヶ月）の双方で出版業務に一貫して携わってまいりました。<br>
      編集部では読者視点を意識した企画・取材・原稿作成を行いました。1999年に業務部へ異動してからは、出版物の原価管理・資材購買・納期調整・精算処理など、製造工程を企画段階から納品まで管理。年間最大250点以上の出版物の進行管理を担当し、印刷会社・製紙メーカーとの価格交渉や、社内各部署（編集・営業・経理）との折衝を主導しました。現在は課長（マネージャー）として、年齢・経験の異なる部下をまとめ、部署運営の中核を担っています。<br>
      私の最大の特徴は、単なる工程管理者にとどまらず、<span class="cv-highlight">「改善策を実装してきた点」</span>です。手作業だったバーコード生成のシステム化、発注先の分散化による人為ミス削減、発注システム刷新によるトラブル削減など、コスト最適化と事故の削減で成果を上げました。<br>
      40歳を過ぎてからも自己研鑽を継続し、TOEIC 905点・日商簿記2級を取得。語学力や財務知識、独学で身につけたGAS・生成AI等のITスキルを掛け合わせ、調達・業務設計・DX推進といった領域で他業界にも貢献できる人材を目指しています。
    </div>

    <div class="cv-section-title">■ 職務経歴詳細</div>
    <div class="company-info">
      <strong>株式会社祥伝社</strong> （1998年4月〜現在 / 在籍期間：約28年）<br>
      <span style="font-weight: normal; font-size: 10px;">事業内容：出版業 / 資本金：1,000万円 / 売上高：31億円 / 従業員数：41名 / 雇用形態：正社員</span>
    </div>

    <table class="cv-project-table">
      <tr>
        <th style="width: 25%;">期間 / 所属</th>
        <th style="width: 75%;">担当業務・役割</th>
      </tr>
      <tr>
        <td>
          <div class="bold">1999年8月〜現在</div>
          <div style="margin: 3px 0;">業務部（制作部）</div>
          <div class="bold">【役職】</div>
          <div>課長 / マネージャー</div>
          <div style="color: #666; font-size: 10px;">(部下：1〜4名)</div>
        </td>
        <td>
          出版物の製造に関する進行管理・原価管理・資材調達・発注・精算を統合的に実施し、品質・コスト・納期の最適化を図る。
          <br>
          <div class="bold">1. 購買・資材調達および原価管理</div>
          <ul class="cv-list">
            <li>文庫、コミック、雑誌など様々な出版物の原価計算（制作原価・編集原価・予定売上・間接費）。</li>
            <li>予算制約下での代替仕様（用紙変更・色数削減等）を提案し、品質・コストを両立。</li>
            <li>印刷会社・製紙メーカーとの直接交渉による、単価・納期・品質の最適化。</li>
            <li>災害リスクを踏まえた調達先分散と、代替銘柄の事前選定を実施。</li>
          </ul>
          <div class="bold">2. 進行管理・部署間調整・マネジメント</div>
          <ul class="cv-list">
            <li>企画確定から納品・精算までの管理。編集・営業・外注と連携した納期と工程管理。</li>
            <li>トラブルに対して事故情報を共有し、チェックポイントを共有。</li>
            <li>発注ロットに依存しない安定契約のため「最低保証金額」を導入し製本単価を適正化。</li>
            <li>印刷/用紙担当・発注/精算担当の分業化による、チェック機能の内蔵と人為ミス削減。</li>
          </ul>
        </td>
      </tr>
    </table>
  </div>
</body>
</html>
"""

font_config = FontConfiguration()
html = HTML(string=html_p3_final_fixed, encoding="utf-8")
html.write_pdf(
    "resume_p3_final_confirmed.pdf",
    stylesheets=[],
    font_config=font_config,
    presentational_hints=True
)
print("P3 Final Confirmed PDF generated successfully.")
