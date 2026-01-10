from shared.common.database.client import DatabaseClient

db = DatabaseClient()

doc_ids = [
    '299d6d5d-d105-4503-982f-8db48e77eded',
    '485f2b52-1914-4adf-b04a-2c9c94b0760c',
    '87ba1314-9ef0-49bd-a6b4-0a558d32bf0d'
]

for doc_id in doc_ids:
    result = db.client.table('Rawdata_FILE_AND_MAIL').update({
        'processing_status': 'pending',
        'stage_e1_text': None,
        'stage_e2_text': None,
        'stage_e3_text': None,
        'stage_e4_text': None,
        'stage_e5_text': None,
        'attachment_text': None,
        'stage_f_text_ocr': None,
        'stage_f_layout_ocr': None,
        'stage_f_visual_elements': None,
        'stage_h_normalized': None,
        'stage_i_structured': None,
        'summary': None,
        'tags': None,
        'title': None,
        'stage_j_chunks_json': None,
        'chunk_count': 0
    }).eq('id', doc_id).execute()
    print(f'Reset to pending: {doc_id}')

print('\n3件をpendingに戻しました')
