"""
E-7P: Pythonパッチ適用（Patch Applier）

E-7Lが検出した差分（merge_instructions）を元トークンに適用する。
LLM不要・完全決定論。
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
入力:
  - vision_tokens: E6出力（座標付きトークン、マスターリスト）
  - merge_instructions: E-7Lの出力 [{"ids": [...], "text": "..."}, ...]

出力:
  - merged_tokens: パッチ適用済みトークンリスト

処理:
  1. Chain Merge: 重なり合うグループを連鎖結合
  2. ジオメトリガード: 空間的妥当性を検証
  3. 物理結合: bbox合成 + テキスト連結
  4. 不変トークンの自動補完: AIが言及しなかったトークンはそのまま残す
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from typing import Dict, Any, List
from loguru import logger


class E7PPatchApplier:
    """E-7P: Pythonパッチ適用 - LLM不要・決定論"""

    def __init__(self):
        pass

    def apply(
        self,
        vision_tokens: List[Dict[str, Any]],
        merge_instructions: List[Dict]
    ) -> List[Dict[str, Any]]:
        """
        差分指示を元トークンに適用する

        Args:
            vision_tokens: E6出力（マスターリスト）
            merge_instructions: E-7Lの出力

        Returns:
            merged_tokens: パッチ適用済みトークンリスト
        """
        if not vision_tokens:
            return []

        logger.info(f"[E-7P] パッチ適用開始: {len(vision_tokens)}トークン, {len(merge_instructions)}指示")

        if not merge_instructions:
            logger.info("[E-7P] 差分指示なし → 元トークンをそのまま返却")
            return vision_tokens.copy()

        # 1. Chain Merge: 重なり合うグループを連鎖結合
        chained = self._chain_merge_groups(merge_instructions)
        logger.info(f"[E-7P] Chain Merge: {len(merge_instructions)}指示 → {len(chained)}グループ")

        # 2. ID→インデックスマッピング構築
        id_to_idx = {f"t{i}": i for i in range(len(vision_tokens))}

        # 3. パッチ適用
        merged_indices = set()
        result = []

        for item in chained:
            group_ids = item.get("ids", [])
            corrected_text = item.get("text", "")

            # IDからインデックスへ変換（存在しないIDはスキップ）
            indices = [id_to_idx[gid] for gid in group_ids if gid in id_to_idx]

            # 重複使用防止
            if any(idx in merged_indices for idx in indices):
                logger.debug(f"[E-7P] スキップ(重複): {group_ids}")
                continue
            if len(indices) < 2:
                continue

            tokens_to_merge = [vision_tokens[i] for i in indices]

            # 4. ジオメトリガード
            if not self._is_valid_merge_chain(tokens_to_merge):
                logger.debug(f"[E-7P] スキップ(ジオメトリ不正): {group_ids}")
                continue

            # 5. 物理結合
            # 座標順ソート（左上→右下）
            tokens_to_merge.sort(key=lambda t: (t['bbox'][1], t['bbox'][0]))

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

            logger.info(f"[E-7P] 結合: {group_ids} -> '{final_text}'")

        # 6. 不変トークンの自動補完（AIが言及しなかった＝修正不要）
        for i, token in enumerate(vision_tokens):
            if i not in merged_indices:
                result.append(token)

        # 読み順ソート（上→下、左→右）
        result.sort(key=lambda t: (t['bbox'][1], t['bbox'][0]))

        self._log_result(result)
        return result

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

                    if merged_ids & g2["ids"]:
                        merged_ids |= g2["ids"]
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

        original_count = len(merges)
        if len(result) != original_count:
            logger.info(f"[E-7P] Chain Merge: {original_count}グループ → {len(result)}グループに統合")

        return result

    def _is_valid_merge_chain(self, tokens: List[Dict]) -> bool:
        """
        ジオメトリガード（最大高基準・緩和版）

        数字や記号による平均高の低下を防ぎ、広い隙間の結合を許容する
        """
        if len(tokens) < 2:
            return True

        text_preview = "".join([t.get('text', '') for t in tokens])[:50]
        all_bboxes = [t.get('bbox', [0, 0, 0, 0]) for t in tokens]

        # 基準: グループ内で最も背の高い文字
        max_h = max(b[3] - b[1] for b in all_bboxes)
        ref_h = max(max_h, 12)

        total_width = max(b[2] for b in all_bboxes) - min(b[0] for b in all_bboxes)
        total_height = max(b[3] for b in all_bboxes) - min(b[1] for b in all_bboxes)

        is_vertical = total_height > total_width * 2

        if is_vertical:
            x_centers = [(b[0] + b[2]) / 2 for b in all_bboxes]
            x_spread = max(x_centers) - min(x_centers)
            x_limit = ref_h * 2.0
            if x_spread > x_limit:
                logger.warning(f"[E-7P] 結合却下(縦書きXズレ): '{text_preview}'")
                return False
        else:
            y_centers = [(b[1] + b[3]) / 2 for b in all_bboxes]
            y_spread = max(y_centers) - min(y_centers)
            y_limit = ref_h * 1.5
            if y_spread > y_limit:
                logger.warning(f"[E-7P] 結合却下(横書きYズレ): '{text_preview}'")
                return False

        # 隣接ペアの隙間チェック
        sorted_tokens = sorted(tokens, key=lambda t: (t['bbox'][0]))
        large_gaps = 0
        for i in range(len(sorted_tokens) - 1):
            b1 = sorted_tokens[i].get('bbox', [0, 0, 0, 0])
            b2 = sorted_tokens[i + 1].get('bbox', [0, 0, 0, 0])

            h_gap = b2[0] - b1[2]
            v_gap = abs(b2[1] - b1[1])

            h_gap_limit = ref_h * 10.0
            v_gap_limit = ref_h * 2.0

            if h_gap > h_gap_limit or v_gap > v_gap_limit:
                large_gaps += 1

        gap_ratio = large_gaps / len(tokens) if len(tokens) > 0 else 0
        if gap_ratio > 0.6:
            logger.warning(f"[E-7P] 結合却下(隙間過多): '{text_preview}' gaps={large_gaps}/{len(tokens)}")
            return False

        return True

    def _log_result(self, tokens: List[Dict]):
        """結果ログ"""
        logger.info("[E-7P] ===== 生成物ログ開始 =====")
        logger.info(f"[E-7P] トークン数: {len(tokens)}")
        merged_count = 0
        for i, t in enumerate(tokens[:20]):
            merged = t.get('_merged_from', [])
            if merged:
                merged_count += 1
                logger.info(f"[E-7P]   [{i+1}] merged={merged} -> '{t['text']}'")
            else:
                logger.info(f"[E-7P]   [{i+1}] '{t.get('text', '')}'")
        if len(tokens) > 20:
            logger.info(f"[E-7P]   ... 他{len(tokens)-20}件")
        logger.info(f"[E-7P] 結合済みトークン: {merged_count}件")
        logger.info("[E-7P] ===== 生成物ログ終了 =====")
