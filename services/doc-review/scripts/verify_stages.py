#!/usr/bin/env python3
"""
Stage検証スクリプト

DBのstage_*カラムが実際に埋まっているかを確認する
E1〜E5, F, H/I/J/Kの各ステージの状態を一括確認

使い方:
    python scripts/verify_stages.py <doc_id>
    python scripts/verify_stages.py --all --limit 10
    python scripts/verify_stages.py --file-type jpg --limit 5
"""
import os
import sys
import json
from pathlib import Path

# プロジェクトルートをパスに追加
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / '.env')

from shared.common.database.client import DatabaseClient


def get_stage_lengths(doc: dict) -> dict:
    """各stageの長さを取得"""
    stages = {
        'e1': 'stage_e1_text',
        'e2': 'stage_e2_text',
        'e3': 'stage_e3_text',
        'e4': 'stage_e4_text',
        'e5': 'stage_e5_text',
        'f_text_ocr': 'stage_f_text_ocr',
        'f_layout_ocr': 'stage_f_layout_ocr',
        'f_visual_elements': 'stage_f_visual_elements',
        'h_normalized': 'stage_h_normalized',
        'i_structured': 'stage_i_structured',
        'j_chunks_json': 'stage_j_chunks_json',
        'summary': 'summary',
    }

    result = {}
    for key, column in stages.items():
        value = doc.get(column)
        if value is None:
            result[key] = 0
        elif isinstance(value, str):
            result[key] = len(value)
        elif isinstance(value, (dict, list)):
            result[key] = len(json.dumps(value))
        else:
            result[key] = len(str(value)) if value else 0

    return result


def check_e_stages_identical(doc: dict) -> dict:
    """E1〜E5が全部同じ（破壊の典型パターン）かを確認"""
    e1 = doc.get('stage_e1_text') or ''
    e2 = doc.get('stage_e2_text') or ''
    e3 = doc.get('stage_e3_text') or ''
    e4 = doc.get('stage_e4_text') or ''
    e5 = doc.get('stage_e5_text') or ''

    return {
        'e1_eq_e4': e1 == e4 and e1 != '',
        'e2_eq_e4': e2 == e4 and e2 != '',
        'e3_eq_e4': e3 == e4 and e3 != '',
        'e5_eq_e4': e5 == e4 and e5 != '',
        'all_identical': e1 == e2 == e3 == e4 == e5 and e1 != '',
    }


def analyze_document(doc: dict) -> None:
    """ドキュメントのステージ状態を分析・表示"""
    print("=" * 80)
    print(f"ID: {doc.get('id')}")
    print(f"File: {doc.get('file_name')}")
    print(f"Type: {doc.get('file_type')}")
    print(f"Workspace: {doc.get('workspace')}")
    print("-" * 40)

    # Stage lengths
    lengths = get_stage_lengths(doc)
    print("\n[Stage Lengths]")
    print(f"  E1 (text extract):     {lengths['e1']:>8} chars")
    print(f"  E2 (text extract):     {lengths['e2']:>8} chars")
    print(f"  E3 (text extract):     {lengths['e3']:>8} chars")
    print(f"  E4 (text extract):     {lengths['e4']:>8} chars")
    print(f"  E5 (text extract):     {lengths['e5']:>8} chars")
    print(f"  F  (text OCR):         {lengths['f_text_ocr']:>8} chars")
    print(f"  F  (layout OCR):       {lengths['f_layout_ocr']:>8} chars")
    print(f"  F  (visual elements):  {lengths['f_visual_elements']:>8} chars")
    print(f"  H  (normalized):       {lengths['h_normalized']:>8} chars")
    print(f"  I  (structured):       {lengths['i_structured']:>8} chars")
    print(f"  J  (chunks JSON):      {lengths['j_chunks_json']:>8} chars")
    print(f"  Summary:               {lengths['summary']:>8} chars")

    # E stages identical check
    identical = check_e_stages_identical(doc)
    print("\n[E Stages Identical Check]")
    print(f"  E1 == E4: {identical['e1_eq_e4']}")
    print(f"  E2 == E4: {identical['e2_eq_e4']}")
    print(f"  E3 == E4: {identical['e3_eq_e4']}")
    print(f"  E5 == E4: {identical['e5_eq_e4']}")
    print(f"  All identical: {identical['all_identical']}")

    # Diagnosis
    print("\n[Diagnosis]")
    file_type = (doc.get('file_type') or '').lower()

    if file_type in ['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp']:
        # 画像ファイルの場合
        if lengths['f_text_ocr'] == 0 and lengths['f_visual_elements'] == 0:
            print("  WARNING: Image file but F stage (OCR/Vision) is empty!")
            print("           -> Vision/OCR pipeline may not have run.")
        elif lengths['h_normalized'] == 0 or lengths['i_structured'] == 0:
            print("  WARNING: F stage has data but H/I stages are empty.")
            print("           -> Normalization/Structuring pipeline may have failed.")
        else:
            print("  OK: Image pipeline appears complete.")
    else:
        # PDF/テキストファイルの場合
        if lengths['e4'] == 0:
            print("  WARNING: E4 (text extract) is empty!")
            print("           -> Text extraction pipeline may not have run.")
        elif lengths['h_normalized'] == 0 or lengths['i_structured'] == 0:
            print("  WARNING: E stages have data but H/I stages are empty.")
            print("           -> Normalization/Structuring pipeline may have failed.")
        else:
            print("  OK: Text pipeline appears complete.")

    if identical['all_identical'] and lengths['e1'] > 0:
        print("  WARNING: All E stages are identical!")
        print("           -> E1-E5 may have been incorrectly set to same value.")


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Verify stage_* columns in DB')
    parser.add_argument('doc_id', nargs='?', help='Document ID (UUID)')
    parser.add_argument('--all', action='store_true', help='Check all documents')
    parser.add_argument('--file-type', help='Filter by file type (e.g., jpg, pdf)')
    parser.add_argument('--limit', type=int, default=10, help='Limit number of documents')
    parser.add_argument('--workspace', help='Filter by workspace')

    args = parser.parse_args()

    db_client = DatabaseClient(use_service_role=True)

    if args.doc_id:
        # 単一ドキュメントを検証
        doc = db_client.get_document_by_id(args.doc_id)
        if not doc:
            print(f"Document not found: {args.doc_id}")
            sys.exit(1)
        analyze_document(doc)
    else:
        # 複数ドキュメントを検証
        query = db_client.client.table('Rawdata_FILE_AND_MAIL').select('*')

        if args.file_type:
            query = query.eq('file_type', args.file_type)
        if args.workspace:
            query = query.eq('workspace', args.workspace)

        query = query.limit(args.limit)
        result = query.execute()

        if not result.data:
            print("No documents found.")
            sys.exit(0)

        print(f"Found {len(result.data)} documents\n")

        for doc in result.data:
            analyze_document(doc)
            print()


if __name__ == '__main__':
    main()
