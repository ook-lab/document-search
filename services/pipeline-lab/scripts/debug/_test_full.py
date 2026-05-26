import sys, json
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, '.')
import types
_pkg = types.ModuleType('dms.pipeline')
_pkg.__path__ = ['dms/pipeline']
sys.modules.setdefault('dms', types.ModuleType('dms'))
sys.modules['dms'].__path__ = ['dms']
sys.modules['dms.pipeline'] = _pkg

from dms.pipeline.stage_g.g11_controller import G11Controller

# 実際のパイプラインが送るであろうフルテキスト（手紙全文＋係名ブロック）
full_text = """2026年5月3日
6年生保護者各位
第73回役員一同
第73回洗足学園小学校謝恩会　係決定のご案内
先日は、アンケートにご協力頂き、誠にありがとうございました。
下記の通り、本年度の係を決定させて頂きましたので、ご案内申し上げます。
出来るだけ皆様のご希望に添えるようにという主旨のもとではございますが、
配分等を考慮する必要もあり、その上での決定となりました。
何卒、ご理解とご協力の程、よろしくお願い申し上げます。
＊敬称省略　　五十音順
【常任委員】3名
角田（会長）北村（副会長）山浦(会計）
【アルバム】18名（役員含む）
宮内（役員代表）世古（役員）
石村・片山・蒲池・上條・川北・関田・長江・中島・永野
長谷川・堀部・松井(虹）・松田・籾倉・矢野・梁
【会場】19名（役員含む）
武智（役員代表）川原（役員）
石井・大久保・香月・河内・康・岸本・清原・五藤・榊山
嶋村・高木・根岸・羽方・花島・八巻・山崎・吉岡
【プログラム】11名（役員含む）
一宮（代表役員）檜垣（役員）
天野・宇留賀・小野・関口（百）・中西・服部
星川・松井（美）・渡邉
【記念品】8名（役員含む）
和田（役員代表）栗山（役員）
朝倉・小山・関口（葵）・園山・深井・山下
【余興】13名（役員含む）
三浦（代表役員）宮本（役員）檜垣（役員兼務）
大村・古賀・篠田・清水・澄川・内藤・幡・林・平塚・松木・柳川""".strip()

lines = full_text.split("\n")
print(f"全行数: {len(lines)}")
for i, l in enumerate(lines):
    print(f"  [{i:2d}] {l}")

print("\n--- AIアノテーション (has_typography=True) ---")
result = G11Controller._get_ai_annotations(full_text, has_typography=True)
anns = result.get("annotations", [])
print(f"アノテーション数: {len(anns)}")
for ann in anns:
    idx = ann.get("line")
    t = ann.get("type")
    line_text = lines[idx] if isinstance(idx, int) and idx < len(lines) else "?"
    print(f"  line {idx:2d} [{t}]: {line_text}")

print("\n--- 最終アーティクル ---")
applied = G11Controller._apply_annotations(full_text, anns)
articles = G11Controller._split_md_to_articles(applied)
merged = G11Controller._merge_small_articles(articles)
print(f"{len(merged)} articles:")
for i, a in enumerate(merged):
    body = a.get("body", "")
    print(f"  [{i}] {body.split(chr(10))[0]}")
