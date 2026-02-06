"""
G6: Packager（用途別出力整形）

【Ver 9.0】I/O契約（固定）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
入力:
  - scrubbed_data: G5の出力（唯一の正本）

出力（用途別）:
  - payload_for_db: DB格納用
  - payload_for_search: 検索用
  - payload_for_ui: UI表示用
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ルール:
- 値の変更禁止 ← read-only
- AI禁止
- "変換" は可（型変換・キー名・構造）
"""

import time
from typing import Dict, Any, List, Optional
from loguru import logger


class G6Packager:
    """G6: Packager - 用途別出力整形（read-only）"""

    def __init__(self):
        pass

    def package(
        self,
        scrubbed_data: Dict[str, Any],
        targets: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        下流別にフォーマットを変える

        Args:
            scrubbed_data: G5の出力（唯一の正本）
            targets: 出力対象 ['db', 'search', 'ui'] (デフォルト: 全部)

        Returns:
            {
                'payload_for_db': {...},
                'payload_for_search': {...},
                'payload_for_ui': {...}
            }
        """
        g6_start = time.time()

        if targets is None:
            targets = ['db', 'search', 'ui']

        logger.info(f"[G6] Packager開始: targets={targets}")

        result = {}

        if 'db' in targets:
            result['payload_for_db'] = self._package_for_db(scrubbed_data)

        if 'search' in targets:
            result['payload_for_search'] = self._package_for_search(scrubbed_data)

        if 'ui' in targets:
            result['payload_for_ui'] = self._package_for_ui(scrubbed_data)

        elapsed = time.time() - g6_start
        logger.info(f"[G6] Packager完了: {len(result)}種類, elapsed={elapsed:.2f}s")

        return result

    def _package_for_db(self, scrubbed_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        DB格納用フォーマット

        - IDs、型、参照、フラット化
        - 正規化スキーマ
        """
        path_a = scrubbed_data.get('path_a_result', {})
        tagged_texts = path_a.get('tagged_texts', [])
        anchors = scrubbed_data.get('anchors', [])

        # documents
        documents = {
            'schema_version': scrubbed_data.get('schema_version'),
            'text_source': path_a.get('text_source'),
            'text_source_by_page': path_a.get('text_source_by_page', {}),
            'quality_score': scrubbed_data.get('metadata', {}).get('quality_score', 0),
            'anomaly_count': scrubbed_data.get('metadata', {}).get('anomaly_count', 0),
        }

        # pages
        pages = {}
        for t in tagged_texts:
            page = t.get('page', 0)
            if page not in pages:
                pages[page] = {
                    'page_num': page,
                    'text_source': path_a.get('text_source_by_page', {}).get(page, 'vision_ocr'),
                    'cell_count': 0,
                    'text_count': 0,
                }
            if t.get('type') == 'cell':
                pages[page]['cell_count'] += 1
            else:
                pages[page]['text_count'] += 1

        # tables (フラット化)
        tables = []
        for anchor in anchors:
            if anchor.get('type') == 'table':
                tables.append({
                    'anchor_id': anchor.get('anchor_id'),
                    'x_headers': anchor.get('x_headers', []),
                    'y_headers': anchor.get('y_headers', []),
                    'row_count': anchor.get('row_count', 0),
                    'col_count': anchor.get('col_count', 0),
                    'is_heavy': anchor.get('is_heavy', False),
                })

        # cells (フラット化)
        cells = []
        for t in tagged_texts:
            if t.get('type') == 'cell':
                cells.append({
                    'id': t.get('id'),
                    'text': t.get('text', ''),
                    'x_header': t.get('x_header', ''),
                    'y_header': t.get('y_header', ''),
                    'page': t.get('page', 0),
                    'bbox': t.get('bbox'),
                    'source': t.get('_source', 'unknown'),
                })

        # provenance
        provenance = {
            'change_log': scrubbed_data.get('change_log', []),
            'anomaly_report': scrubbed_data.get('anomaly_report', []),
            'quality_detail': scrubbed_data.get('quality_detail', {}),
        }

        return {
            'documents': documents,
            'pages': list(pages.values()),
            'tables': tables,
            'cells': cells,
            'anchors': anchors,
            'provenance': provenance,
        }

    def _package_for_search(self, scrubbed_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        検索用フォーマット

        - 全文テキスト
        - テーブルのキー列
        - インデックス語彙
        """
        path_a = scrubbed_data.get('path_a_result', {})
        tagged_texts = path_a.get('tagged_texts', [])

        # unified_text: 全テキストを連結
        unified_text = path_a.get('full_text_ordered', '')

        # table_text: 列ヘッダー＋セルを検索向けに連結
        table_parts = []
        x_headers = path_a.get('x_headers', [])
        y_headers = path_a.get('y_headers', [])

        if x_headers:
            table_parts.append(' '.join(x_headers))
        if y_headers:
            table_parts.append(' '.join(y_headers))

        for t in tagged_texts:
            if t.get('type') == 'cell':
                cell_text = t.get('text', '')
                x_h = t.get('x_header', '')
                y_h = t.get('y_header', '')
                if x_h or y_h:
                    table_parts.append(f"{y_h} {x_h}: {cell_text}")
                else:
                    table_parts.append(cell_text)

        table_text = '\n'.join(table_parts)

        # keywords: 列役割やヘッダーから抽出
        keywords = list(set(x_headers + y_headers))

        return {
            'unified_text': unified_text,
            'table_text': table_text,
            'keywords': keywords,
            'x_headers': x_headers,
            'y_headers': y_headers,
        }

    def _package_for_ui(self, scrubbed_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        UI表示用フォーマット

        - ページ単位の表示モデル
        - セルbbox付き
        - アンカーマップ
        """
        path_a = scrubbed_data.get('path_a_result', {})
        tagged_texts = path_a.get('tagged_texts', [])
        anchors = scrubbed_data.get('anchors', [])

        # page_render_model: ページ単位の表示
        pages = {}
        for t in tagged_texts:
            page = t.get('page', 0)
            if page not in pages:
                pages[page] = {
                    'page_num': page,
                    'cells': [],
                    'texts': [],
                }
            item = {
                'id': t.get('id'),
                'text': t.get('text', ''),
                'bbox': t.get('bbox'),
                'x_header': t.get('x_header', ''),
                'y_header': t.get('y_header', ''),
            }
            if t.get('type') == 'cell':
                pages[page]['cells'].append(item)
            else:
                pages[page]['texts'].append(item)

        page_render_model = list(pages.values())

        # table_render_model: セルbbox付き
        table_render_model = []
        for anchor in anchors:
            if anchor.get('type') == 'table':
                table_render_model.append({
                    'anchor_id': anchor.get('anchor_id'),
                    'x_headers': anchor.get('x_headers', []),
                    'y_headers': anchor.get('y_headers', []),
                    'cells': [
                        {
                            'id': c.get('id'),
                            'text': c.get('text', ''),
                            'bbox': c.get('bbox'),
                            'x': c.get('x_header', ''),
                            'y': c.get('y_header', ''),
                        }
                        for c in anchor.get('tagged_texts', [])
                    ]
                })

        # anchor_map: 本文↔表の対応
        anchor_map = {
            a.get('anchor_id'): {
                'type': a.get('type'),
                'page': a.get('page', 0) if a.get('type') == 'text' else None,
            }
            for a in anchors
        }

        return {
            'page_render_model': page_render_model,
            'table_render_model': table_render_model,
            'anchor_map': anchor_map,
            'quality': scrubbed_data.get('quality_detail', {}),
            'warnings': scrubbed_data.get('warnings', []),
        }
