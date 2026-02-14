"""Debug pipelineの結果をDBに保存"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
sys.path.insert(0, str(Path.cwd() / 'services' / 'doc-review'))
from services.document_service import update_stage_g_result
from shared.common.database.client import DatabaseClient

# Debug pipelineの結果を読み込み
stage_g_path = Path('debug_output/test_table_fix3/test_table_fix3_stage_g.json')
ui_data_path = Path('debug_output/test_table_fix3/test_table_fix3_ui_data.json')

with open(stage_g_path, 'r', encoding='utf-8') as f:
    stage_g_result = json.load(f)

with open(ui_data_path, 'r', encoding='utf-8') as f:
    ui_data = json.load(f)

print(f'Loaded Stage G result: ui_data.tables={len(ui_data.get("tables", []))}')
print(f'Loaded final_metadata: tables={len(stage_g_result.get("final_metadata", {}).get("tables", []))}')

# DBに保存
doc_id = '01fea093-c4e2-440a-bf4c-e40ccc99d041'
db_client = DatabaseClient(use_service_role=True)

print(f'\nSaving to database: {doc_id}')
success = update_stage_g_result(
    db_client=db_client,
    document_id=doc_id,
    ui_data=ui_data
)

if not success:
    print('❌ Failed to update database')
    sys.exit(1)

print('✅ Database updated successfully!')
