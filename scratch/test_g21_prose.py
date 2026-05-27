import sys
from pathlib import Path

# Add services/pipeline-lab to sys.path
repo_root = Path(__file__).resolve().parent.parent
pipeline_lab_path = repo_root / "services" / "pipeline-lab"
sys.path.append(str(pipeline_lab_path))

from dms.pipeline.stage_g.g11_controller import G11Controller

prose_text = """洗足学園小学校 2026年5月11日 保健室 山元直子
ほけんだより 5月
やわらかな若葉がまぶしい季節となりました。新しい学年・学級での生活が始まり、1か月が過ぎましたが、連休明けは疲れが出やすく生活リズムも乱れやすい時期です。早寝・早起き・朝ごはんを心がけ、元気に登校できるようご協力をお願いいたします。
集団生活だからこそ感染症予防が大切です！
水ぼうそう(水痘)について
水ぼうそうは感染力が強く、空気感染・飛沫感染・接触感染によって広がります。ワクチン接種により重症化予防が期待されていますが、接種済みでも発症する場合があります。発熱や発疹などの症状がみられる場合は、早めの受診をお願いいたします。
麻疹(はしか)にも注意！
現在、全国的に麻疹(はしか)の発生が報告されています。麻疹(はしか)は非常に感染力が強く、集団生活では特に注意が必要な感染症です。感染症予防には、手洗い・咳エチケット・十分な睡眠や休養に加え、ワクチン接種の確認が大切です。
先月、学校で水ぼうそう(水痘)の発症がみられ、2Bは学級閉鎖となりました。学校は集団生活の場であり、感染症が広がりやすい環境です。現在、麻疹(はしか)にも注意が必要とされています。ご家庭でも母子健康手帳などでワクチン接種状況の確認をお願いいたします。
※接種時期や回数は、体調や制度変更等により異なる場合があります。
特に、麻疹・風疹(MR)ワクチンの2回接種、水痘ワクチンの接種状況についてご確認ください。"""

print("--- Calling G21 Articles ---")
articles = G11Controller._g21_articles(prose_text)

print("\n--- G21 Output ---")
for i, art in enumerate(articles):
    print(f"Article {i+1} Title: {repr(art['title'])}")
    print(f"Article {i+1} Body:\n{art['body']}")
    print("-" * 40)
