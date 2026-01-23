#!/usr/bin/env python3
"""
Ops CLI - ドキュメント管理システム運用コマンド（SSOT）

【設計原則】
- 運用操作の唯一の入口（Worker以外）
- ops_requests テーブルを通じた要求管理
- dry-run → apply の二段階実行で事故防止
- DB更新のみ（処理パイプラインは実行しない）

【SSOT構造】
- ops_requests テーブルが運用要求の真実（SSOT）
- worker_state.stop_requested は派生キャッシュ（このファイルのみが書き込み）
- apply は ops.py のみが行う（Worker は処理のみ、apply しない）
- Web API は ops_requests に enqueue するだけ（apply しない）

【使い方】
    # 停止要求を登録
    python ops.py stop
    python ops.py stop --workspace ema_classroom

    # リース解放要求を登録
    python ops.py release-lease --workspace ema_classroom
    python ops.py release-lease --doc-id <uuid>

    # ステータスリセット（processing→pending）
    python ops.py reset-status --workspace ema_classroom
    python ops.py reset-status --workspace ema_classroom --apply

    # ステージクリア（E-K削除して再処理）
    python ops.py reset-stages --workspace ema_classroom
    python ops.py reset-stages --workspace ema_classroom --apply

    # 統計情報
    python ops.py stats
    python ops.py stats --workspace ema_classroom

    # ops_requests の確認・適用
    python ops.py requests              # 未処理の要求一覧
    python ops.py requests --apply      # 未処理の要求を適用

    # ヘルプ
    python ops.py --help
"""
import sys
import argparse
import json
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Optional, List, Dict, Any

# プロジェクトルートへのパスを追加
_root_dir = Path(__file__).resolve().parent.parent
if str(_root_dir) not in sys.path:
    sys.path.insert(0, str(_root_dir))

from loguru import logger
from shared.common.database.client import DatabaseClient


# ============================================================
# データクラス
# ============================================================

@dataclass
class DryRunResult:
    """dry-run の結果"""
    affected_count: int
    affected_items: List[Dict[str, Any]]
    message: str


# ============================================================
# 共通ユーティリティ
# ============================================================

def get_db_client(use_service_role: bool = True) -> DatabaseClient:
    """DBクライアントを取得（デフォルト: Service Role - RLSバイパス）"""
    return DatabaseClient(use_service_role=use_service_role)


def print_dry_run_warning():
    """dry-run モードの警告を表示"""
    print("\n" + "="*70)
    print("【DRY-RUN MODE】実際の変更は行われません")
    print("実行するには --apply フラグを追加してください")
    print("="*70)


def print_result_summary(affected_count: int, action: str):
    """結果サマリーを表示"""
    print("\n" + "-"*70)
    print(f"[完了] {affected_count}件を{action}しました")
    print("-"*70 + "\n")


# ============================================================
# Stats コマンド
# ============================================================

def cmd_stats(args):
    """統計情報を表示"""
    db = get_db_client()
    workspace = args.workspace

    try:
        query = db.client.table('Rawdata_FILE_AND_MAIL').select('processing_status, workspace')

        if workspace != 'all':
            query = query.eq('workspace', workspace)

        response = query.limit(100000).execute()

        stats = {
            'pending': 0,
            'processing': 0,
            'completed': 0,
            'failed': 0,
            'null': 0
        }

        for doc in response.data:
            status = doc.get('processing_status')
            if status is None:
                stats['null'] += 1
            else:
                stats[status] = stats.get(status, 0) + 1

        stats['total'] = len(response.data)

        processed = stats['completed'] + stats['failed']
        if processed > 0:
            stats['success_rate'] = round(stats['completed'] / processed * 100, 1)
        else:
            stats['success_rate'] = 0.0

        print("\n" + "="*60)
        if workspace == 'all':
            print("全体統計")
        else:
            print(f"統計 (workspace: {workspace})")
        print("="*60)
        print(f"待機中 (pending):      {stats['pending']:>5}件")
        print(f"処理中 (processing):   {stats['processing']:>5}件")
        print(f"完了   (completed):    {stats['completed']:>5}件")
        print(f"失敗   (failed):       {stats['failed']:>5}件")
        print(f"未処理 (null):         {stats['null']:>5}件")
        print("-" * 60)
        print(f"合計:                  {stats['total']:>5}件")
        print(f"成功率:                {stats['success_rate']:>5.1f}%")
        print("="*60 + "\n")

    except Exception as e:
        logger.error(f"統計取得エラー: {e}")
        return 1

    return 0


