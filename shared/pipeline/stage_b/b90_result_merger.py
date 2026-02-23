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
        log_file=None,
        original_pdf_path: Optional[str] = None,
        total_pages: int = 0,
    ) -> Dict[str, Any]:
        """
        複数 B プロセッサの生結果リストを単一の stage_b_result にマージ

        Args:
            raw_results: B プロセッサの生結果リスト
            log_file: 個別ログファイルパス（Noneなら共有ロガーのみ）

        Returns:
            F1 が読める統合 stage_b_result
        """
        _sink_id = None
        if log_file:
            _sink_id = logger.add(
                str(log_file),
                format="{time:HH:mm:ss} | {level:<5} | {message}",
                filter=lambda r: "[B-90]" in r["message"],
                level="DEBUG",
                encoding="utf-8",
            )

        logger.info("=" * 70)
        logger.info(f"[B-90] Result Merger 開始: {len(raw_results)}件")

        all_logical_blocks: List[Dict] = []
        all_structured_tables: List[Dict] = []
        purged_sources: List[Dict] = []  # {'path': str, 'pages': List[int]}
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

            # purged PDF を pages と共に収集（後でページ順マージ）
            if result.get('purged_pdf_path'):
                purged_sources.append({
                    'path': result['purged_pdf_path'],
                    'pages': source_pages,
                })
                logger.info(f"[B-90]   purged PDF 登録: {Path(result['purged_pdf_path']).name} pages={source_pages}")

        # ページ番号 → Y座標 順にソート
        all_logical_blocks.sort(key=lambda b: (
            b.get('page', 0),
            b.get('bbox', [0, 0, 0, 0])[1] if b.get('bbox') else 0
        ))

        # purged PDF をページ順にマージ
        if len(purged_sources) > 1:
            merged_purged = self._merge_purged_pdfs(purged_sources, original_pdf_path, total_pages)
        elif purged_sources:
            merged_purged = purged_sources[0]['path']
        else:
            merged_purged = ''

        logger.info(f"[B-90] マージ完了:")
        logger.info(f"  ├─ 成功プロセッサ: {success_count}/{len(raw_results)}")
        logger.info(f"  ├─ logical_blocks: {len(all_logical_blocks)}件")
        logger.info(f"  ├─ structured_tables: {len(all_structured_tables)}件")
        logger.info(f"  ├─ source_types: {source_types}")
        logger.info(f"  └─ purged PDF: {Path(merged_purged).name if merged_purged else 'なし'}")
        for idx, block in enumerate(all_logical_blocks):
            logger.info(f"[B-90] block{idx} (page={block.get('page')}): {block.get('text', '')}")
        logger.info("=" * 70)

        if _sink_id is not None:
            logger.remove(_sink_id)

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
            'purged_pdf_path': merged_purged,
            'b_source_types': source_types,
        }

    # ------------------------------------------------------------------
    # purged PDF マージ
    # ------------------------------------------------------------------

    def _merge_purged_pdfs(
        self,
        purged_sources: List[Dict],
        original_pdf_path: Optional[str],
        total_pages: int,
    ) -> str:
        """
        各プロセッサの purged サブPDF を元のページ順にマージする。

        purged_sources[i]['pages'] = 元PDFでのページ番号リスト（0始まり）
        サブPDF内のページ順は pages[0]→0, pages[1]→1, ... に対応する。
        """
        try:
            import fitz

            # page_num → (sub_pdf_path, sub_pdf_page_index)
            page_map: Dict = {}
            for source in purged_sources:
                for sub_idx, orig_page in enumerate(source['pages']):
                    page_map[orig_page] = (source['path'], sub_idx)

            # 総ページ数を決定
            if not total_pages:
                total_pages = max(page_map.keys()) + 1 if page_map else 0

            logger.info(f"[B-90] purged PDF マージ開始: {total_pages}ページ")

            # ソースPDFをキャッシュ（同じファイルを何度も開かない）
            open_docs: Dict[str, Any] = {}
            for source in purged_sources:
                p = source['path']
                if p not in open_docs:
                    open_docs[p] = fitz.open(p)

            orig_doc = fitz.open(original_pdf_path) if original_pdf_path else None

            merged = fitz.open()
            for page_num in range(total_pages):
                if page_num in page_map:
                    sub_path, sub_idx = page_map[page_num]
                    src = open_docs[sub_path]
                    merged.insert_pdf(src, from_page=sub_idx, to_page=sub_idx)
                    logger.info(f"[B-90]   ページ{page_num + 1}: {Path(sub_path).name}[{sub_idx}]")
                elif orig_doc and page_num < len(orig_doc):
                    merged.insert_pdf(orig_doc, from_page=page_num, to_page=page_num)
                    logger.info(f"[B-90]   ページ{page_num + 1}: 元PDF（未処理ページ）")

            for doc in open_docs.values():
                doc.close()
            if orig_doc:
                orig_doc.close()

            first_path = purged_sources[0]['path']
            merged_path = Path(first_path).parent / "b90_merged_purged.pdf"
            merged.save(str(merged_path))
            merged.close()

            logger.info(f"[B-90] マージ済み purged PDF: {merged_path.name}")
            return str(merged_path)

        except Exception as e:
            logger.error(f"[B-90] purged PDF マージ失敗: {e}", exc_info=True)
            return purged_sources[0]['path'] if purged_sources else ''

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
