"""
全ステータスのドキュメントのステージE～Kのデータを削除し、processing_statusをpendingに戻す
"""
from shared.common.database.client import DatabaseClient
import sys

def reset_all_stages_e_to_k(workspace=None, all_workspaces=False, skip_confirm=False):
    """
    全ステータスのドキュメントのステージE～Kのフィールドをクリアし、processing_statusをpendingに戻す

    Args:
        workspace: 対象のワークスペース（Noneの場合、all_workspaces=Trueが必要）
        all_workspaces: Trueの場合、全ワークスペースを対象
        skip_confirm: Trueの場合、確認プロンプトをスキップ
    """
    db = DatabaseClient(use_service_role=True)  # RLSバイパスのためService Role使用

    # クリアするフィールドのリスト
    fields_to_clear = {
        # ステージE
        'stage_e1_text': None,
        'stage_e2_text': None,
        'stage_e3_text': None,
        'stage_e4_text': None,
        'stage_e5_text': None,
        # ステージF
        'stage_f_text_ocr': None,
        'stage_f_layout_ocr': None,
        'stage_f_visual_elements': None,
        # ステージH
        'stage_h_normalized': None,
        # ステージI
        'stage_i_structured': None,
        # ステージJ
        'stage_j_chunks_json': None,
        # ステータスをpendingに戻す
        'processing_status': 'pending',
        'processing_stage': None,
    }

    # 対象ドキュメントを取得（全ステータス）
    if all_workspaces:
        # 全ワークスペースを対象
        query = db.client.table('Rawdata_FILE_AND_MAIL')\
            .select('id, file_name, title, processing_status, workspace, stage_e1_text, stage_f_text_ocr, stage_h_normalized, stage_i_structured, stage_j_chunks_json')
    elif workspace:
        # 指定ワークスペースを対象
        query = db.client.table('Rawdata_FILE_AND_MAIL')\
            .select('id, file_name, title, processing_status, workspace, stage_e1_text, stage_f_text_ocr, stage_h_normalized, stage_i_structured, stage_j_chunks_json')\
            .eq('workspace', workspace)
    else:
        print("エラー: workspace または all_workspaces=True を指定してください")
        return

    result = query.execute()

    if not result.data:
        print(f"対象のドキュメントが見つかりません")
        return

    # ステージE～Kのデータが存在するドキュメントのみフィルタリング
    docs_with_stages = []
    for doc in result.data:
        has_stage_data = (
            doc.get('stage_e1_text') is not None or
            doc.get('stage_f_text_ocr') is not None or
            doc.get('stage_h_normalized') is not None or
            doc.get('stage_i_structured') is not None or
            doc.get('stage_j_chunks_json') is not None
        )
        if has_stage_data:
            docs_with_stages.append(doc)

    if not docs_with_stages:
        print("ステージE～Kのデータが存在するドキュメントが見つかりません")
        return

    print(f"ステージE～Kのデータが存在するドキュメント: {len(docs_with_stages)}件")
    print(f"（全ドキュメント数: {len(result.data)}件）\n")

    # ステータス別の集計
    status_counts = {}
    for doc in docs_with_stages:
        status = doc.get('processing_status', '(不明)')
        status_counts[status] = status_counts.get(status, 0) + 1

    print("ステータス別:")
    for status, count in status_counts.items():
        print(f"  - {status}: {count}件")

    # ワークスペース別の集計
    workspace_counts = {}
    for doc in docs_with_stages:
        ws = doc.get('workspace', '(不明)')
        workspace_counts[ws] = workspace_counts.get(ws, 0) + 1

    print("\nワークスペース別:")
    for ws, count in workspace_counts.items():
        print(f"  - {ws}: {count}件")

    print("\nドキュメント一覧（最初の10件）:")
    for i, doc in enumerate(docs_with_stages[:10]):
        title = doc.get('title', doc.get('file_name', '(名前なし)'))
        status = doc.get('processing_status', '不明')
        ws = doc.get('workspace', '不明')

        # どのステージにデータがあるか表示
        stages = []
        if doc.get('stage_e1_text'): stages.append('E')
        if doc.get('stage_f_text_ocr'): stages.append('F')
        if doc.get('stage_h_normalized'): stages.append('H')
        if doc.get('stage_i_structured'): stages.append('I')
        if doc.get('stage_j_chunks_json'): stages.append('J')
        stages_str = ','.join(stages)

        print(f"  {i+1}. [{ws}] {title}")
        print(f"      ステータス: {status}, データあり: ステージ {stages_str}")

    if len(docs_with_stages) > 10:
        print(f"  ... 他 {len(docs_with_stages) - 10}件")

    # 確認プロンプト
    if not skip_confirm:
        confirm = input(f"\n{len(docs_with_stages)}件のドキュメントのステージE～Kをクリアし、pendingに戻しますか? (yes/no): ")
        if confirm.lower() != 'yes':
            print("キャンセルしました")
            return

    # 各ドキュメントを更新
    print("\n処理中...")
    success_count = 0
    error_count = 0
    for doc in docs_with_stages:
        try:
            db.client.table('Rawdata_FILE_AND_MAIL')\
                .update(fields_to_clear)\
                .eq('id', doc['id'])\
                .execute()
            title = doc.get('title', doc.get('file_name', '(名前なし)'))
            print(f"  [OK] {title}")
            success_count += 1
        except Exception as e:
            print(f"  [ERROR] (id: {doc['id']}): {e}")
            error_count += 1

    print(f"\n完了: {success_count}件成功, {error_count}件エラー")

if __name__ == '__main__':
    # コマンドライン引数の処理
    skip_confirm = '--yes' in sys.argv or '-y' in sys.argv

    if '--all' in sys.argv:
        reset_all_stages_e_to_k(all_workspaces=True, skip_confirm=skip_confirm)
    elif len(sys.argv) > 1 and not sys.argv[1].startswith('--'):
        workspace = sys.argv[1]
        reset_all_stages_e_to_k(workspace=workspace, skip_confirm=skip_confirm)
    else:
        print("使用方法:")
        print("  python reset_all_stages_e_to_k.py --all [--yes]         # 全ワークスペース")
        print("  python reset_all_stages_e_to_k.py <workspace> [--yes]   # 指定ワークスペース")
        print("\nオプション:")
        print("  --yes, -y    確認プロンプトをスキップ")