# ============================================================
# Stop コマンド（停止要求）
# ============================================================

def cmd_stop(args):
    """停止要求を ops_requests に登録（enqueue のみ）

    【設計原則】
    - このコマンドは ops_requests に enqueue するだけ
    - 実際の適用（worker_state.stop_requested への反映）は requests --apply で行う
    - ただし --immediate オプションで即時適用も可能（緊急時用）
    """
    db = get_db_client()

    scope_type = 'workspace' if args.workspace and args.workspace != 'all' else 'global'
    scope_id = args.workspace if scope_type == 'workspace' else None

    try:
        # ops_requests に登録
        request_data = {
            'request_type': 'STOP',
            'scope_type': scope_type,
            'scope_id': scope_id,
            'requested_by': 'ops.py',
            'payload': json.dumps({'reason': args.reason or '手動停止'})
        }

        result = db.client.table('ops_requests').insert(request_data).execute()

        if result.data:
            req_id = result.data[0]['id']
            print("\n" + "="*60)
            print("[OK] 停止要求を登録しました（SSOT: ops_requests）")
            print("="*60)
            print(f"  Request ID: {req_id}")
            print(f"  Scope: {scope_type}" + (f" ({scope_id})" if scope_id else ""))
            print(f"  Reason: {args.reason or '手動停止'}")

            # --immediate オプションがある場合は即時適用
            if getattr(args, 'immediate', False):
                print("\n  [即時適用中...]")
                db.client.table('worker_state').update({
                    'stop_requested': True
                }).eq('id', 1).execute()
                db.client.table('ops_requests').update({
                    'status': 'applied',
                    'applied_at': datetime.now(timezone.utc).isoformat(),
                    'applied_by': 'ops.py (immediate)'
                }).eq('id', req_id).execute()
                print("  [OK] 派生キャッシュ（worker_state.stop_requested）に反映しました")
            else:
                print("\n  Worker は次の処理開始時にこの要求を検出して停止します")
                print("  即時反映が必要な場合: python ops.py requests --apply")

            print("="*60 + "\n")
        else:
            print("[ERROR] 停止要求の登録に失敗しました")
            return 1

    except Exception as e:
        # ops_requests テーブルがない場合はエラー（フォールバックしない）
        logger.error(f"ops_requests への登録失敗: {e}")
        print("\n[ERROR] ops_requests テーブルが存在しません")
        print("  マイグレーションを実行してください:")
        print("  database/migrations/create_ops_requests.sql")
        print("\n  緊急時の直接停止（非推奨）:")
        print("  python -c \"from shared.common.database.client import DatabaseClient; DatabaseClient().client.table('worker_state').update({'stop_requested': True}).eq('id', 1).execute()\"")
        return 1

    return 0


# ============================================================
# Release-Lease コマンド（リース解放要求）
# ============================================================

