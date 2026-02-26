"""Debug pipelineの結果をDBに保存

使い方:
    python update_db_from_debug.py <uuid> <doc_id>

例:
    python update_db_from_debug.py test001 01fea093-c4e2-440a-bf4c-e40ccc99d041
"""
import sys
import json
import argparse
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
sys.path.insert(0, str(Path.cwd() / 'services' / 'doc-review'))
from services.document_service import update_stage_g_result
from shared.common.database.client import DatabaseClient

parser = argparse.ArgumentParser(description='Debug pipeline結果をDBに保存')
parser.add_argument('uuid', help='デバッグUUID（debug_output/<uuid>/）')
parser.add_argument('doc_id', help='ドキュメントID')
parser.add_argument('--base-dir', default='debug_output', help='出力ベースディレクトリ')
args = parser.parse_args()

output_dir = Path(args.base_dir) / args.uuid

# Debug pipelineの結果を読み込み
stage_g_path = output_dir / f'{args.uuid}_stage_g.json'
ui_data_path = output_dir / f'{args.uuid}_ui_data.json'
final_metadata_path = output_dir / f'{args.uuid}_final_metadata.json'

if not stage_g_path.exists():
    print(f'❌ Stage G result not found: {stage_g_path}')
    sys.exit(1)

with open(stage_g_path, 'r', encoding='utf-8') as f:
    stage_g_result = json.load(f)

ui_data = {}
if ui_data_path.exists():
    with open(ui_data_path, 'r', encoding='utf-8') as f:
        ui_data = json.load(f)
else:
    ui_data = stage_g_result.get('ui_data', {})

final_metadata = {}
if final_metadata_path.exists():
    with open(final_metadata_path, 'r', encoding='utf-8') as f:
        final_metadata = json.load(f)
else:
    final_metadata = stage_g_result.get('final_metadata', {})

print(f'UUID: {args.uuid}')
print(f'Doc ID: {args.doc_id}')
print(f'ui_data keys: {list(ui_data.keys())}')
print(f'final_metadata - g11_output: {len(final_metadata.get("g11_output", []))}個')
print(f'final_metadata - g17_output: {len(final_metadata.get("g17_output", []))}個')
print(f'final_metadata - g21_output: {len(final_metadata.get("g21_output", []))}件')
print(f'final_metadata - g22_output.calendar_events: {len(final_metadata.get("g22_output", {}).get("calendar_events", []))}件')

# DBに保存
db_client = DatabaseClient(use_service_role=True)

print(f'\nSaving to database: {args.doc_id}')
success = update_stage_g_result(
    db_client=db_client,
    document_id=args.doc_id,
    ui_data=ui_data,
    final_metadata=final_metadata if final_metadata else None
)

if not success:
    print('❌ Failed to update database')
    sys.exit(1)

print('✅ Database updated successfully!')
if final_metadata:
    print('  ✅ final_metadata (g11/g17/g21/g22) saved to metadata column')
else:
    print('  ⚠️  final_metadata なし（metadata カラム未更新）')
