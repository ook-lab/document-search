"""
E-30: Table Structure Extractor（表構造専用 - Gemini 2.5 Flash）

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
        model_name: str = "gemini-2.5-flash"
    ):
        self.model_name = model_name
        self.api_key = api_key

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

        logger.info(f"[E-30] 構造抽出開始: {image_path.name} table_id={table_id}")

        try:
            with open(image_path, 'rb') as f:
                image_data = f.read()

            prompt = self._build_structure_prompt(cell_map)

            logger.info(f"[E-30] モデル: {self.model_name}")
            logger.info(f"[E-30] プロンプト長: {len(prompt)}文字")

            response = self.model.generate_content([
                prompt,
                {
                    'mime_type': 'image/png',
                    'data': image_data
                }
            ])

            raw_text = response.text
            logger.info(f"[E-30] レスポンス長: {len(raw_text)}文字")

            cells, n_rows, n_cols = self._parse_structure_response(raw_text, cell_map)

            tokens_used = (len(prompt) + len(raw_text)) // 4

            logger.info(f"[E-30] 構造抽出完了: {n_rows}行 × {n_cols}列, {len(cells)}セル")

            return {
                'success': True,
                'table_id': table_id,
                'cells': cells,
                'n_rows': n_rows,
                'n_cols': n_cols,
                'route': 'E30_STRUCTURE_ONLY',
                'model_used': self.model_name,
                'tokens_used': tokens_used
            }

        except Exception as e:
            logger.error(f"[E-30] 構造抽出エラー: {e}", exc_info=True)
            return self._error_result(str(e), page_index, table_index)

    def _build_structure_prompt(self, cell_map: Optional[List[Dict]]) -> str:
        """構造専用プロンプトを構築（セル値は要求しない）"""
        hint = ""
        if cell_map:
            rows = max((c.get('row', 0) for c in cell_map), default=0) + 1
            cols = max((c.get('col', 0) for c in cell_map), default=0) + 1
            hint = f"\n参考情報（Stage D 線分解析）：推定 {rows}行 × {cols}列\n"

        return f"""あなたは表画像の構造を解析する専門家です。
添付された表画像を分析し、セル構造のみをJSON形式で出力してください。
{hint}
【重要な指示】
- セルの「値・内容・テキスト」は一切出力しないでください
- セルの「位置（bbox）」「行番号」「列番号」「結合情報」のみを出力します
- 座標は表画像全体を [0.0, 1.0] に正規化してください（左上が(0,0)、右下が(1,1)）
- セル結合（rowspan/colspan）は必ず記録してください

【出力形式（JSON のみ）】
```json
{{
  "n_rows": 行数,
  "n_cols": 列数,
  "cells": [
    {{"row": 0, "col": 0, "x0": 0.0, "y0": 0.0, "x1": 0.5, "y1": 0.25, "rowspan": 1, "colspan": 1}},
    {{"row": 0, "col": 1, "x0": 0.5, "y0": 0.0, "x1": 1.0, "y1": 0.25, "rowspan": 1, "colspan": 2}},
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
