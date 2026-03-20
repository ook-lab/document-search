import subprocess, json

def gcloud_json(args):
    r = subprocess.run(['gcloud'] + args + ['--format=json'], capture_output=True, text=True, shell=True)
    return json.loads(r.stdout)

# deploy-all-on-shared-change から SUPABASE 系 + DEFAULT_OWNER_ID を取得
src = gcloud_json(['builds', 'triggers', 'describe', 'deploy-all-on-shared-change', '--region=asia-northeast1'])
want_keys = {'_SUPABASE_URL', '_SUPABASE_SERVICE_ROLE_KEY', '_SUPABASE_KEY', '_DEFAULT_OWNER_ID'}
subs = {k: v for k, v in src.get('substitutions', {}).items() if k in want_keys}
print('Keys found:', list(subs.keys()))

missing = want_keys - set(subs.keys())
if missing:
    print('WARNING: missing keys:', missing)

# kakeibo-ui トリガーを取得して更新
trigger = gcloud_json(['builds', 'triggers', 'describe', 'kakeibo-ui', '--region=asia-northeast1'])
trigger['substitutions'] = subs
for k in ['id', 'createTime', 'resourceName']:
    trigger.pop(k, None)

with open('trigger_kakeibo_ui_update.json', 'w') as f:
    json.dump(trigger, f, indent=2)
print('Written trigger_kakeibo_ui_update.json')
print('Next: gcloud builds triggers import --region=asia-northeast1 --source=trigger_kakeibo_ui_update.json')
