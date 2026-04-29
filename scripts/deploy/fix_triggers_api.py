import subprocess
import urllib.request
import json

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
        'drive-checker': 'services/drive-duplicate-checker/**',
        'doc-review': 'services/doc-review/**',
        'doc-search': 'services/doc-search/**',
        'data-ingestion': 'services/data-ingestion/**',
        'tenshoku-tool': 'services/tenshoku-tool/**'
    }

    for t in triggers:
        name = t.get('name', '')
        trigger_id = t.get('id')
        
        target_dir = None
        for svc, path in service_map.items():
            if svc in name.lower():
                target_dir = [f"{path}", "shared/**"]
                break
        
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
