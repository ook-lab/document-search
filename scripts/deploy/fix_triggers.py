import subprocess
import json

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
    
    # サービス名とフォルダの対応マップ
    service_map = {
        'doc-processor': 'services/doc-processor/**',
        'html-to-a4': 'services/html-to-a4/**',
        'doda-scraper': 'services/doda-scraper/**',
        'ocr-editor': 'services/pdf-toolbox/**',
        'pdf-splitter': 'services/pdf-toolbox/**',
        'resume-maker': 'services/resume-maker/**',
        'kakeibo': 'services/kakeibo/**',
        'calendar-register': 'services/calendar-register/**',
        'daily-report': 'services/daily-report/**',
        'ai-cost-tracker': 'services/ai-cost-tracker/**',
        'my-calendar-app': 'my-calendar-app/**',
        'portal-app': 'portal-app/**',
        'doc-review': 'services/doc-review/**',
        'doc-search': 'services/doc-search/**',
        'data-ingestion': 'services/data-ingestion/**',
        'tenshoku-tool': 'services/tenshoku-tool/**',
        'drive-checker': 'services/drive-duplicate-checker/**'
    }

    for t in triggers:
        name = t.get('name')
        if not name: continue
        
        target_dir = None
        for svc, path in service_map.items():
            if svc in name.lower():
                target_dir = path
                break
        
        if target_dir:
            print(f"Updating trigger [{name}] -> monitoring only: {target_dir}")
            # GitHubトリガー専用のコマンド形式に修正
            subprocess.run(f'gcloud builds triggers update github {name} --region=asia-northeast1 --included-files="{target_dir}"', shell=True)
        else:
            print(f"Skipping trigger [{name}] (no mapping found or already filtered)")

if __name__ == "__main__":
    fix_triggers()
