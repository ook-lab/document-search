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
from .g11_table_structurer import G11TableStructurer
from .g12_table_ai_processor import G12TableAIProcessor
from .g21_text_structurer import G21TextStructurer
from .g22_text_ai_processor import G22TextAIProcessor


class G1Controller:
    """G-1: Stage G Controller（Orchestrator）"""

    def __init__(self, api_key: Optional[str] = None):
        """
        G-1 コントローラー初期化

        Args:
            api_key: Google AI API Key（G-12/G-22で使用）
        """
        self.table_reproducer = G1TableReproducer()
        self.block_arranger = G3BlockArranger()
        self.noise_eliminator = G5NoiseEliminator()
        self.table_structurer = G11TableStructurer()
        self.table_ai_processor = G12TableAIProcessor(api_key=api_key)
        self.text_structurer = G21TextStructurer()
        self.text_ai_processor = G22TextAIProcessor(api_key=api_key)

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

            # Step 4: Table Structuring (G-11)
            logger.info("\n[G-1] ステップ4: 表の構造化（G-11）")

            table_struct_result = self.table_structurer.structure(ui_tables)
            if not table_struct_result.get('success'):
                logger.warning(f"[G-1] 表の構造化失敗: {table_struct_result.get('error')}")
                structured_tables = []
            else:
                structured_tables = table_struct_result['structured_tables']
                logger.info(f"[G-1] 表の構造化: {len(structured_tables)}個")

            # Step 4.5: Table AI Processing (G-12)
            logger.info("\n[G-1] ステップ4.5: 表のAI処理（G-12）")

            table_ai_result = self.table_ai_processor.process(structured_tables)
            if not table_ai_result.get('success'):
                logger.warning(f"[G-1] 表のAI処理失敗: {table_ai_result.get('error')}")
                table_analyses = []
            else:
                table_analyses = table_ai_result['table_analyses']
                logger.info(f"[G-1] 表のAI処理: {len(table_analyses)}個")

            # Step 5: Text Structuring (G-21)
            logger.info("\n[G-1] ステップ5: テキストの構造化（G-21）")

            text_struct_result = self.text_structurer.structure(
                sections=blocks,
                timeline=ui_data.get('timeline', []),
                actions=ui_data.get('actions', []),
                notices=ui_data.get('notices', [])
            )
            if not text_struct_result.get('success'):
                logger.warning(f"[G-1] テキストの構造化失敗: {text_struct_result.get('error')}")
                final_metadata = {}
            else:
                final_metadata = text_struct_result['metadata']

            # Step 5.5: Text AI Processing (G-22)
            logger.info("\n[G-1] ステップ5.5: テキストのAI処理（G-22）")

            # G-21で生成された articles を使用
            articles = final_metadata.get('articles', [])
            text_ai_result = self.text_ai_processor.process(articles)
            if not text_ai_result.get('success'):
                logger.warning(f"[G-1] テキストのAI処理失敗: {text_ai_result.get('error')}")
                calendar_events = []
                tasks = []
                notices = []
            else:
                calendar_events = text_ai_result.get('calendar_events', [])
                tasks = text_ai_result.get('tasks', [])
                notices = text_ai_result.get('notices', [])
                logger.info(f"[G-1] テキストのAI処理完了: イベント{len(calendar_events)}件, タスク{len(tasks)}件, 注意事項{len(notices)}件")

            # final_metadata を G11/G12/G21/G22 で明確に分離
            final_metadata = {
                # UI表示用（各ステージを明確に区別）
                'g11_output': structured_tables,  # G-11: 表をそのまま出す（加工なし）
                'g12_output': table_analyses,     # G-12: 表の構造化・理解
                'g21_output': articles,           # G-21: テキストをそのまま出す（加工なし）
                'g22_output': {                   # G-22: テキストから情報抽出
                    'calendar_events': calendar_events,
                    'tasks': tasks,
                    'notices': notices
                },

                # グループ化（AI前/AI後）
                'ai_input': {
                    'tables': structured_tables,  # G-11の出力
                    'articles': articles          # G-21の出力
                },
                'ai_output': {
                    'table_analyses': table_analyses,  # G-12の出力
                    'calendar_events': calendar_events,
                    'tasks': tasks,
                    'notices': notices
                },

                # 互換性のために従来のキーも保持
                'structured_tables': ui_tables,  # JavaScriptが期待するキー
                'articles': articles,
                'calendar_events': calendar_events,
                'tasks': tasks,
                'notices': notices,
                'table_analyses': table_analyses
            }

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
            logger.info(f"  ├─ 表のAI解析: {len(table_analyses)}個")
            logger.info(f"  ├─ ブロック: {len(blocks)}個")
            logger.info(f"  ├─ articles: {len(final_metadata.get('articles', []))}件")
            logger.info(f"  ├─ イベント（G-22抽出）: {len(final_metadata.get('calendar_events', []))}件")
            logger.info(f"  ├─ タスク（G-22抽出）: {len(final_metadata.get('tasks', []))}件")
            logger.info(f"  └─ 注意事項（G-22抽出）: {len(final_metadata.get('notices', []))}件")
            logger.info("=" * 60)

            # ログ（最終的な metadata の内容）を出力
            logger.info("")
            logger.info("[G-1] ========== final_metadata 構造（ステージ別） ==========")
            logger.info("【G-11】表をそのまま出す（加工なし）")
            logger.info(f"  └─ tables: {len(final_metadata['g11_output'])}個")
            logger.info("【G-12】表の構造化・理解（AI処理）")
            logger.info(f"  └─ table_analyses: {len(final_metadata['g12_output'])}個")
            logger.info("【G-21】テキストをそのまま出す（加工なし）")
            logger.info(f"  └─ articles: {len(final_metadata['g21_output'])}件")
            logger.info("【G-22】テキストから情報抽出（AI処理）")
            logger.info(f"  ├─ calendar_events: {len(final_metadata['g22_output']['calendar_events'])}件")
            logger.info(f"  ├─ tasks: {len(final_metadata['g22_output']['tasks'])}件")
            logger.info(f"  └─ notices: {len(final_metadata['g22_output']['notices'])}件")
            logger.info("")
            import json
            logger.info("[G-1] ========== final_metadata 完全版 ==========")
            logger.info(json.dumps(final_metadata, ensure_ascii=False, indent=2))
            logger.info("=" * 60)

            return {
                'success': True,
                'ui_data': ui_data,
                'final_metadata': final_metadata,  # G11/G21 で構造化されたメタデータ
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
