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

text = """＊敬称省略　　五十音順
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

# 実際のAIアノテーション（直前のテストで取得）
real_annotations = [
    {"line": 0,  "type": "blockquote"},
    {"line": 1,  "type": "heading_1"},
    {"line": 2,  "type": "paragraph_break"},
    {"line": 3,  "type": "heading_1"},
    {"line": 4,  "type": "bullet_item"},
    {"line": 5,  "type": "bullet_item"},
    {"line": 6,  "type": "bullet_item"},
    {"line": 7,  "type": "heading_1"},
    {"line": 8,  "type": "paragraph_break"},
    {"line": 9,  "type": "bullet_item"},
    {"line": 10, "type": "bullet_item"},
    {"line": 11, "type": "heading_1"},
    {"line": 12, "type": "paragraph_break"},
    {"line": 13, "type": "bullet_item"},
    {"line": 14, "type": "bullet_item"},
    {"line": 15, "type": "heading_1"},
    {"line": 16, "type": "paragraph_break"},
    {"line": 17, "type": "bullet_item"},
    {"line": 18, "type": "heading_1"},
    {"line": 19, "type": "paragraph_break"},
    {"line": 20, "type": "bullet_item"},
]

applied = G11Controller._apply_annotations(text, real_annotations)
print("=== applied (lines) ===")
for i, l in enumerate(applied.split("\n")):
    print(f"  [{i:2d}] {repr(l)}")

articles = G11Controller._split_md_to_articles(applied)
merged = G11Controller._merge_small_articles(articles)

print(f"\n=== final: {len(merged)} articles ===")
for i, a in enumerate(merged):
    body = a.get("body", "")
    print(f"  [{i}] {body.split(chr(10))[0]}")