def cmd_release_lease(args):
    """リース解放要求を登録（stuck対策）

    【スコープ制限（事故防止）】
    - workspace または doc-id を必須
    - "all" は禁止（全件解放は危険すぎる）
    - dry-run で影響件数を確認してから apply
    """
    db = get_db_client()

    # スコープ必須チェック
    if not args.workspace and not args.doc_id:
        print("\n[ERROR] スコープを指定してください")
        print("  --workspace <workspace_name>  : ワークスペース単位")
        print("  --doc-id <uuid>               : ドキュメント単位")
        print("\n  全件解放は禁止されています（事故防止）")
        return 1

    # "all" は禁止
    if args.workspace and args.workspace.lower() == 'all':
        print("\n[ERROR] --workspace all は禁止されています")
        print("  全件解放は事故の原因になります。")
        print("  特定のワークスペースを指定してください。")
        return 1

    scope_type = 'document' if args.doc_id else 'workspace'
    scope_id = args.doc_id or args.workspace

    # dry-run: 影響件数を表示
    try:
        if scope_type == 'document':
            affected = db.client.table('Rawdata_FILE_AND_MAIL').select('id, title, file_name')\
                .eq('id', scope_id).eq('processing_status', 'processing').execute()
        else:
            affected = db.client.table('Rawdata_FILE_AND_MAIL').select('id, title, file_name')\
                .eq('workspace', scope_id).eq('processing_status', 'processing').limit(100).execute()

        affected_count = len(affected.data) if affected.data else 0

        print("\n" + "="*60)
        print(f"Release Lease: {scope_type} = {scope_id}")
        print("="*60)
        print(f"  影響件数（processing状態）: {affected_count}件")

        if affected_count > 0 and affected_count <= 10:
            print("\n  対象ドキュメント:")
            for doc in affected.data:
                title = doc.get('title', doc.get('file_name', '(不明)'))
                print(f"    - {title}")

        if affected_count == 0:
            print("\n  processing 状態のドキュメントはありません")
            return 0

    except Exception as e:
        logger.warning(f"影響件数の取得に失敗（継続）: {e}")
        affected_count = -1  # 不明

    # ops_requests に登録
    try:
        request_data = {
            'request_type': 'RELEASE_LEASE',
            'scope_type': scope_type,
            'scope_id': scope_id,
            'requested_by': 'ops.py',
            'payload': json.dumps({
                'force': args.force,
                'estimated_affected_count': affected_count
            })
        }

        result = db.client.table('ops_requests').insert(request_data).execute()

        if result.data:
            req_id = result.data[0]['id']
            print("\n" + "-"*60)
            print(f"[OK] リース解放要求を登録しました（SSOT: ops_requests）")
            print(f"  Request ID: {req_id}")
            print("\n  適用するには: python ops.py requests --apply")
            print("="*60 + "\n")
        else:
            print("[ERROR] 要求の登録に失敗しました")
            return 1

    except Exception as e:
        logger.error(f"ops_requests への登録失敗: {e}")
        print(f"\n[ERROR] ops_requests テーブルが存在しません: {e}")
        return 1

    return 0


# ============================================================
# Reset-Status コマンド（processing→pending）
# ============================================================

def dry_run_reset_status(db: DatabaseClient, workspace: str = None, doc_id: str = None) -> DryRunResult:
    """reset-status の dry-run"""
    if doc_id:
        result = db.client.table('Rawdata_FILE_AND_MAIL')\
            .select('id, file_name, title, processing_status')\
            .eq('id', doc_id)\
            .execute()
    elif workspace:
        result = db.client.table('Rawdata_FILE_AND_MAIL')\
            .select('id, file_name, title, processing_status')\
            .eq('workspace', workspace)\
            .eq('processing_status', 'processing')\
            .execute()
    else:
        result = db.client.table('Rawdata_FILE_AND_MAIL')\
            .select('id, file_name, title, processing_status')\
            .eq('processing_status', 'processing')\
            .limit(1000)\
            .execute()

    return DryRunResult(
        affected_count=len(result.data) if result.data else 0,
        affected_items=result.data or [],
        message="processing状態のドキュメントをpendingに戻します"
    )


