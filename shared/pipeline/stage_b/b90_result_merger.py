"""
B-90: Result Merger（複数 B プロセッサ結果のマージ）

役割:
  MIXED 文書で複数の B プロセッサが実行された場合に、
  各プロセッサの生結果を共通フォーマット（logical_blocks + structured_tables）に
  正規化・マージして単一の stage_b_result を返す。

入力:
  raw_results: List[Dict]
    - 各 B プロセッサの生結果
    - '_source_type' キーにプロセッサが担当した種別（REPORT / DTP / WORD 等）
    - '_source_pages' キーにそのプロセッサが処理したページ番号リスト

出力:
  {
    'success': bool,
    'is_structured': bool,
    'processor_name': 'B90_MERGED',
    'logical_blocks': [...],     # F1 が読む（全プロセッサ結果を統合・ページ順）
    'structured_tables': [...],  # F1 が読む（全プロセッサ結果を統合）
    'purged_pdf_path': str,      # 最初の purged PDF（D に渡す）
    'b_source_types': [...],     # デバッグ用：実行した種別リスト
  }
"""

from pathlib import Path
from typing import Any, Dict, List, Optional
from loguru import logger


class B90ResultMerger:
    """B-90: 複数 B プロセッサ結果のマージャー"""

    def merge(
        self,
        raw_results: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        複数 B プロセッサの生結果リストを単一の stage_b_result にマージ

        Args:
            raw_results: B プロセッサの生結果リスト

        Returns:
            F1 が読める統合 stage_b_result
        """
        logger.info("=" * 70)
        logger.info(f"[B-90] Result Merger 開始: {len(raw_results)}件")

        all_logical_blocks: List[Dict] = []
        all_structured_tables: List[Dict] = []
        purged_pdf_path: Optional[str] = None
        source_types: List[str] = []
        success_count = 0

        for i, result in enumerate(raw_results):
            source_type = result.get('_source_type', f'unknown_{i}')
            source_pages = result.get('_source_pages', [])

            logger.info(f"[B-90] 処理 {i+1}/{len(raw_results)}: type={source_type} pages={source_pages}")

            if not result.get('success') and not result.get('is_structured'):
                logger.warning(f"[B-90]   失敗結果 → スキップ")
                continue

            success_count += 1
            source_types.append(source_type)

            # logical_blocks に正規化してマージ
            blocks = self._normalize_to_logical_blocks(result, source_type)
            logger.info(f"[B-90]   logical_blocks: {len(blocks)}件に正規化")
            all_logical_blocks.extend(blocks)

            # structured_tables をマージ
            tables = result.get('structured_tables', []) or []
            logger.info(f"[B-90]   structured_tables: {len(tables)}件")
            all_structured_tables.extend(tables)

            # purged_pdf_path は最初のものを採用
            if not purged_pdf_path and result.get('purged_pdf_path'):
                purged_pdf_path = result['purged_pdf_path']
                logger.info(f"[B-90]   purged_pdf_path 採用: {Path(purged_pdf_path).name}")

        # ページ番号 → Y座標 順にソート
        all_logical_blocks.sort(key=lambda b: (
            b.get('page', 0),
            b.get('bbox', [0, 0, 0, 0])[1] if b.get('bbox') else 0
        ))

        logger.info(f"[B-90] マージ完了:")
        logger.info(f"  ├─ 成功プロセッサ: {success_count}/{len(raw_results)}")
        logger.info(f"  ├─ logical_blocks: {len(all_logical_blocks)}件")
        logger.info(f"  ├─ structured_tables: {len(all_structured_tables)}件")
        logger.info(f"  └─ source_types: {source_types}")
        logger.info("=" * 70)

        if success_count == 0:
            return {
                'success': False,
                'is_structured': False,
                'error': 'すべての B プロセッサが失敗しました',
                'processor_name': 'B90_MERGED',
            }

        return {
            'success': True,
            'is_structured': True,
            'processor_name': 'B90_MERGED',
            'logical_blocks': all_logical_blocks,
            'structured_tables': all_structured_tables,
            'purged_pdf_path': purged_pdf_path or '',
            'b_source_types': source_types,
        }

    # ------------------------------------------------------------------
    # 正規化ヘルパー
    # ------------------------------------------------------------------

    def _normalize_to_logical_blocks(
        self,
        result: Dict[str, Any],
        source_type: str,
    ) -> List[Dict[str, Any]]:
        """
        各 B プロセッサの独自フォーマットを logical_blocks 形式に変換

        対応フォーマット:
          logical_blocks  → B3 / B11 / B30 等（そのまま）
          records         → B42（帳票レコード → ブロック変換）
          paragraphs      → B6 Native Word（段落 → ブロック変換）
        """
        # ── logical_blocks（B3 / B11 / B30 等）──
        if 'logical_blocks' in result:
            blocks = result['logical_blocks'] or []
            for b in blocks:
                b.setdefault('_source', source_type)
            return blocks

        # ── records（B42 帳票）──
        if 'records' in result:
            blocks = []
            for rec in (result['records'] or []):
                text_parts = []
                for key in ('rank', 'name', 'organization', 'score'):
                    val = rec.get(key)
                    if val:
                        text_parts.append(str(val))
                blocks.append({
                    'text': ' | '.join(text_parts),
                    'page': rec.get('page', 0),
                    'bbox': [],
                    'type': 'record',
                    '_source': source_type,
                    '_raw': rec,  # F1 が詳細を参照する場合のため
                })
            return blocks

        # ── paragraphs（B6 Native Word）──
        if 'paragraphs' in result:
            blocks = []
            for idx, para in enumerate(result['paragraphs'] or []):
                text = para.get('text', '').strip()
                if not text:
                    continue
                blocks.append({
                    'text': text,
                    'page': 0,
                    'bbox': [],
                    'type': 'paragraph',
                    '_source': source_type,
                })
            return blocks

        logger.warning(f"[B-90] 未知の結果フォーマット（source_type={source_type}）: "
                        f"keys={list(result.keys())}")
        return []
