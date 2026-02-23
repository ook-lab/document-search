"""
E-1: Stage E Controller（唯一の執行者）

重要ルール（確定仕様をコードで強制）:
  - E32 は候補集合化で止める（next_stage で E40 は呼ばない）
  - E40 は controller が明示実行（E32 or E37 に候補があれば）
  - E37 単体を F へ渡すのは禁止（E40 成功分だけ table_contents に入る）
  - 表 OCR（E30/E31）は char_count >= 500 のときだけ

分岐ルール（char_count）:
  非表:
    >= 500文字  → E21（Context Extractor）
    1-499文字   → E21
    0文字       → スキップ
  表:
    >= 500文字  → E30→E31→E32（候補集合）→ E40（SSOT）
    1-499文字   → E32スキップ、E37のみ → E40（E37候補あれば）
    0文字       → E32/E37スキップ

出力キー（F との互換性を維持）:
  non_table_content  = 非表抽出結果（F1 が読む）
  table_contents     = E40 table_ssot リスト（F1 が読む正本）
  table_audit        = E37 監査リスト（F に渡さない）
  metadata           = トークン情報（F1 が読む）
"""

from loguru import logger
from pathlib import Path
from typing import Optional

from .e1_ocr_scouter import E1OcrScouter
from .e5_text_block_visualizer import E5TextBlockVisualizer
from .e16_line_eraser import E16LineEraser
from .e20_non_table_vision_ocr import E20NonTableVisionOcr
from .e21_context_extractor import E21ContextExtractor
from .e30_table_structure_extractor import E30TableStructureExtractor
from .e31_table_vision_ocr import E31TableVisionOcr
from .e32_table_cell_merger import E32TableCellMerger
from .e37_embedded_cell_assigner import E37EmbeddedCellAssigner
from .e40_image_ssot_consolidator import E40ImageSsotConsolidator

# 表 OCR（E30/E31）を実行する最低文字数閾値
_TABLE_OCR_THRESHOLD = 500


