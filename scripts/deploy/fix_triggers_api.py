import subprocess
import urllib.request
import json
import sys
from pathlib import Path

_deploy = Path(__file__).resolve().parent
if str(_deploy) not in sys.path:
    sys.path.insert(0, str(_deploy))
from trigger_included_paths import included_glob_for_trigger_name


def get_access_token():
    res = subprocess.run(['gcloud', 'auth', 'print-access-token'], capture_output=True, text=True, shell=True)
    if res.returncode != 0:
        raise Exception(f"Failed to get access token: {res.stderr}")
    return res.stdout.strip()

def run():
    token = get_access_token()
    project_id = "consummate-yew-479020-u2"
    region = "asia-northeast1"
    
    # 1. List triggers
    url = f"https://cloudbuild.googleapis.com/v1/projects/{project_id}/locations/{region}/triggers"
    req = urllib.request.Request(url)
    req.add_header('Authorization', f'Bearer {token}')
    
    try:
        with urllib.request.urlopen(req) as f:
            resp = json.loads(f.read().decode())
    except Exception as e:
        print(f"Error listing triggers: {e}")
        return

    triggers = resp.get('triggers', [])
    print(f"Found {len(triggers)} triggers.")

    # includedFiles のみ PATCH。同一パスに複数トリガーがある場合の無効化は fix_triggers_v3.py を使う。
    for t in triggers:
        name = t.get('name', '')
        trigger_id = t.get('id')

        glob = included_glob_for_trigger_name(name)
        target_dir = [glob] if glob else None

        if target_dir:
            print(f"Patching trigger [{name}] ({trigger_id}) -> {target_dir}")
            patch_url = f"{url}/{trigger_id}?updateMask=included_files"
            # PATCH request to update includedFiles
            patch_data = json.dumps({"includedFiles": target_dir}).encode()
            preq = urllib.request.Request(patch_url, data=patch_data, method='PATCH')
            preq.add_header('Authorization', f'Bearer {token}')
            preq.add_header('Content-Type', 'application/json')
            try:
                with urllib.request.urlopen(preq) as pf:
                    print(f"  Result: {pf.status}")
            except Exception as pe:
                print(f"  Error patching {name}: {pe}")

if __name__ == "__main__":
    run()