def cmd_reset_status(args):
    """processing状態をpendingに戻す"""
    db = get_db_client(use_service_role=True)

    # dry-run を実行
    dry_result = dry_run_reset_status(db, args.workspace, args.doc_id)

    print("\n" + "="*60)
    print("Reset Status: processing → pending")
    print("="*60)
    print(f"対象件数: {dry_result.affected_count}件")

    if dry_result.affected_count == 0:
        print("\n対象のドキュメントがありません")
        return 0

    # 対象の表示
    print("\n対象ドキュメント:")
    for i, doc in enumerate(dry_result.affected_items[:20]):
        title = doc.get('title', doc.get('file_name', '(不明)'))
        print(f"  {i+1:>3}. {title}")
    if dry_result.affected_count > 20:
        print(f"  ... 他 {dry_result.affected_count - 20}件")

    # dry-run の場合はここで終了
    if not args.apply:
        print_dry_run_warning()
        print(f"\n実行コマンド:")
        if args.doc_id:
            print(f"  python ops.py reset-status --doc-id {args.doc_id} --apply")
        elif args.workspace:
            print(f"  python ops.py reset-status --workspace {args.workspace} --apply")
        else:
            print(f"  python ops.py reset-status --apply")
        return 0

    # 確認
    if not args.yes:
        confirm = input(f"\n{dry_result.affected_count}件をpendingに戻しますか? (yes/no): ")
        if confirm.lower() != 'yes':
            print("キャンセルしました")
            return 0

    # 実行
    print("\n処理中...")
    success_count = 0
    for doc in dry_result.affected_items:
        try:
            db.client.table('Rawdata_FILE_AND_MAIL')\
                .update({'processing_status': 'pending'})\
                .eq('id', doc['id'])\
                .execute()
            success_count += 1
        except Exception as e:
            logger.error(f"更新エラー: {doc['id']} - {e}")

    print_result_summary(success_count, "pendingに戻し")
    return 0


# ============================================================
# Reset-Stages コマンド（E-Kクリア）
# ============================================================

# クリアするフィールド
STAGE_FIELDS_TO_CLEAR = {
    'stage_e1_text': None,
    'stage_e2_text': None,
    'stage_e3_text': None,
    'stage_e4_text': None,
    'stage_e5_text': None,
    'stage_f_text_ocr': None,
    'stage_f_layout_ocr': None,
    'stage_f_visual_elements': None,
    'stage_h_normalized': None,
    'stage_i_structured': None,
    'stage_j_chunks_json': None,
    'processing_status': 'pending',
    'processing_stage': None,
}


def dry_run_reset_stages(db: DatabaseClient, workspace: str = None, doc_id: str = None, target_status: str = 'completed') -> DryRunResult:
    """reset-stages の dry-run"""
    if doc_id:
        result = db.client.table('Rawdata_FILE_AND_MAIL')\
            .select('id, file_name, title, processing_status, workspace')\
            .eq('id', doc_id)\
            .execute()
    elif workspace:
        result = db.client.table('Rawdata_FILE_AND_MAIL')\
            .select('id, file_name, title, processing_status, workspace')\
            .eq('workspace', workspace)\
            .eq('processing_status', target_status)\
            .execute()
    else:
        result = db.client.table('Rawdata_FILE_AND_MAIL')\
            .select('id, file_name, title, processing_status, workspace')\
            .eq('processing_status', target_status)\
            .limit(1000)\
            .execute()

    return DryRunResult(
        affected_count=len(result.data) if result.data else 0,
        affected_items=result.data or [],
        message=f"ステージE-Kをクリアしてpendingに戻します（対象: {target_status}）"
    )


