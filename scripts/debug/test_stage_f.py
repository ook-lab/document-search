"""
Stage F テストスクリプト（データ統合・正規化）

使用例:
    # Stage A-E の結果ファイルを指定
    python scripts/debug/test_stage_f.py \
        --stage-a "path/to/stage_a_result.json" \
        --stage-b "path/to/stage_b_result.json" \
        --stage-e "path/to/stage_e_result.json" \
        --api-key "YOUR_GEMINI_API_KEY"
"""

import sys
import json
import os
import argparse
from pathlib import Path

# プロジェクトルートをパスに追加
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from loguru import logger
from shared.pipeline.stage_f import F1Controller


def load_json(file_path: Path) -> dict:
    """JSON ファイルを読み込み"""
    if not file_path or not file_path.exists():
        return {}

    with open(file_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def main():
    """Stage F テスト実行"""
    parser = argparse.ArgumentParser(description='Stage F データ統合・正規化テスト')
    parser.add_argument('--stage-a', type=str, help='Stage A 結果ファイル')
    parser.add_argument('--stage-b', type=str, help='Stage B 結果ファイル')
    parser.add_argument('--stage-d', type=str, help='Stage D 結果ファイル')
    parser.add_argument('--stage-e', type=str, help='Stage E 結果ファイル')
    parser.add_argument('--api-key', type=str, help='Gemini API Key（日付正規化用）')
    parser.add_argument('--year', type=int, help='年度コンテキスト（例: 2025）')
    parser.add_argument('--output', type=str, help='出力ディレクトリ（オプション）')

    args = parser.parse_args()

    # API Key の取得（環境変数 or 引数）
    api_key = args.api_key or os.environ.get('GEMINI_API_KEY')
    if not api_key:
        logger.warning("Gemini API Key が設定されていません（日付正規化はスキップされます）")

    # 結果ファイルを読み込み
    stage_a_result = load_json(Path(args.stage_a)) if args.stage_a else None
    stage_b_result = load_json(Path(args.stage_b)) if args.stage_b else None
    stage_d_result = load_json(Path(args.stage_d)) if args.stage_d else None
    stage_e_result = load_json(Path(args.stage_e)) if args.stage_e else None

    if not any([stage_a_result, stage_b_result, stage_d_result, stage_e_result]):
        logger.error("少なくとも1つのステージ結果ファイルを指定してください")
        sys.exit(1)

    logger.info("=" * 60)
    logger.info("Stage F データ統合・正規化テスト開始")
    logger.info("=" * 60)

    # Stage F 実行
    controller = F1Controller(gemini_api_key=api_key)
    result = controller.process(
        stage_a_result=stage_a_result,
        stage_b_result=stage_b_result,
        stage_d_result=stage_d_result,
        stage_e_result=stage_e_result,
        year_context=args.year
    )

    # 結果を表示
    logger.info("\n[Stage F] 実行結果:")
    logger.info(f"  成功: {result.get('success')}")

    if result.get('success'):
        # ドキュメント情報
        doc_info = result.get('document_info', {})
        logger.info("\n  ドキュメント情報:")
        logger.info(f"    ├─ タイプ: {doc_info.get('document_type')}")
        logger.info(f"    └─ 年度: {doc_info.get('year_context')}")

        # 正規化されたイベント
        events = result.get('normalized_events', [])
        if events:
            logger.info(f"\n  正規化イベント: {len(events)}件")
            for i, event in enumerate(events[:5]):
                logger.info(f"    [{i+1}] {event.get('normalized_date', 'N/A')}: {event.get('event', event.get('original_text', 'N/A'))}")
            if len(events) > 5:
                logger.info(f"    ... 他 {len(events) - 5}件")

        # タスク
        tasks = result.get('tasks', [])
        if tasks:
            logger.info(f"\n  タスク: {len(tasks)}件")
            for i, task in enumerate(tasks[:3]):
                logger.info(f"    [{i+1}] {task.get('deadline', 'N/A')}: {task.get('item', 'N/A')}")
            if len(tasks) > 3:
                logger.info(f"    ... 他 {len(tasks) - 3}件")

        # 注意事項
        notices = result.get('notices', [])
        if notices:
            logger.info(f"\n  注意事項: {len(notices)}件")
            for i, notice in enumerate(notices[:3]):
                logger.info(f"    [{i+1}] {notice.get('category', 'N/A')}: {notice.get('content', 'N/A')}")
            if len(notices) > 3:
                logger.info(f"    ... 他 {len(notices) - 3}件")

        # 統合された表
        tables = result.get('consolidated_tables', [])
        if tables:
            logger.info(f"\n  統合表: {len(tables)}個")
            for i, table in enumerate(tables):
                table_id = table.get('table_id', f'T{i+1}')
                source = table.get('source', 'unknown')
                logger.info(f"    [{i+1}] {table_id} (ソース: {source})")

                # 結合情報
                joined_from = table.get('joined_from')
                if joined_from:
                    logger.info(f"        結合元: {', '.join(joined_from)}")

        # メタデータ
        metadata = result.get('metadata', {})
        logger.info(f"\n  メタデータ:")
        logger.info(f"    ├─ 総トークン: {metadata.get('total_tokens', 0)}")
        logger.info(f"    ├─ 処理ステージ: {', '.join(metadata.get('stages_processed', []))}")
        logger.info(f"    └─ 使用モデル: {', '.join(metadata.get('models_used', []))}")

        # 統合テキストのプレビュー
        raw_text = result.get('raw_integrated_text', '')
        if raw_text:
            logger.info(f"\n  統合テキスト（先頭500文字）:")
            logger.info(f"    {raw_text[:500]}...")

        # JSON形式で保存
        output_dir = Path(args.output) if args.output else Path.cwd()
        output_path = output_dir / "stage_f_result.json"

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        logger.info(f"\n  結果を保存: {output_path}")

    else:
        logger.error(f"  エラー: {result.get('error')}")

    logger.info("=" * 60)


if __name__ == "__main__":
    main()
