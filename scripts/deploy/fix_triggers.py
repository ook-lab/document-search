import subprocess
import json
import sys
from pathlib import Path

_deploy = Path(__file__).resolve().parent
if str(_deploy) not in sys.path:
    sys.path.insert(0, str(_deploy))
from trigger_included_paths import included_glob_for_trigger_name


def fix_triggers():
    # asia-northeast1 リージョンの全トリガーを取得
    print("Fetching Cloud Build triggers...")
    res = subprocess.run(['gcloud', 'beta', 'builds', 'triggers', 'list', '--region=asia-northeast1', '--format=json'], capture_output=True, text=True, shell=True)
    if res.returncode != 0:
        print("Error fetching triggers:", res.stderr)
        return

    stdout = res.stdout.strip()
    if not stdout:
        print("No triggers found in asia-northeast1.")
        return
        
    try:
        triggers = json.loads(stdout)
    except Exception as e:
        print(f"Failed to parse triggers JSON: {e}")
        return
    
    for t in triggers:
        name = t.get('name')
        if not name: continue

        target_dir = included_glob_for_trigger_name(name)

        if target_dir:
            print(f"Updating trigger [{name}] -> monitoring only: {target_dir}")
            # GitHubトリガー専用のコマンド形式に修正
            subprocess.run(f'gcloud builds triggers update github {name} --region=asia-northeast1 --included-files="{target_dir}"', shell=True)
        else:
            print(f"Skipping trigger [{name}] (no mapping found or already filtered)")

if __name__ == "__main__":
    fix_triggers()