def cmd_reset_stages(args):
    """ステージE-Kをクリアしてpendingに戻す"""
    db = get_db_client(use_service_role=True)

    target_status = args.status or 'completed'

    # dry-run を実行
    dry_result = dry_run_reset_stages(db, args.workspace, args.doc_id, target_status)

    print("\n" + "="*60)
    print(f"Reset Stages: ステージE-Kクリア → pending")
    print(f"対象ステータス: {target_status}")
    print("="*60)
    print(f"対象件数: {dry_result.affected_count}件")

    if dry_result.affected_count == 0:
        print("\n対象のドキュメントがありません")
        return 0

    # ワークスペース別集計
    workspace_counts = {}
    for doc in dry_result.affected_items:
        ws = doc.get('workspace', '(不明)')
        workspace_counts[ws] = workspace_counts.get(ws, 0) + 1

    print("\nワークスペース別:")
    for ws, count in workspace_counts.items():
        print(f"  - {ws}: {count}件")

    # 対象の表示
    print("\n対象ドキュメント:")
    for i, doc in enumerate(dry_result.affected_items[:20]):
        title = doc.get('title', doc.get('file_name', '(不明)'))
        ws = doc.get('workspace', '不明')
        print(f"  {i+1:>3}. [{ws}] {title}")
    if dry_result.affected_count > 20:
        print(f"  ... 他 {dry_result.affected_count - 20}件")

    # dry-run の場合はここで終了
    if not args.apply:
        print_dry_run_warning()
        print(f"\n実行コマンド:")
        if args.doc_id:
            print(f"  python ops.py reset-stages --doc-id {args.doc_id} --apply")
        elif args.workspace:
            print(f"  python ops.py reset-stages --workspace {args.workspace} --apply")
        else:
            print(f"  python ops.py reset-stages --status {target_status} --apply")
        return 0

    # 確認
    if not args.yes:
        confirm = input(f"\n{dry_result.affected_count}件のステージE-Kをクリアしますか? (yes/no): ")
        if confirm.lower() != 'yes':
            print("キャンセルしました")
            return 0

    # 実行
    print("\n処理中...")
    success_count = 0
    error_count = 0
    for doc in dry_result.affected_items:
        try:
            db.client.table('Rawdata_FILE_AND_MAIL')\
                .update(STAGE_FIELDS_TO_CLEAR)\
                .eq('id', doc['id'])\
                .execute()
            success_count += 1
        except Exception as e:
            logger.error(f"更新エラー: {doc['id']} - {e}")
            error_count += 1

    print(f"\n[完了] {success_count}件成功, {error_count}件エラー\n")
    return 0


# ============================================================
# Requests コマンド（ops_requests の管理）
# ============================================================

def cmd_requests(args):
    """ops_requests の一覧表示・適用"""
    db = get_db_client(use_service_role=True)

    try:
        # 未処理の要求を取得
        result = db.client.table('ops_requests')\
            .select('*')\
            .eq('status', 'queued')\
            .order('created_at', desc=False)\
            .limit(100)\
            .execute()

        requests_list = result.data or []

        print("\n" + "="*60)
        print("Ops Requests: 未処理の運用要求")
        print("="*60)
        print(f"件数: {len(requests_list)}件")

        if not requests_list:
            print("\n未処理の要求はありません")
            return 0

        print("\n要求一覧:")
        for i, req in enumerate(requests_list):
            req_type = req.get('request_type', '不明')
            scope = f"{req.get('scope_type', '')}:{req.get('scope_id', '')}" if req.get('scope_id') else req.get('scope_type', 'global')
            created = req.get('created_at', '')[:19] if req.get('created_at') else ''
            print(f"  {i+1:>3}. [{req_type}] {scope} ({created})")
            print(f"       ID: {req.get('id')}")

        if not args.apply:
            print("\n" + "-"*60)
            print("要求を適用するには: python ops.py requests --apply")
            print("-"*60 + "\n")
            return 0

        # 適用実行
        print("\n" + "-"*60)
        print("要求を適用中...")
        print("-"*60)

        for req in requests_list:
            req_id = req['id']
            req_type = req.get('request_type')

            try:
                result_msg = apply_ops_request(db, req)
                db.client.table('ops_requests').update({
                    'status': 'applied',
                    'applied_at': datetime.now(timezone.utc).isoformat(),
                    'applied_by': 'ops.py',
                    'result_message': result_msg
                }).eq('id', req_id).execute()
                print(f"  [OK] {req_type}: {result_msg}")
            except Exception as e:
                db.client.table('ops_requests').update({
                    'status': 'rejected',
                    'result_message': str(e)
                }).eq('id', req_id).execute()
                print(f"  [ERROR] {req_type}: {e}")

        print("\n[完了] 要求の適用が終了しました\n")
        return 0

    except Exception as e:
        logger.error(f"ops_requests 取得エラー: {e}")
        print(f"\n[WARNING] ops_requests テーブルが存在しないか、アクセスできません: {e}")
        return 1