class E1Controller:
    """
    Stage E Controller（唯一の執行者）

    チェーン構造:
      表: E30 → E31 → E32（候補集合で止まる）
      E40 は controller が明示実行（next_stage 連鎖は禁止）
    """

    def __init__(self, gemini_api_key=None):
        self.scouter = E1OcrScouter()
        self.visualizer = E5TextBlockVisualizer()

        # 非表処理
        self.line_eraser = E16LineEraser()
        self.non_table_vision_ocr = E20NonTableVisionOcr()
        self.context_extractor = E21ContextExtractor(api_key=gemini_api_key)

        # 表処理チェーン: E30 → E31 → E32（E32 で止まる。E40 は controller が呼ぶ）
        e32 = E32TableCellMerger()                         # ★ next_stage なし
        e31 = E31TableVisionOcr(next_stage=e32)
        self.table_extractor = E30TableStructureExtractor(
            api_key=gemini_api_key,
            next_stage=e31
        )

        # E37 + E40: controller が唯一の実行者
        self.embedded_assigner = E37EmbeddedCellAssigner()
        self.ssot_consolidator = E40ImageSsotConsolidator()

    # ------------------------------------------------------------------
    # 判定ヘルパー
    # ------------------------------------------------------------------

    @staticmethod
    def _has_e32_candidates(e32_result) -> bool:
        """E32 結果に 1 つ以上のテキスト候補があるか"""
        if not e32_result or not e32_result.get('success'):
            return False
        cands = e32_result.get('image_candidates', []) or []
        return any((c.get('items') or []) for c in cands)

    @staticmethod
    def _has_e37_candidates(e37_result) -> bool:
        """E37 結果に 1 つ以上の embedded 候補があるか"""
        if not e37_result or not e37_result.get('success'):
            return False
        cands = e37_result.get('embedded_candidates', []) or []
        return len(cands) > 0

    @staticmethod
    def _crop_to_text_roi(
        image_path: Path,
        words: list,
        output_dir=None,
        margin: int = 60,
    ) -> Path:
        """
        E-1のwordsのbbox全体を包む外接矩形にマージンを付けてクロップ。

        根拠: E-1（Tesseract）が文字と判定した領域 = 本文が存在する場所。
             その周辺のみをGeminiに渡すことで、罫線・装飾・円弧などを除外する。

        Args:
            image_path: 元画像パス
            words:      E-1が返した単語リスト [{'text':..., 'bbox':[x1,y1,x2,y2], ...}]
            output_dir: 保存先（省略時は元画像と同ディレクトリ）
            margin:     外接矩形に加えるマージン（px）。行間・句読点を拾うため。

        Returns:
            クロップ済み画像パス（失敗時は元画像パスをフォールバック）
        """
        import cv2

        output_dir = Path(output_dir) if output_dir else image_path.parent
        output_path = output_dir / (image_path.stem + "_roi" + image_path.suffix)

        img = cv2.imread(str(image_path))
        if img is None:
            logger.error(f"[E-1] ROIクロップ: 画像読み込み失敗 → 元画像で続行")
            return image_path

        img_h, img_w = img.shape[:2]

        # words の bbox を union
        x1 = min(w['bbox'][0] for w in words)
        y1 = min(w['bbox'][1] for w in words)
        x2 = max(w['bbox'][2] for w in words)
        y2 = max(w['bbox'][3] for w in words)

        # マージンを加えて画像範囲にクリップ
        rx1 = max(0,     x1 - margin)
        ry1 = max(0,     y1 - margin)
        rx2 = min(img_w, x2 + margin)
        ry2 = min(img_h, y2 + margin)

        crop = img[ry1:ry2, rx1:rx2]
        cv2.imwrite(str(output_path), crop)
        logger.info(
            f"[E-1] ROIクロップ完了: ({rx1},{ry1})-({rx2},{ry2}) "
            f"({rx2-rx1}x{ry2-ry1}px) → {output_path.name}"
        )
        return output_path

    # ------------------------------------------------------------------
    # メイン処理
    # ------------------------------------------------------------------

    def process(
        self,
        purged_pdf_path,
        stage_d_result,
        stage_b_result=None,
        output_dir=None,
        gemini_api_key=None,
        log_dir=None,
    ):
        """
        Stage E 処理実行

        Args:
            purged_pdf_path: purged PDF パス
            stage_d_result: Stage D の結果
            stage_b_result: Stage B の結果（E37 監査用。なくても動作）
            output_dir: 出力ディレクトリ
            gemini_api_key: API Key（オプション）
            log_dir: ログディレクトリ（オプション）

        Returns:
            {
                'success': bool,
                'non_table_content': dict,    # E21 抽出結果（F1 が読む）
                'table_contents': list[dict], # E40 table_ssot リスト（F1 が読む正本）
                'table_audit': list[dict],    # E37 監査リスト（F には渡さない）
                'page_scout': dict,
                'metadata': dict              # F1 が読む
            }
        """
        return self._process_impl(
            purged_pdf_path, stage_d_result, stage_b_result, output_dir, gemini_api_key
        )

    def _process_impl(
        self,
        purged_pdf_path,
        stage_d_result,
        stage_b_result=None,
        output_dir=None,
        gemini_api_key=None,
    ):
        """process() の実装本体"""
        page = stage_d_result.get('page_index', 0)

        logger.info("=" * 90)
        logger.info(f"[E-1] Stage E 開始 page={page}")
        logger.info("=" * 90)

        # ===========================
        # 非表領域処理（マルチページ対応）
        # non_table_image_paths があれば全ページ分ループ、なければ単一画像にフォールバック
        # ===========================
        total_tokens = 0
        models_used = []
        non_table_content = {}

        # マルチページ: non_table_image_paths リスト、単一: non_table_image_path
        non_table_images = stage_d_result.get('non_table_image_paths') or []
        if not non_table_images:
            single = stage_d_result.get('non_table_image_path')
            if single:
                non_table_images = [single]

        all_non_table_results = []
        for img_idx, non_table_image in enumerate(non_table_images):
            non_table_path = Path(non_table_image)
            if not non_table_path.exists():
                logger.info(f"[E-1] 非表領域画像なし → スキップ: {non_table_path.name}")
                continue

            scout = self.scouter.scout(non_table_path, include_words=True)
            cc = int(scout.get('char_count', 0))
            logger.info(f"[E-1] 非表[{img_idx}] char_count={cc}")

            if cc >= 1:
                words = scout.get('words', [])
                if words:
                    # E-1のwordsからROIクロップ → 本文領域のみをE-21に渡す
                    logger.info(f"[E-1] 非表[{img_idx}]: ROIクロップ({len(words)}単語) → E21 実行")
                    e21_input_path = self._crop_to_text_roi(
                        non_table_path,
                        words,
                        output_dir=Path(output_dir) if output_dir else None,
                    )
                else:
                    # wordsが空だがcc>=1 → E-16（罫線除去）でフォールバック
                    logger.info(f"[E-1] 非表[{img_idx}]: words空 → E16（罫線除去）→ E21 実行")
                    e21_input_path = self.line_eraser.erase(
                        non_table_path,
                        output_dir=Path(output_dir) if output_dir else None,
                    )
                page_ntc = self.context_extractor.extract(
                    e21_input_path,
                    page=page + img_idx,
                    words=words,
                    blocks=[],
                    block_hint='',
                    vision_text=None,
                )
                total_tokens += int(page_ntc.get('tokens_used', 0))
                m = page_ntc.get('model_used')
                if m and m not in models_used:
                    models_used.append(m)
                all_non_table_results.append(page_ntc)
            else:
                logger.info(f"[E-1] 非表[{img_idx}]: char_count=0 → スキップ")

        # 複数ページの非表結果をマージ（schedule/tasks/notices/items を結合）
        if len(all_non_table_results) == 1:
            non_table_content = all_non_table_results[0]
        elif len(all_non_table_results) > 1:
            merged = {'success': True}
            for key in ('schedule', 'tasks', 'notices', 'events', 'items'):
                combined = []
                for r in all_non_table_results:
                    v = r.get(key)
                    if isinstance(v, list):
                        combined.extend(v)
                if combined:
                    merged[key] = combined
            non_table_content = merged
        else:
            logger.info("[E-1] 非表領域: 全画像スキップ")

        # ===========================
        # 表領域処理
        # ===========================
        table_contents = []  # ★ E40 table_ssot のみ（F へ渡す正本）
        table_audit = []     # ★ E37 監査のみ（F には渡さない）

        tables = stage_d_result.get('tables', []) or []
        logger.info("=" * 90)
        logger.info(f"[E-1] 表領域処理: {len(tables)}個")
        logger.info("=" * 90)

        for i, d10_table in enumerate(tables, 1):
            table_id = d10_table.get('table_id', f'T{i}')
            image_path = Path(d10_table.get('image_path', ''))

            logger.info("-" * 90)
            logger.info(f"[E-1] 表 {i}/{len(tables)} id={table_id}")

            if not image_path.exists():
                logger.warning(f"[E-1]   画像なし → スキップ: {image_path}")
                continue

            scout = self.scouter.scout(image_path, include_words=False)
            cc = int(scout.get('char_count', 0))
            logger.info(f"[E-1]   char_count={cc}")

            # ---- E30→E31→E32（画像候補集合）: >= 500 のみ ----
            e32_result = None
            if cc >= _TABLE_OCR_THRESHOLD:
                logger.info(f"[E-1]   char_count>={_TABLE_OCR_THRESHOLD} → E30→E31→E32 実行")
                e32_result = self.table_extractor.extract_structure(
                    image_path,
                    cell_map=d10_table.get('cell_map', []),
                    page_index=d10_table.get('page_index', page),
                    table_index=d10_table.get('table_index'),
                    d10_table=d10_table,
                )
            else:
                logger.info(f"[E-1]   char_count<{_TABLE_OCR_THRESHOLD} → 表 OCR スキップ")

            # ---- E37（B候補集合）: stage_b_result がある場合のみ ----
            e37_result = None
            if stage_b_result:
                e37_result = self.embedded_assigner.assign(
                    d10_table=d10_table,
                    stage_b_result=stage_b_result,
                )
                table_audit.append({
                    'table_id': table_id,
                    'success': bool(e37_result and e37_result.get('success')),
                    'route': (e37_result or {}).get('route'),
                    'embedded_count': len((e37_result or {}).get('embedded_candidates', [])),
                })

            # ---- E40（SSOT）: E32 or E37 に候補があれば実行 ----
            has_e32 = self._has_e32_candidates(e32_result)
            has_e37 = self._has_e37_candidates(e37_result)
            logger.info(f"[E-1]   has_e32={has_e32} has_e37={has_e37}")

            if not (has_e32 or has_e37):
                logger.info("[E-1]   E32/E37 両方空 → E40 スキップ")
                continue

            e40_result = self.ssot_consolidator.consolidate(
                d10_table=d10_table,
                e32_result=e32_result,
                e37_result=e37_result,
            )

            if e40_result and e40_result.get('success'):
                logger.info(f"[E-1]   E40 SSOT 作成完了: table_id={table_id}")
                table_contents.append(e40_result['table_ssot'])
            else:
                logger.warning(
                    f"[E-1]   E40 失敗/スキップ: route={(e40_result or {}).get('route')}"
                )

        logger.info("=" * 90)
        logger.info(
            f"[E-1] Stage E 完了: "
            f"table_ssot={len(table_contents)} table_audit={len(table_audit)}"
        )
        logger.info("=" * 90)

        return {
            'success': True,
            'non_table_content': non_table_content,  # F1 互換キー
            'table_contents': table_contents,         # E40 SSOT → F1 が読む正本
            'table_audit': table_audit,               # E37 監査 → F には渡さない
            'page_scout': {},
            'metadata': {'total_tokens': total_tokens, 'models_used': models_used},
        }
