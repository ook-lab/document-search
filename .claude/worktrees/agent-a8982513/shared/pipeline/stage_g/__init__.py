"""
Stage G: UI Optimized Structuring（UI最適化構造化）

Stage F の統合データを、doc-review の UI が追加処理なしで
「完全再現」できる形式にパッケージングする。

パイプライン（チェーン）:
G-3 → G-5 → (G-11 → G-13 → G-14 → G-17 & G-6 → G-21 → G-22)

出力:
- UI用表データ（headers[], rows[][]）
- 意味的なテキストブロック
- クリーンな表示用データ（ノイズレス）
"""

from .g1_table_reproducer import G1TableReproducer
from .g3_block_arranger import G3BlockArranger
from .g5_noise_eliminator import G5NoiseEliminator
from .g11_table_structurer import G11TableStructurer
from .g13_repeating_header_detector import G13RepeatingHeaderDetector
from .g14_table_reconstructor import G14TableReconstructor
from .g17_table_ai_processor import G17TableAIProcessor
from .g21_text_structurer import G21TextStructurer
from .g22_text_ai_processor import G22TextAIProcessor


class G1Controller:
    """Stage G チェーン（旧 Controller）"""

    def __init__(self, document_id=None, api_key=None):
        """
        チェーン構築: G-3 → G-5 → (G-11 → G-13 → G-14 → G-17 & G-6 → G-21 → G-22)

        Args:
            document_id: ドキュメントID（Supabase保存用）
            api_key: Google AI API Key
        """
        # ★チェーンパターン: 逆順で構築
        text_ai_processor = G22TextAIProcessor(document_id=document_id, api_key=api_key)
        text_structurer = G21TextStructurer(document_id=document_id, next_stage=text_ai_processor)
        table_ai_processor = G17TableAIProcessor(document_id=document_id, api_key=api_key)
        table_reconstructor = G14TableReconstructor(next_stage=table_ai_processor)
        header_detector = G13RepeatingHeaderDetector(next_stage=table_reconstructor)
        table_structurer = G11TableStructurer(document_id=document_id, next_stage=header_detector)
        noise_eliminator = G5NoiseEliminator(
            table_chain=table_structurer,
            text_chain=text_structurer
        )
        self.block_arranger = G3BlockArranger(next_stage=noise_eliminator)
        self.table_reproducer = G1TableReproducer()

    def process(self, f5_result, log_dir=None):
        """
        Stage G 処理実行（チェーン開始）

        Args:
            f5_result: F-5の結果（直前ステージのみ）
            log_dir: ログディレクトリ（オプション）

        Returns:
            最終UI用データ（チェーン経由）
        """
        from loguru import logger

        logger.info("=" * 60)
        logger.info("[G-1] Stage G UIデリバリー構造化開始（チェーン）")
        logger.info("=" * 60)

        # Step 1: Table Reproduction（G-1の独自処理）
        # ★F-5の結果から必要なデータを取得
        tables = f5_result.get('consolidated_tables', [])
        table_result = self.table_reproducer.reproduce(tables)
        ui_tables = table_result.get('ui_tables', []) if table_result.get('success') else []

        # Step 2: G-1の処理結果を構築
        # ★G-3はG-1の結果のみを受け取る
        g1_result = {
            'success': True,
            'ui_tables': ui_tables,  # G-1で生成
            'raw_text': f5_result.get('non_table_text', ''),
            'events': f5_result.get('normalized_events', []),
            'tasks': f5_result.get('tasks', []),
            'notices': f5_result.get('notices', []),
            'document_info': f5_result.get('document_info', {}),
            'conversion_count': table_result.get('conversion_count', 0),
            'display_fields': f5_result.get('display_fields'),
        }

        logger.info(f"[G-1] 処理完了: 表変換 {g1_result['conversion_count']}個")

        # Step 3: チェーン開始（G-3へ）
        chain_result = self.block_arranger.arrange(g1_result=g1_result)

        # チェーン結果を整形
        ui_data = chain_result.get('ui_data', {})
        table_result_chain = chain_result.get('table_result', {})
        text_result_chain = chain_result.get('text_result', {})

        # ★G-11 → G-13 → G-14 → G-17 のチェーン結果を取得
        g13_result = table_result_chain.get('g13_result', {})
        g14_result = g13_result.get('g14_result', {})
        g17_result = g14_result.get('g17_result', {})
        table_analyses = g17_result.get('table_analyses', [])
        ui_data['tables'] = self._convert_analyses_to_ui_format(table_analyses)

        # ★G-11の結果（構造化済み）をui_dataに含める
        structured_tables = table_result_chain.get('structured_tables', [])
        if structured_tables:
            ui_data['g11_structured_tables'] = structured_tables
            logger.info(f"[G-1] ✓ G-11構造化表をui_dataに追加: {len(structured_tables)}表")

        # ★G-21の結果（articles）をui_dataに含める
        text_metadata = text_result_chain.get('metadata', {})
        articles = text_metadata.get('articles', [])

        # G-22のtopic_sectionsがあればそちらをg21_outputとして使う（AIグループ化済み）
        topic_sections = text_result_chain.get('topic_sections', [])
        g21_output = (
            [{'title': s.get('title', ''), 'body': s.get('body', '')} for s in topic_sections]
            if topic_sections else articles
        )

        if g21_output:
            ui_data['g21_articles'] = g21_output
            logger.info(f"[G-1] ✓ G-21 articlesをui_dataに追加: {len(g21_output)}件 (topic_sections使用: {bool(topic_sections)})")

        # ★G-22の結果（AI抽出）をui_dataに反映
        if text_result_chain.get('success'):
            if 'calendar_events' in text_result_chain:
                ui_data['timeline'] = text_result_chain.get('calendar_events', ui_data.get('timeline', []))
            if 'tasks' in text_result_chain:
                ui_data['actions'] = text_result_chain.get('tasks', ui_data.get('actions', []))
            if 'notices' in text_result_chain:
                ui_data['notices'] = text_result_chain.get('notices', ui_data.get('notices', []))

        final_metadata = {
            'g11_output': structured_tables,
            'g14_output': g14_result.get('g14_reconstructed', []),
            'g17_output': ui_data['tables'],  # ★変換後のデータを使用
            'g21_output': g21_output,          # G22のtopic_sections優先、なければG21暫定
            'g22_output': {
                'calendar_events': text_result_chain.get('calendar_events', []),
                'tasks': text_result_chain.get('tasks', []),
                'notices': text_result_chain.get('notices', []),
            }
        }

        logger.info("=" * 60)
        logger.info("[G-1] Stage G 完了（チェーン）")
        logger.info("=" * 60)

        return {
            'success': True,
            'ui_data': ui_data,
            'final_metadata': final_metadata,
            'metadata': {'stage': 'G', 'conversion_count': table_result.get('conversion_count', 0)}
        }

    def _convert_analyses_to_ui_format(self, table_analyses):
        """G-17の table_analyses を UI表示用フォーマットに変換"""
        ui_tables = []
        for analysis in table_analyses:
            # G-17 の実際の出力: sections[0].data
            sections = analysis.get('sections', [])
            if sections and len(sections) > 0:
                section_data = sections[0].get('data', [])
            else:
                section_data = []

            ui_table = {
                'table_id': analysis.get('table_id', ''),
                'table_type': analysis.get('table_type', 'structured'),
                'description': analysis.get('description', ''),
                'headers': [],  # headers は空（UI側で自動生成される）
                'rows': section_data,  # sections[0].data を rows に設定
                'sections': sections,  # UI契約: 常に sections を含める
                # ★section-level metadata（col_map, row_label_col, header_meanings 等）を保持
                'metadata': sections[0].get('metadata', {}) if sections else analysis.get('metadata', {})
            }
            ui_tables.append(ui_table)
        return ui_tables


__all__ = [
    'G1Controller',
    'G1TableReproducer',
    'G3BlockArranger',
    'G5NoiseEliminator',
    'G11TableStructurer',
    'G21TextStructurer',
]