def apply_ops_request(db: DatabaseClient, req: Dict[str, Any]) -> str:
    """個別の ops_request を適用"""
    req_type = req.get('request_type')
    scope_type = req.get('scope_type')
    scope_id = req.get('scope_id')

    if req_type == 'STOP':
        # worker_state.stop_requested を true に
        db.client.table('worker_state').update({
            'stop_requested': True
        }).eq('id', 1).execute()
        return "停止フラグを設定しました"

    elif req_type == 'RESUME':
        # worker_state.stop_requested を false に
        db.client.table('worker_state').update({
            'stop_requested': False
        }).eq('id', 1).execute()
        return "停止フラグを解除しました"

    elif req_type == 'RELEASE_LEASE':
        # processing 状態のドキュメントを pending に
        if scope_type == 'document' and scope_id:
            db.client.table('Rawdata_FILE_AND_MAIL').update({
                'processing_status': 'pending'
            }).eq('id', scope_id).eq('processing_status', 'processing').execute()
            return f"ドキュメント {scope_id} のリースを解放しました"
        elif scope_type == 'workspace' and scope_id:
            result = db.client.table('Rawdata_FILE_AND_MAIL').update({
                'processing_status': 'pending'
            }).eq('workspace', scope_id).eq('processing_status', 'processing').execute()
            count = len(result.data) if result.data else 0
            return f"workspace {scope_id} の {count}件のリースを解放しました"
        else:
            return "スコープが不正です"

    elif req_type == 'RESET_DOC':
        if scope_type == 'document' and scope_id:
            db.client.table('Rawdata_FILE_AND_MAIL').update({
                'processing_status': 'pending'
            }).eq('id', scope_id).execute()
            return f"ドキュメント {scope_id} をpendingにリセットしました"
        else:
            return "スコープが不正です"

    elif req_type == 'RESET_WORKSPACE':
        if scope_type == 'workspace' and scope_id:
            result = db.client.table('Rawdata_FILE_AND_MAIL').update({
                'processing_status': 'pending'
            }).eq('workspace', scope_id).in_('processing_status', ['processing', 'failed']).execute()
            count = len(result.data) if result.data else 0
            return f"workspace {scope_id} の {count}件をpendingにリセットしました"
        else:
            return "スコープが不正です"

    elif req_type == 'CLEAR_STAGES':
        if scope_type == 'document' and scope_id:
            db.client.table('Rawdata_FILE_AND_MAIL').update(STAGE_FIELDS_TO_CLEAR).eq('id', scope_id).execute()
            return f"ドキュメント {scope_id} のステージをクリアしました"
        elif scope_type == 'workspace' and scope_id:
            result = db.client.table('Rawdata_FILE_AND_MAIL').update(STAGE_FIELDS_TO_CLEAR).eq('workspace', scope_id).execute()
            count = len(result.data) if result.data else 0
            return f"workspace {scope_id} の {count}件のステージをクリアしました"
        else:
            return "スコープが不正です"

    elif req_type == 'PAUSE':
        # 一時停止（ops_requests に queued 状態で存在することで機能）
        # _check_stop_request が ops_requests の PAUSE を検出して停止する
        if scope_type == 'workspace' and scope_id:
            # ops_requests に queued で入っている時点で停止が効いている
            return f"workspace {scope_id} を一時停止しました（ops_requests SSOT）"
        else:
            # グローバル停止（派生キャッシュ更新）
            db.client.table('worker_state').update({
                'stop_requested': True
            }).eq('id', 1).execute()
            return "グローバル停止フラグを設定しました"

    else:
        return f"不明な request_type: {req_type}"


# ============================================================
# Clear-Worker-State コマンド（レガシー互換）
# ============================================================

def cmd_clear_worker_state(args):
    """Worker状態をクリア（レガシー互換、release-lease推奨）"""
    print("\n[WARNING] このコマンドは非推奨です。release-lease の使用を推奨します。")
    print("  python ops.py release-lease --workspace <workspace>\n")

    db = get_db_client()

    try:
        db.client.table('worker_state').update({
            'is_processing': False,
            'stop_requested': False,
            'current_index': 0,
            'total_count': 0,
            'current_file': '',
            'success_count': 0,
            'error_count': 0,
            'logs': []
        }).eq('id', 1).execute()

        print("[OK] Worker状態をクリアしました\n")
        return 0

    except Exception as e:
        logger.error(f"クリアエラー: {e}")
        return 1


