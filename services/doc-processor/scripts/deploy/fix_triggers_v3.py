import subprocess
import json
import yaml
import os

def run():
    project = 'consummate-yew-479020-u2'
    region = 'asia-northeast1'
    
    print("Fetching trigger list...")
    res = subprocess.run(['gcloud', 'builds', 'triggers', 'list', f'--region={region}', '--format=json'], capture_output=True, text=True, shell=True)
    if res.returncode != 0:
        print(f"Error listing triggers: {res.stderr}")
        return
        
    triggers = json.loads(res.stdout)
    
    service_map = {
        'doc-processor': 'services/doc-processor/**',
        'html-to-a4': 'services/html-to-a4/**',
        'doda-scraper': 'services/doda-scraper/**',
        'ocr-editor': 'services/pdf-toolbox/**',
        'pdf-splitter': 'services/pdf-toolbox/**',
        'pdf-toolbox': 'services/pdf-toolbox/**',
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
        name = t.get('name')
        if not name: continue
        
        target_path = None
        for svc, path in service_map.items():
            if svc in name.lower():
                target_path = path
                break
        
        if target_path:
            print(f"Updating trigger [{name}] via Export/Import...")
            tmp_file = 'tmp_trigger.yaml'
            
            # 1. Export (beta)
            subprocess.run(['gcloud', 'beta', 'builds', 'triggers', 'export', name, f'--region={region}', f'--destination={tmp_file}'], shell=True)
            
            if not os.path.exists(tmp_file):
                print(f"  Failed to export {name}")
                continue
                
            # 2. Modify YAML
            with open(tmp_file, 'r', encoding='utf-8') as f:
                cfg = yaml.safe_load(f)
            
            cfg['includedFiles'] = [target_path, 'shared/**']
            
            with open(tmp_file, 'w', encoding='utf-8') as f:
                yaml.dump(cfg, f)
            
            # 3. Import (beta)
            subprocess.run(['gcloud', 'beta', 'builds', 'triggers', 'import', f'--region={region}', f'--source={tmp_file}'], shell=True)
            print(f"  Successfully updated [{name}]")
            
            if os.path.exists(tmp_file):
                os.remove(tmp_file)

if __name__ == "__main__":
    run()
