"""
E-30: Table Structure Extractor（表構造専用 - Gemini 2.5 Flash-lite）

表画像からセル/行/列/bboxを抽出する「構造専用」コンポーネント。
セルの値・テキストは取得しない（E-31 が担当）。

正しい依存順：
  E-30（構造：セルbbox確定）→ E-31（セルOCR）→ E-32（合成）
"""

from pathlib import Path
from typing import Dict, Any, List, Optional
from loguru import logger
import json

try:
    import google.generativeai as genai
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False
    logger.warning("[E-30] google-generativeai がインストールされていません")


class E30TableStructureExtractor:
    """E-30: Table Structure Extractor（構造専用）"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: str = "gemini-2.5-flash-lite",
        next_stage=None
    ):
        """
        E-30 初期化（チェーンパターン）

        Args:
            api_key: Google AI API Key
            model_name: モデル名
            next_stage: 次のステージ（E-31）のインスタンス
        """
        self.model_name = model_name
        self.api_key = api_key
        self.next_stage = next_stage

        if not GENAI_AVAILABLE:
            logger.error("[E-30] google-generativeai が必要です")
            return

        if api_key:
            genai.configure(api_key=api_key)
            self.model = genai.GenerativeModel(model_name)
            logger.info(f"[E-30] モデル初期化: {model_name}")
        else:
            logger.warning("[E-30] API key が設定されていません")
            self.model = None

    def extract_structure(
        self,
        image_path: Path,
        cell_map: Optional[List[Dict]] = None,
        page_index: Optional[int] = None,
        table_index: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        表画像からセル構造（bbox/row/col）を抽出する。
        セルの値・テキストは取得しない。

        Args:
            image_path: 表画像パス
            cell_map: Stage D の cell_map（行列数ヒント用）
            page_index: ページ番号（table_id生成用）
            table_index: 表番号（table_id生成用）

        Returns:
            {
                'success': bool,
                'table_id': str,         # "E30_p000_t00" 形式
                'cells': [               # 構造セルリスト
                    {
                        'row': int,
                        'col': int,
                        'x0': float,     # 正規化座標 [0,1]（表画像基準）
                        'y0': float,
                        'x1': float,
                        'y1': float,
                        'rowspan': int,
                        'colspan': int
                    }, ...
                ],
                'n_rows': int,
                'n_cols': int,
                'route': 'E30_STRUCTURE_ONLY',
                'model_used': str,
                'tokens_used': int
            }
        """
        if not GENAI_AVAILABLE or not self.model:
            logger.error("[E-30] Gemini API が利用できません")
            return self._error_result("Gemini API not available", page_index, table_index)

        if page_index is not None and table_index is not None:
            table_id = f"E30_p{page_index:03d}_t{table_index:02d}"
        else:
            table_id = "E30_Unknown"

        logger.info("=" * 80)
        logger.info(f"[E-30] 構造抽出開始: {image_path.name} table_id={table_id}")
        logger.info(f"[E-30] page_index={page_index}, table_index={table_index}")
        logger.info("=" * 80)

        try:
            with open(image_path, 'rb') as f:
                image_data = f.read()

            logger.info(f"[E-30] 表画像サイズ: {len(image_data)} bytes")

            prompt = self._build_structure_prompt(cell_map)

            logger.info(f"[E-30] モデル: {self.model_name}")
            logger.info(f"[E-30] プロンプト長: {len(prompt)}文字")

            # Stage D cell_map のヒント情報
            if cell_map:
                rows_hint = max((c.get('row', 0) for c in cell_map), default=0) + 1
                cols_hint = max((c.get('col', 0) for c in cell_map), default=0) + 1
                logger.info(f"[E-30] Stage D ヒント: {rows_hint}行 × {cols_hint}列, {len(cell_map)}セル")
            else:
                logger.info(f"[E-30] Stage D ヒント: なし")

            logger.info("[E-30] ===== AI プロンプト全文 =====")
            logger.info(prompt)
            logger.info("[E-30] ===== プロンプト終了 =====")

            logger.info("[E-30] Gemini API 呼び出し開始...")
            response = self.model.generate_content([
                prompt,
                {
                    'mime_type': 'image/png',
                    'data': image_data
                }
            ])
            logger.info("[E-30] Gemini API 呼び出し完了")

            raw_text = response.text
            logger.info(f"[E-30] レスポンス長: {len(raw_text)}文字")
            logger.info("[E-30] ===== AI レスポンス全文 =====")
            logger.info(raw_text)
            logger.info("[E-30] ===== レスポンス終了 =====")

            cells, n_rows, n_cols = self._parse_structure_response(raw_text, cell_map)

            logger.info(f"[E-30] パース結果: {n_rows}行 × {n_cols}列, {len(cells)}セル")

            # セル構造のサンプル出力
            if cells:
                logger.info("[E-30] セル構造サンプル（最初の5セル）:")
                for idx, cell in enumerate(cells[:5]):
                    logger.info(f"  ├─ R{cell.get('row')}C{cell.get('col')}: "
                              f"bbox=({cell.get('x0'):.3f},{cell.get('y0'):.3f})-({cell.get('x1'):.3f},{cell.get('y1'):.3f}), "
                              f"span={cell.get('rowspan')}x{cell.get('colspan')}")

            tokens_used = (len(prompt) + len(raw_text)) // 4

            logger.info("=" * 80)
            logger.info(f"[E-30] 構造抽出完了: {n_rows}行 × {n_cols}列, {len(cells)}セル")
            logger.info(f"  ├─ モデル: {self.model_name}")
            logger.info(f"  ├─ トークン: 約{tokens_used}")
            logger.info(f"  └─ route: E30_STRUCTURE_ONLY")
            logger.info("=" * 80)

            struct_result = {
                'success': True,
                'table_id': table_id,
                'cells': cells,
                'n_rows': n_rows,
                'n_cols': n_cols,
                'route': 'E30_STRUCTURE_ONLY',
                'model_used': self.model_name,
                'tokens_used': tokens_used
            }

            # ★チェーン: 次のステージ（E-31）を呼び出す
            if self.next_stage:
                logger.info("[E-30] → 次のステージ（E-31）を呼び出します")
                return self.next_stage.extract_cells(
                    image_path=image_path,
                    cells=cells,
                    struct_result=struct_result
                )

            return struct_result

        except Exception as e:
            logger.error(f"[E-30] 構造抽出エラー: {e}", exc_info=True)
            error_result = self._error_result(str(e), page_index, table_index)

            # ★チェーン: エラー時もE-31を呼ぶ
            if self.next_stage:
                logger.info("[E-30] → エラー後もE-31を呼び出します")
                return self.next_stage.extract_cells(
                    image_path=image_path,
                    cells=[],
                    struct_result=error_result
                )

            return error_result

    def _build_structure_prompt(self, cell_map: Optional[List[Dict]]) -> str:
        """構造とテキストを同時に取得するプロンプト"""
        hint = ""
        if cell_map:
            rows = max((c.get('row', 0) for c in cell_map), default=0) + 1
            cols = max((c.get('col', 0) for c in cell_map), default=0) + 1
            hint = f"\n参考情報（Stage D 線分解析）：推定 {rows}行 × {cols}列\n"

        return f"""あなたは表画像を解析する専門家です。
添付された表画像から、構造とテキストの両方をJSON形式で出力してください。
{hint}
【重要な指示】
- セルの「位置（bbox）」「行番号」「列番号」「結合情報」「テキスト内容」を出力
- 座標は表画像全体を [0.0, 1.0] に正規化してください（左上が(0,0)、右下が(1,1)）
- セル結合（rowspan/colspan）は必ず記録してください
- 各セルのテキスト内容を正確に読み取ってください

【出力形式（JSON のみ）】
```json
{{
  "n_rows": 行数,
  "n_cols": 列数,
  "cells": [
    {{"row": 0, "col": 0, "x0": 0.0, "y0": 0.0, "x1": 0.5, "y1": 0.25, "rowspan": 1, "colspan": 1, "text": "セルのテキスト"}},
    {{"row": 0, "col": 1, "x0": 0.5, "y0": 0.0, "x1": 1.0, "y1": 0.25, "rowspan": 1, "colspan": 2, "text": "別のセル"}},
    ...
  ]
}}
```

JSONのみ出力してください。説明文は不要です。"""

    def _parse_structure_response(
        self,
        raw_text: str,
        cell_map: Optional[List[Dict]]
    ) -> tuple:
        """レスポンスからセル構造を抽出。失敗時は Stage D の cell_map にフォールバック"""
        try:
            # ```json ... ``` ブロックを抽出
            if '```json' in raw_text:
                start = raw_text.find('```json') + 7
                end = raw_text.find('```', start)
                json_str = raw_text[start:end].strip()
            elif '```' in raw_text:
                start = raw_text.find('```') + 3
                end = raw_text.find('```', start)
                json_str = raw_text[start:end].strip()
            else:
                json_str = raw_text.strip()

            data = json.loads(json_str)
            cells = data.get('cells', [])
            n_rows = data.get('n_rows', 0)
            n_cols = data.get('n_cols', 0)

            # cells が空ならフォールバック
            if not cells and cell_map:
                return self._fallback_from_cell_map(cell_map)

            # n_rows/n_cols が 0 なら cells から計算
            if not n_rows:
                n_rows = max((c.get('row', 0) for c in cells), default=0) + 1
            if not n_cols:
                n_cols = max((c.get('col', 0) for c in cells), default=0) + 1

            return cells, n_rows, n_cols

        except Exception as e:
            logger.warning(f"[E-30] JSONパースエラー: {e} → Stage D フォールバック")
            if cell_map:
                return self._fallback_from_cell_map(cell_map)
            return [], 0, 0

    def _fallback_from_cell_map(self, cell_map: List[Dict]) -> tuple:
        """Stage D の cell_map を正規化座標に変換してフォールバック"""
        logger.info("[E-30] Stage D cell_map フォールバック使用")
        cells = []
        for c in cell_map:
            bbox_norm = c.get('bbox_normalized', [])
            if len(bbox_norm) == 4:
                cells.append({
                    'row': c.get('row', 0),
                    'col': c.get('col', 0),
                    'x0': bbox_norm[0],
                    'y0': bbox_norm[1],
                    'x1': bbox_norm[2],
                    'y1': bbox_norm[3],
                    'rowspan': 1,
                    'colspan': 1
                })

        n_rows = max((c['row'] for c in cells), default=0) + 1 if cells else 0
        n_cols = max((c['col'] for c in cells), default=0) + 1 if cells else 0
        return cells, n_rows, n_cols

    def _error_result(
        self,
        error_message: str,
        page_index: Optional[int] = None,
        table_index: Optional[int] = None
    ) -> Dict[str, Any]:
        """エラー結果を返す"""
        if page_index is not None and table_index is not None:
            table_id = f"E30_p{page_index:03d}_t{table_index:02d}"
        else:
            table_id = "E30_Unknown"
        return {
            'success': False,
            'error': error_message,
            'table_id': table_id,
            'cells': [],
            'n_rows': 0,
            'n_cols': 0,
            'route': 'E30_STRUCTURE_ONLY',
            'model_used': self.model_name,
            'tokens_used': 0
        }
