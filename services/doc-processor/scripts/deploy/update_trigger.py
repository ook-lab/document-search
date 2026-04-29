import subprocess, json

def gcloud_json(args):
    r = subprocess.run(['gcloud'] + args + ['--format=json'], capture_output=True, text=True, shell=True)
    return json.loads(r.stdout)

# ai-cost-tracker から SUPABASE 系 substitutions を取得
src = gcloud_json(['builds', 'triggers', 'describe', 'ai-cost-tracker', '--region=asia-northeast1'])
subs = {k: v for k, v in src.get('substitutions', {}).items() if 'SUPABASE' in k}
print('Supabase keys found:', list(subs.keys()))

# kakeibo-view-deploy トリガーを取得して更新
view = gcloud_json(['builds', 'triggers', 'describe', 'kakeibo-view-deploy', '--region=asia-northeast1'])
view['substitutions'] = subs
for k in ['id', 'createTime', 'resourceName']:
    view.pop(k, None)

with open('trigger_kv_update.json', 'w') as f:
    json.dump(view, f, indent=2)
print('Written trigger_kv_update.json')
