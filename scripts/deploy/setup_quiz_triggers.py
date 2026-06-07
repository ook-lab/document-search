import os
import json
import subprocess
import tempfile
from pathlib import Path

# Load env variables from .env
env_vars = {}
dot_env = Path(__file__).resolve().parents[2] / '.env'
if dot_env.exists():
    with open(dot_env, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, val = line.split('=', 1)
                env_vars[key.strip()] = val.strip().strip("'").strip('"')

PROJECT = "consummate-yew-479020-u2"
REGION = "asia-northeast1"

def _gcloud():
    import shutil
    exe = shutil.which("gcloud")
    if exe:
        return exe
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if local_app_data:
        win = Path(local_app_data) / "Google/Cloud SDK/google-cloud-sdk/bin/gcloud.cmd"
        if win.is_file():
            return str(win)
    app_data_path = Path("C:/Users/ookub/AppData/Local/Google/Cloud SDK/google-cloud-sdk/bin/gcloud.cmd")
    if app_data_path.is_file():
        return str(app_data_path)
    return "gcloud"

def run_gcloud(args):
    # Runs gcloud cmd
    cmd = [_gcloud(), '--project', PROJECT] + args
    print(f"Running: {' '.join(cmd)}")
    res = subprocess.run(cmd, capture_output=True, text=True, shell=False)
    if res.returncode != 0:
        print(f"Error executing gcloud: {res.stderr}")
    return res

def get_trigger(name):
    res = run_gcloud(['builds', 'triggers', 'describe', name, f'--region={REGION}', '--format=json'])
    if res.returncode == 0 and res.stdout.strip():
        return json.loads(res.stdout)
    return None

def update_trigger(name, mappings):
    trigger = get_trigger(name)
    if not trigger:
        print(f"Trigger {name} not found!")
        return False
    
    # Prepare substitutions
    subs = {}
    for sub_key, env_key in mappings.items():
        val = env_vars.get(env_key, '')
        subs[sub_key] = val
        
    trigger['substitutions'] = subs
    
    # Clean read-only fields
    for k in ['id', 'createTime', 'resourceName']:
        trigger.pop(k, None)
        
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as f:
        json.dump(trigger, f, indent=2)
        temp_path = f.name
        
    try:
        res = run_gcloud(['beta', 'builds', 'triggers', 'import', f'--region={REGION}', f'--source={temp_path}'])
        if res.returncode == 0:
            print(f"Successfully updated substitutions for trigger {name}")
            return True
        else:
            print(f"Failed to update trigger {name}")
            return False
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

# Mappings for quiz-maker
quiz_maker_mappings = {
    '_SUPABASE_URL': 'SUPABASE_URL',
    '_SUPABASE_KEY': 'SUPABASE_KEY',
    '_SUPABASE_SERVICE_ROLE_KEY': 'SUPABASE_SERVICE_ROLE_KEY',
    '_GEMINI_AI_API_KEY': 'GEMINI_AI_API_KEY',
    '_IKUYA_SCHOOL_FOLDER_ID': 'IKUYA_SCHOOL_FOLDER_ID',
    '_IKUYA_JUKU_FOLDER_ID': 'IKUYA_JUKU_FOLDER_ID',
    '_IKUYA_EXAM_FOLDER_ID': 'IKUYA_EXAM_FOLDER_ID',
    '_EMA_SCHOOL_FOLDER_ID': 'EMA_SCHOOL_FOLDER_ID',
    '_HOME_LIVING_FOLDER_ID': 'HOME_LIVING_FOLDER_ID',
}

# Mappings for quiz-maker-ema
quiz_maker_ema_mappings = {
    '_SUPABASE_URL': 'SUPABASE_URL',
    '_SUPABASE_KEY': 'SUPABASE_KEY',
    '_SUPABASE_SERVICE_ROLE_KEY': 'SUPABASE_SERVICE_ROLE_KEY',
    '_EMA_SCHOOL_FOLDER_ID': 'EMA_SCHOOL_FOLDER_ID',
    '_HOME_LIVING_FOLDER_ID': 'HOME_LIVING_FOLDER_ID',
    '_HOME_COOKING_FOLDER_ID': 'HOME_COOKING_FOLDER_ID',
}

update_trigger('quiz-maker', quiz_maker_mappings)
update_trigger('quiz-maker-ema', quiz_maker_ema_mappings)
