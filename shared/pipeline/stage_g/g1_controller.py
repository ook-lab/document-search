"""
G-1: Stage G Controller（Orchestrator）

Stage G の各コンポーネントを統合し、UIデリバリー用構造化を実行する。

パイプライン:
G-1: High-Fidelity Table Reproduction（表の完全再現）
  ↓
G-3: Semantic Block Arrangement（ブロック整頓）
  ↓
G-5: Noise Elimination（ノイズ除去）
  ↓
G-1: Controller（全体統合）
"""

from pathlib import Path
from typing import Dict, Any, Optional
from loguru import logger

from .g1_table_reproducer import G1TableReproducer
from .g3_block_arranger import G3BlockArranger
from .g5_noise_eliminator import G5NoiseEliminator


class G1Controller:
    """G-1: Stage G Controller（Orchestrator）"""

    def __init__(self):
        """G-1 コントローラー初期化"""
        self.table_reproducer = G1TableReproducer()
        self.block_arranger = G3BlockArranger()
        self.noise_eliminator = G5NoiseEliminator()

    def process(
        self,
        stage_f_result: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Stage G UIデリバリー用構造化を実行

        Args:
            stage_f_result: Stage F の結果

        Returns:
            {
                'success': bool,
                'ui_data': dict,  # クリーンなUI用データ
                'metadata': dict
            }
        """
        logger.info("=" * 60)
        logger.info("[G-1] Stage G UIデリバリー構造化開始")
        logger.info("=" * 60)

        try:
            # Step 1: Table Reproduction
            logger.info("\n[G-1] ステップ1: 表の完全再現（G-1）")

            tables = stage_f_result.get('consolidated_tables', [])
            table_result = self.table_reproducer.reproduce(tables)

            if not table_result.get('success'):
                logger.warning(f"[G-1] 表変換失敗: {table_result.get('error')}")
                ui_tables = []
            else:
                ui_tables = table_result['ui_tables']
                logger.info(f"[G-1] 表変換: {table_result['conversion_count']}個")

            # Step 2: Block Arrangement
            logger.info("\n[G-1] ステップ2: ブロック整頓（G-3）")

            raw_text = stage_f_result.get('raw_integrated_text', '')
            events = stage_f_result.get('normalized_events', [])
            tasks = stage_f_result.get('tasks', [])
            notices = stage_f_result.get('notices', [])

            block_result = self.block_arranger.arrange(
                raw_text=raw_text,
                events=events,
                tasks=tasks,
                notices=notices
            )

            if not block_result.get('success'):
                logger.warning(f"[G-1] ブロック整頓失敗: {block_result.get('error')}")
                blocks = []
            else:
                blocks = block_result['blocks']
                logger.info(f"[G-1] ブロック整頓: {block_result['block_count']}個")

            # Step 3: Noise Elimination
            logger.info("\n[G-1] ステップ3: ノイズ除去（G-5）")

            elimination_result = self.noise_eliminator.eliminate(
                stage_f_result=stage_f_result,
                ui_tables=ui_tables,
                blocks=blocks
            )

            if not elimination_result.get('success'):
                logger.error(f"[G-1] ノイズ除去失敗: {elimination_result.get('error')}")
                return elimination_result

            ui_data = elimination_result['ui_data']

            # メタデータを構築
            metadata = {
                'stage': 'G',
                'conversion_count': table_result.get('conversion_count', 0),
                'block_count': block_result.get('block_count', 0),
                'total_tokens': stage_f_result.get('metadata', {}).get('total_tokens', 0)
            }

            logger.info("=" * 60)
            logger.info("[G-1] Stage G 完了")
            logger.info(f"  ├─ 表: {len(ui_tables)}個")
            logger.info(f"  ├─ ブロック: {len(blocks)}個")
            logger.info(f"  ├─ イベント: {len(ui_data.get('timeline', []))}件")
            logger.info(f"  ├─ タスク: {len(ui_data.get('actions', []))}件")
            logger.info(f"  └─ 注意事項: {len(ui_data.get('notices', []))}件")
            logger.info("=" * 60)

            return {
                'success': True,
                'ui_data': ui_data,
                'metadata': metadata
            }

        except Exception as e:
            logger.error(f"[G-1] 処理エラー: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'ui_data': {},
                'metadata': {}
            }
