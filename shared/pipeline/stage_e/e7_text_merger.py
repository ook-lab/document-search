"""
E-7: Vision接着・修正（Glue and Repair）

Vision APIの座標を維持しつつ、画像を見ながら：
1. 分割されたテキストを結合（接着）
2. 誤読パーツを修正（洗浄）

入力: vision_tokens (E6出力) + image_path
出力: merged_tokens = [{text, bbox}, ...]
モデル: gemini-2.5-flash

【Ver 9.1】Chain Merge対応
- AIが重なり合う提案をした場合、自動的に連鎖結合
- 例: [t1,t2] + [t2,t3] → [t1,t2,t3]
"""

import json
import base64
from pathlib import Path
from typing import Dict, Any, List, Optional, Set
from loguru import logger


class E7TextMerger:
    """E-7: Vision接着・修正（Glue and Repair）"""

    MODEL = "gemini-2.5-flash"

    def __init__(self, llm_client):
        self.llm_client = llm_client

    def merge(
        self,
        vision_tokens: List[Dict[str, Any]],
        image_path: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        画像とトークンを照合して結合・修正

        Args:
            vision_tokens: E6出力（座標付きトークン）
            image_path: ページ画像パス（Vision用）
        """
        if not vision_tokens:
            return []

        logger.info(f"[E-7] Vision接着・修正開始: {len(vision_tokens)}トークン")

        # 入力データ整理
        input_data = [
            {"id": f"t{i}", "text": t["text"], "bbox": t["bbox"]}
            for i, t in enumerate(vision_tokens)
        ]

        # Vision有無で分岐
        if image_path and Path(image_path).exists():
            merges = self._call_vision_merge(input_data, image_path)
        else:
            logger.warning("[E-7] 画像なし → テキストのみで結合")
            merges = self._call_text_merge(input_data)

        # 適用
        if not merges:
            logger.info("[E-7] 結合対象なし")
            merged_tokens = vision_tokens.copy()
        else:
            logger.info(f"[E-7] AI提案: {len(merges)}グループ")
            # Chain Merge: 重なり合うグループを連鎖結合
            chained_merges = self._chain_merge_groups(merges)
            logger.info(f"[E-7] Chain Merge後: {len(chained_merges)}グループ")
            merged_tokens = self._apply_merges(vision_tokens, input_data, chained_merges)

        self._log_result(merged_tokens)
        return merged_tokens

    def _call_vision_merge(
        self,
        input_data: List[Dict],
        image_path: str
    ) -> List[Dict]:
        """Vision付きで結合・修正指示を取得"""
        try:
            with open(image_path, "rb") as f:
                image_base64 = base64.b64encode(f.read()).decode("utf-8")
        except Exception as e:
            logger.error(f"[E-7] 画像読み込み失敗: {e}")
            return []

        # 強化されたプロンプト
        prompt = """Act as a 'Glue and Repair' agent for Japanese document OCR.
Look at the attached image and the fragmented OCR token list.

YOUR MISSION:
1. MERGE: Find ALL token IDs that form a single word, school name, sentence, or title.
   - IMPORTANT: Include ALL fragments in ONE group. Do NOT split into pairs.
   - Example: If "都立大学等特力推薦" is split into ["都","立","大","学","等","特","力","推","薦"],
     return ONE group with ALL 9 IDs, not multiple pairs.

2. REPAIR: If tokens contain misread components, provide the CORRECTED text.
   - Example: 'ㄕㄡ'+'卜' should become 'テスト'

3. CONTEXT: Consider both vertical and horizontal reading flows.

4. BOUNDARIES: Never merge across different table columns or sections.

5. COMPREHENSIVE: Find ALL mergeable sequences. Do not leave fragments behind.

CRITICAL RULES:
- Each token ID can appear in ONLY ONE group (no overlapping)
- Prefer LONGER groups over multiple short groups
- If unsure, include more tokens rather than fewer

Return ONLY JSON:
{
  "merges": [
    { "ids": ["t247", "t248", "t249", "t250", "t251", "t252", "t253", "t254", "t255"], "text": "都立大学等特力推薦" },
    { "ids": ["t1", "t2", "t3", "t4", "t5"], "text": "筑波大附属駒場" },
    { "ids": ["t100", "t101", "t102", "t103"], "text": "※この一覧表は" }
  ]
}
If no merges needed: {"merges": []}"""

        try:
            full_prompt = prompt + "\n\nInput Tokens:\n" + json.dumps(input_data, ensure_ascii=False)
            response = self.llm_client.generate_with_vision(
                prompt=full_prompt,
                image_path=image_path,
                model=self.MODEL,
                max_tokens=8192,
                temperature=0.0,
                response_format="json"
            )

            import json_repair
            parsed = json_repair.loads(response or "{}")
            return parsed.get("merges", [])

        except Exception as e:
            logger.warning(f"[E-7] Vision結合エラー: {e}")
            return []

    def _call_text_merge(self, input_data: List[Dict]) -> List[Dict]:
        """テキストのみで結合指示を取得（フォールバック）"""
        prompt = """You are a text reconstructor for Japanese documents.
Merge split text blocks into semantic units.

RULES:
1. Merge ALL blocks that form one entity (school name, sentence, note).
2. Include ALL fragments in ONE group - do not split into pairs.
3. If OCR misread characters, provide corrected text.
4. Each ID can only appear in ONE group.

Return JSON:
{
  "merges": [
    { "ids": ["t1", "t2", "t3", "t4", "t5"], "text": "筑波大附駒場" }
  ]
}"""

        try:
            full_prompt = prompt + "\n\nInput:\n" + json.dumps(input_data, ensure_ascii=False)
            response = self.llm_client.generate(
                prompt=full_prompt,
                model=self.MODEL,
                max_tokens=8192,
                temperature=0.0
            )

            import json_repair
            parsed = json_repair.loads(response or "{}")
            return parsed.get("merges", [])

        except Exception as e:
            logger.warning(f"[E-7] テキスト結合エラー: {e}")
            return []

    def _chain_merge_groups(self, merges: List[Dict]) -> List[Dict]:
        """
        重なり合うグループを連鎖結合（Chain Merge）

        例: [t1,t2] + [t2,t3] + [t3,t4] → [t1,t2,t3,t4]
        """
        if not merges:
            return []

        # 各グループのIDセットを作成
        groups = []
        for item in merges:
            if isinstance(item, dict):
                ids = set(item.get("ids", []))
                text = item.get("text", "")
            elif isinstance(item, list):
                ids = set(item)
                text = ""
            else:
                continue
            if ids:
                groups.append({"ids": ids, "text": text})

        if not groups:
            return []

        # Union-Find的に連鎖結合
        changed = True
        while changed:
            changed = False
            new_groups = []
            used = [False] * len(groups)

            for i, g1 in enumerate(groups):
                if used[i]:
                    continue

                merged_ids = g1["ids"].copy()
                merged_text = g1["text"]

                for j, g2 in enumerate(groups):
                    if i == j or used[j]:
                        continue

                    # 重なりがあれば結合
                    if merged_ids & g2["ids"]:
                        merged_ids |= g2["ids"]
                        # テキストは長い方を採用（または空でない方）
                        if not merged_text and g2["text"]:
                            merged_text = g2["text"]
                        elif g2["text"] and len(g2["text"]) > len(merged_text):
                            merged_text = g2["text"]
                        used[j] = True
                        changed = True

                used[i] = True
                new_groups.append({"ids": merged_ids, "text": merged_text})

            groups = new_groups

        # IDリストをソートして返す
        result = []
        for g in groups:
            sorted_ids = sorted(g["ids"], key=lambda x: int(x[1:]) if x[1:].isdigit() else 0)
            result.append({
                "ids": sorted_ids,
                "text": g["text"]
            })

        # 結合数が変わった場合はログ出力
        original_count = len(merges)
        if len(result) != original_count:
            logger.info(f"[E-7] Chain Merge: {original_count}グループ → {len(result)}グループに統合")

        return result

    def _apply_merges(
        self,
        vision_tokens: List[Dict],
        input_data: List[Dict],
        merges: List[Dict]
    ) -> List[Dict]:
        """結合・修正を適用"""
        id_to_idx = {d["id"]: i for i, d in enumerate(input_data)}
        merged_indices = set()
        result = []

        for item in merges:
            # 新形式: {"ids": [...], "text": "..."}
            if isinstance(item, dict):
                group_ids = item.get("ids", [])
                corrected_text = item.get("text", "")
            # 旧形式: ["id1", "id2"]
            elif isinstance(item, list):
                group_ids = item
                corrected_text = ""
            else:
                continue

            indices = [id_to_idx[gid] for gid in group_ids if gid in id_to_idx]

            # 重複使用防止（Chain Merge後は基本的に重複しないはず）
            if any(idx in merged_indices for idx in indices):
                logger.debug(f"[E-7] スキップ：重複 {group_ids}")
                continue
            if len(indices) < 2:
                continue

            tokens_to_merge = [vision_tokens[i] for i in indices]

            # ジオメトリガード（緩和版：連続性をチェック）
            if not self._is_valid_merge_chain(tokens_to_merge):
                logger.debug(f"[E-7] スキップ：ジオメトリ不正 {group_ids}")
                continue

            # 座標順ソート（左上→右下）
            tokens_to_merge.sort(key=lambda t: (t['bbox'][1], t['bbox'][0]))

            # BBox結合（Vision座標を維持）
            all_bboxes = [t.get("bbox", [0, 0, 0, 0]) for t in tokens_to_merge]
            combined_bbox = [
                min(b[0] for b in all_bboxes),
                min(b[1] for b in all_bboxes),
                max(b[2] for b in all_bboxes),
                max(b[3] for b in all_bboxes)
            ]

            # AIが修正テキストを提供していれば採用、なければ単純結合
            if corrected_text:
                final_text = corrected_text
            else:
                final_text = "".join([t.get("text", "") for t in tokens_to_merge])

            result.append({
                "text": final_text,
                "bbox": combined_bbox,
                "_merged_from": group_ids
            })
            merged_indices.update(indices)

            logger.info(f"[E-7] 結合: {group_ids} -> '{final_text}'")

        # 未結合トークンを追加
        for i, token in enumerate(vision_tokens):
            if i not in merged_indices:
                result.append(token)

        # 読み順ソート（上→下、左→右）
        result.sort(key=lambda t: (t['bbox'][1], t['bbox'][0]))
        return result

    def _is_valid_merge_chain(self, tokens: List[Dict]) -> bool:
        """
        ジオメトリガード（最大高基準・緩和版）

        数字や記号による平均高の低下を防ぎ、広い隙間の結合を許容する
        全判定プロセスをログ出力
        """
        if len(tokens) < 2:
            return True

        # テキストプレビュー
        text_preview = "".join([t.get('text', '') for t in tokens])[:50]

        all_bboxes = [t.get('bbox', [0, 0, 0, 0]) for t in tokens]

        # --- 基準の算出 ---
        # 平均ではなく、グループ内で「最も背の高い文字」を基準にする
        max_h = max(b[3] - b[1] for b in all_bboxes)
        ref_h = max(max_h, 12)  # 最低でも12pxを基準とする

        # 全体の広がり
        total_width = max(b[2] for b in all_bboxes) - min(b[0] for b in all_bboxes)
        total_height = max(b[3] for b in all_bboxes) - min(b[1] for b in all_bboxes)

        # 縦書き判定
        is_vertical = total_height > total_width * 2

        if is_vertical:
            # 縦書き: X中心線のズレをチェック
            x_centers = [(b[0] + b[2]) / 2 for b in all_bboxes]
            x_spread = max(x_centers) - min(x_centers)
            x_limit = ref_h * 2.0
            if x_spread > x_limit:
                logger.warning(f"[E-7] 結合却下(縦書きXズレ): '{text_preview}'")
                logger.warning(f"[E-7]   x_spread={x_spread:.1f} > 許容値={x_limit:.1f} (ref_h={ref_h:.1f})")
                return False
        else:
            # 横書き: Y中心線のズレをチェック
            y_centers = [(b[1] + b[3]) / 2 for b in all_bboxes]
            y_spread = max(y_centers) - min(y_centers)
            y_limit = ref_h * 1.5
            if y_spread > y_limit:
                logger.warning(f"[E-7] 結合却下(横書きYズレ): '{text_preview}'")
                logger.warning(f"[E-7]   y_spread={y_spread:.1f} > 許容値={y_limit:.1f} (ref_h={ref_h:.1f})")
                return False

        # --- 隣接ペアの隙間チェック ---
        # 読み順（左から右）にソートして隙間を測る
        sorted_tokens = sorted(tokens, key=lambda t: (t['bbox'][0]))
        large_gaps = 0
        gap_details = []
        for i in range(len(sorted_tokens) - 1):
            b1 = sorted_tokens[i].get('bbox', [0, 0, 0, 0])
            b2 = sorted_tokens[i + 1].get('bbox', [0, 0, 0, 0])
            t1_text = sorted_tokens[i].get('text', '')
            t2_text = sorted_tokens[i + 1].get('text', '')

            h_gap = b2[0] - b1[2]  # 横の隙間
            v_gap = abs(b2[1] - b1[1])  # 上端のズレ

            # 隙間制限を ref_h の 10倍 まで大幅に緩和
            h_gap_limit = ref_h * 10.0
            v_gap_limit = ref_h * 2.0

            if h_gap > h_gap_limit or v_gap > v_gap_limit:
                large_gaps += 1
                gap_details.append(f"'{t1_text}'→'{t2_text}' h_gap={h_gap:.1f}, v_gap={v_gap:.1f}")

        # 3文字中1文字が離れていても (33%) 許容できるよう 60% に設定
        gap_ratio = large_gaps / len(tokens) if len(tokens) > 0 else 0
        if gap_ratio > 0.6:
            logger.warning(f"[E-7] 結合却下(隙間過多): '{text_preview}'")
            logger.warning(f"[E-7]   large_gaps={large_gaps}/{len(tokens)} ({gap_ratio:.1%}) > 60%")
            for detail in gap_details[:5]:
                logger.warning(f"[E-7]   - {detail}")
            return False

        logger.info(f"[E-7] ジオメトリOK: '{text_preview}' (ref_h={ref_h:.1f}, gaps={large_gaps})")
        return True

    def _log_result(self, tokens: List[Dict]):
        """結果ログ"""
        logger.info("[E-7] ===== 生成物ログ開始 =====")
        logger.info(f"[E-7] トークン数: {len(tokens)}")
        merged_count = 0
        for i, t in enumerate(tokens[:20]):
            merged = t.get('_merged_from', [])
            if merged:
                merged_count += 1
                logger.info(f"[E-7]   [{i+1}] merged={merged} -> '{t['text']}'")
            else:
                logger.info(f"[E-7]   [{i+1}] '{t.get('text', '')}'")
        if len(tokens) > 20:
            logger.info(f"[E-7]   ... 他{len(tokens)-20}件")
        logger.info(f"[E-7] 結合済みトークン: {merged_count}件")
        logger.info("[E-7] ===== 生成物ログ終了 =====")
