"""
ステージE～Kのデータを削除し、processing_statusをpendingに戻す
"""
from shared.common.database.client import DatabaseClient
import sys

def reset_stages_e_to_k(workspace=None, doc_id=None, all_workspaces=False, skip_confirm=False):
    """
    ステージE～Kのフィールドをクリアし、processing_statusをpendingに戻す

    Args:
        workspace: 対象のワークスペース（Noneの場合、all_workspaces=Trueが必要）
        doc_id: 特定のドキュメントIDを指定（Noneの場合は全ドキュメント）
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

    # 対象ドキュメントを取得
    if doc_id:
        # 特定のドキュメントを対象
        query = db.client.table('Rawdata_FILE_AND_MAIL')\
            .select('id, file_name, title, processing_status, workspace')\
            .eq('id', doc_id)
    elif all_workspaces:
        # 全ワークスペースを対象（completedステータスのみ）
        query = db.client.table('Rawdata_FILE_AND_MAIL')\
            .select('id, file_name, title, processing_status, workspace')\
            .eq('processing_status', 'completed')
    elif workspace:
        # 指定ワークスペースを対象（completedステータスのみ）
        query = db.client.table('Rawdata_FILE_AND_MAIL')\
            .select('id, file_name, title, processing_status, workspace')\
            .eq('workspace', workspace)\
            .eq('processing_status', 'completed')
    else:
        print("エラー: workspace または all_workspaces=True を指定してください")
        return

    result = query.execute()

    if not result.data:
        print(f"対象のドキュメントが見つかりません")
        if doc_id:
            print(f"  doc_id: {doc_id}")
        elif all_workspaces:
            print(f"  全ワークスペース, status: completed")
        else:
            print(f"  workspace: {workspace}, status: completed")
        return

    print(f"対象ドキュメント: {len(result.data)}件")

    # ワークスペース別の集計
    workspace_counts = {}
    for doc in result.data:
        ws = doc.get('workspace', '(不明)')
        workspace_counts[ws] = workspace_counts.get(ws, 0) + 1

    print("\nワークスペース別:")
    for ws, count in workspace_counts.items():
        print(f"  - {ws}: {count}件")

    print("\nドキュメント一覧（最初の10件）:")
    for i, doc in enumerate(result.data[:10]):
        title = doc.get('title', doc.get('file_name', '(名前なし)'))
        status = doc.get('processing_status', '不明')
        ws = doc.get('workspace', '不明')
        print(f"  {i+1}. [{ws}] {title} (現在: {status})")

    if len(result.data) > 10:
        print(f"  ... 他 {len(result.data) - 10}件")

    # 確認プロンプト
    if not doc_id and len(result.data) > 0 and not skip_confirm:
        confirm = input(f"\n{len(result.data)}件のドキュメントのステージE～Kをクリアし、pendingに戻しますか? (yes/no): ")
        if confirm.lower() != 'yes':
            print("キャンセルしました")
            return

    # 各ドキュメントを更新
    print("\n処理中...")
    success_count = 0
    error_count = 0
    for doc in result.data:
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
    # 使用例:
    #   python reset_stages_e_to_k.py --all --yes               # 全ワークスペースの全completedドキュメント（確認なし）
    #   python reset_stages_e_to_k.py my_workspace              # 指定ワークスペースの全completedドキュメント
    #   python reset_stages_e_to_k.py --id <doc_id>             # 特定のドキュメントのみ

    skip_confirm = '--yes' in sys.argv or '-y' in sys.argv

    if '--id' in sys.argv:
        idx = sys.argv.index('--id')
        if idx + 1 < len(sys.argv):
            doc_id = sys.argv[idx + 1]
            reset_stages_e_to_k(doc_id=doc_id, skip_confirm=skip_confirm)
        else:
            print("エラー: --id の後にドキュメントIDを指定してください")
    elif '--all' in sys.argv:
        reset_stages_e_to_k(all_workspaces=True, skip_confirm=skip_confirm)
    elif len(sys.argv) > 1 and not sys.argv[1].startswith('--'):
        workspace = sys.argv[1]
        reset_stages_e_to_k(workspace=workspace, skip_confirm=skip_confirm)
    else:
        print("使用方法:")
        print("  python reset_stages_e_to_k.py --all [--yes]         # 全ワークスペース")
        print("  python reset_stages_e_to_k.py <workspace> [--yes]   # 指定ワークスペース")
        print("  python reset_stages_e_to_k.py --id <doc_id>         # 特定ドキュメント")
        print("\nオプション:")
        print("  --yes, -y    確認プロンプトをスキップ")