# ============================================================
# メイン
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description='Ops CLI - ドキュメント管理システム運用コマンド（SSOT）',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用例:
  # 統計情報
  python ops.py stats
  python ops.py stats --workspace ema_classroom

  # 停止要求
  python ops.py stop
  python ops.py stop --workspace ema_classroom

  # リース解放
  python ops.py release-lease --workspace ema_classroom

  # ステータスリセット（dry-run → apply）
  python ops.py reset-status --workspace ema_classroom
  python ops.py reset-status --workspace ema_classroom --apply

  # ステージクリア（dry-run → apply）
  python ops.py reset-stages --workspace ema_classroom
  python ops.py reset-stages --workspace ema_classroom --apply

  # ops_requests 管理
  python ops.py requests          # 一覧表示
  python ops.py requests --apply  # 適用

【重要】
  処理実行は Worker CLI を使用してください:
  python scripts/processing/process_queued_documents.py --execute
        """
    )

    subparsers = parser.add_subparsers(dest='command', help='サブコマンド')

    # stats
    stats_parser = subparsers.add_parser('stats', help='統計情報を表示')
    stats_parser.add_argument('--workspace', default='all', help='対象ワークスペース')

    # stop
    stop_parser = subparsers.add_parser('stop', help='停止要求を登録')
    stop_parser.add_argument('--workspace', help='対象ワークスペース')
    stop_parser.add_argument('--reason', help='停止理由')
    stop_parser.add_argument('--immediate', action='store_true',
                            help='即時適用（派生キャッシュに反映）')

    # release-lease
    release_parser = subparsers.add_parser('release-lease', help='リース解放要求を登録')
    release_parser.add_argument('--workspace', help='対象ワークスペース')
    release_parser.add_argument('--doc-id', help='対象ドキュメントID')
    release_parser.add_argument('--force', action='store_true', help='強制解放')

    # reset-status
    reset_status_parser = subparsers.add_parser('reset-status', help='processing→pendingにリセット')
    reset_status_parser.add_argument('--workspace', help='対象ワークスペース')
    reset_status_parser.add_argument('--doc-id', help='対象ドキュメントID')
    reset_status_parser.add_argument('--apply', action='store_true', help='実際に適用（なければdry-run）')
    reset_status_parser.add_argument('--yes', '-y', action='store_true', help='確認をスキップ')

    # reset-stages
    reset_stages_parser = subparsers.add_parser('reset-stages', help='ステージE-Kをクリアしてpendingに戻す')
    reset_stages_parser.add_argument('--workspace', help='対象ワークスペース')
    reset_stages_parser.add_argument('--doc-id', help='対象ドキュメントID')
    reset_stages_parser.add_argument('--status', default='completed', help='対象ステータス（デフォルト: completed）')
    reset_stages_parser.add_argument('--apply', action='store_true', help='実際に適用（なければdry-run）')
    reset_stages_parser.add_argument('--yes', '-y', action='store_true', help='確認をスキップ')

    # requests
    requests_parser = subparsers.add_parser('requests', help='ops_requests の管理')
    requests_parser.add_argument('--apply', action='store_true', help='未処理の要求を適用')

    # clear-worker-state（レガシー互換）
    clear_parser = subparsers.add_parser('clear-worker-state', help='[非推奨] Worker状態をクリア')

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return 0

    if args.command == 'stats':
        return cmd_stats(args)
    elif args.command == 'stop':
        return cmd_stop(args)
    elif args.command == 'release-lease':
        return cmd_release_lease(args)
    elif args.command == 'reset-status':
        return cmd_reset_status(args)
    elif args.command == 'reset-stages':
        return cmd_reset_stages(args)
    elif args.command == 'requests':
        return cmd_requests(args)
    elif args.command == 'clear-worker-state':
        return cmd_clear_worker_state(args)
    else:
        parser.print_help()
        return 1


if __name__ == '__main__':
    sys.exit(main())
