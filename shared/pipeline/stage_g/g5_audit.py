"""
G5: Audit（検算・品質・確定）

【Ver 9.0】I/O契約（固定）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
入力:
  - assembled_payload: G4の出力

出力（唯一の正本）: scrubbed_data
  - tagged_texts: 確定済み
  - anchors: 確定済み
  - quality_detail: 品質指標
  - anomaly_report: 未解決一覧
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ルール:
- 値は変えない ← read-only
- 失敗は"未解決として確定"（戻らない）
- quality_detail / anomaly_report を確定
- ここがVer9の唯一出口
"""

import time
from typing import Dict, Any, List, Optional
from loguru import logger

from ..constants import STAGE_F_OUTPUT_SCHEMA_VERSION


class G5Audit:
    """G5: Audit - 検算・品質・確定（唯一の正本出口）"""

    def __init__(self):
        pass

    def audit(
        self,
        assembled_payload: Dict[str, Any],
        post_body: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        正本の品質を確定し、未解決を明示する

        Returns:
            scrubbed_data（Ver9の唯一出口）
        """
        g5_start = time.time()

        tagged_texts = assembled_payload.get('tagged_texts', [])
        anchors = assembled_payload.get('anchors', [])

        logger.info(f"[G5] Audit開始: texts={len(tagged_texts)}, anchors={len(anchors)}")

        # 品質検算
        quality_detail = self._calculate_quality(tagged_texts, anchors)

        # 異常検出（未解決として確定）
        anomaly_report = self._detect_anomalies(tagged_texts, anchors)

        # 未解決があっても戻らない（確定する）
        if anomaly_report:
            logger.warning(f"[G5] 未解決あり: {len(anomaly_report)}件（確定として処理）")

        elapsed = time.time() - g5_start

        # 最終payload構築（scrubbed_data = Ver9唯一の正本）
        scrubbed_data = {
            "schema_version": STAGE_F_OUTPUT_SCHEMA_VERSION,
            "post_body": post_body or {},
            "path_a_result": {
                "tagged_texts": tagged_texts,
                "extracted_texts": assembled_payload.get('extracted_texts', []),
                "full_text_ordered": assembled_payload.get('full_text_ordered', ''),
                "x_headers": assembled_payload.get('x_headers', []),
                "y_headers": assembled_payload.get('y_headers', []),
                "tables": assembled_payload.get('tables', []),
                "text_source": assembled_payload.get('text_source'),
                "text_source_by_page": assembled_payload.get('text_source_by_page', {}),
            },
            "anchors": anchors,
            "quality_detail": quality_detail,
            "anomaly_report": anomaly_report,
            "change_log": assembled_payload.get('change_log', []),
            "metadata": {
                **(metadata or {}),
                **assembled_payload.get('stats', {}),
                "g5_elapsed": elapsed,
                "anomaly_count": len(anomaly_report),
                "quality_score": quality_detail.get('overall', 0),
            },
            "warnings": []
        }

        # 異常を警告に追加
        for anomaly in anomaly_report:
            scrubbed_data["warnings"].append(
                f"ANOMALY_{anomaly['type']}: {anomaly['description']}"
            )

        logger.info(f"[G5] Audit完了: quality={quality_detail.get('overall', 0):.2f}, anomalies={len(anomaly_report)}")

        return scrubbed_data

    def _calculate_quality(
        self,
        tagged_texts: List[Dict],
        anchors: List[Dict]
    ) -> Dict[str, float]:
        """品質指標を計算"""
        total = len(tagged_texts)
        if total == 0:
            return {'overall': 0.0, 'completeness': 0.0, 'consistency': 0.0, 'coverage': 0.0}

        # completeness: 空でないセルの割合
        non_empty = sum(1 for t in tagged_texts if t.get('text', '').strip())
        completeness = non_empty / total

        # consistency: x/y_headerが付いているセルの割合
        with_headers = sum(1 for t in tagged_texts
                          if t.get('type') == 'cell' and (t.get('x_header') or t.get('y_header')))
        cells = sum(1 for t in tagged_texts if t.get('type') == 'cell')
        consistency = with_headers / cells if cells > 0 else 1.0

        # coverage: scrub成功率
        scrubbed = sum(1 for t in tagged_texts if t.get('_scrubbed'))
        coverage = scrubbed / total if total > 0 else 0.0

        overall = completeness * 0.4 + consistency * 0.4 + coverage * 0.2

        return {
            'overall': round(overall, 3),
            'completeness': round(completeness, 3),
            'consistency': round(consistency, 3),
            'coverage': round(coverage, 3),
        }

    def _detect_anomalies(
        self,
        tagged_texts: List[Dict],
        anchors: List[Dict]
    ) -> List[Dict]:
        """異常検出（未解決として確定）"""
        anomalies = []

        # 空セル検出
        empty_cells = [t for t in tagged_texts if t.get('type') == 'cell' and not t.get('text', '').strip()]
        if empty_cells:
            anomalies.append({
                'type': 'EMPTY_CELLS',
                'description': f'{len(empty_cells)}個の空セル',
                'items': [c.get('id') for c in empty_cells[:10]],
                'count': len(empty_cells),
                'severity': 'warning'
            })

        # ヘッダーなしセル検出
        no_header = [t for t in tagged_texts
                     if t.get('type') == 'cell' and not t.get('x_header') and not t.get('y_header')]
        if no_header:
            anomalies.append({
                'type': 'NO_HEADER_CELLS',
                'description': f'{len(no_header)}個のヘッダーなしセル',
                'items': [c.get('id') for c in no_header[:10]],
                'count': len(no_header),
                'severity': 'warning'
            })

        # 未割当トークン（untagged）
        untagged = [t for t in tagged_texts if t.get('type') == 'untagged']
        if len(untagged) > len(tagged_texts) * 0.5:
            anomalies.append({
                'type': 'HIGH_UNTAGGED_RATIO',
                'description': f'未割当が50%超（{len(untagged)}/{len(tagged_texts)}）',
                'count': len(untagged),
                'severity': 'error'
            })

        # アンカーなし
        if not anchors:
            anomalies.append({
                'type': 'NO_ANCHORS',
                'description': 'アンカーが生成されていない',
                'count': 0,
                'severity': 'error'
            })

        return anomalies