import sys, json
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, '.')
from dms.common.path_setup import setup_paths
setup_paths()
from dms.common.database.client import DatabaseClient

db = DatabaseClient(use_service_role=True)
res = db.client.table('pipeline_meta').select('id,processing_status,completed_at,g21_articles').eq('processing_status','completed').order('completed_at', desc=True).limit(3).execute()
for r in res.data:
    raw = r.get('g21_articles')
    if isinstance(raw, str):
        arts = json.loads(raw)
    else:
        arts = raw or []
    print(f"ID: {r['id']} completed: {r['completed_at']}")
    print(f"  g21_articles count: {len(arts)}")
    for i, a in enumerate(arts[:5]):
        if isinstance(a, dict):
            body = (a.get('body') or '')[:100]
        else:
            body = str(a)[:100]
        print(f"  [{i}] {repr(body)}")
    print()
