"""
A-5: Gatekeeper（通行許可）

目的:
- Stage A の判定結果（A-2/A-3/A-4）を材料に、
  「安全に通して良い」ものだけを ALLOW する。
- deny by default（許可リストに合致しないものは全部 BLOCK）
- Controller（B1）はこの結果を "最後の門" として強制する前提。

命名:
- 本来のGステージと混同しないため、A-5 として定義する。
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional
from loguru import logger


@dataclass(frozen=True)
class GatekeeperDecision:
    decision: str  # "ALLOW" | "BLOCK"
    allowed_processors: List[str]
    block_code: Optional[str]
    block_reason: str
    evidence: Dict[str, Any]
    policy_version: str


class A5Gatekeeper:
    """
    A-5 Gatekeeper v1（最小構成・事故ゼロ優先）

    v1 ではまず「WORD+FLOW+HIGH かつテキスト量十分」だけを通す。
    それ以外は全 BLOCK。許可範囲は運用しながら拡張する。
    """

    POLICY_VERSION = "A5.v1"

    # ---- v1: 緩和した閾値（画像多め・テキスト少なめのドキュメント対応）----
    MIN_AVG_WORDS_PER_PAGE = 20  # 80→20 緩和
    MIN_AVG_CHARS_PER_PAGE = 100  # 300→100 緩和
    MAX_AVG_IMAGES_PER_PAGE = 10  # 5→10 緩和
    MAX_AVG_X_STD = 200  # 80→200 緩和
    MIN_AVG_WORDS_PER_LINE = 1.0  # 5.0→1.0 緩和
    MAX_AVG_VECTORS_PER_PAGE = 500  # 300→500 緩和

    def evaluate(self, file_path: str | Path, a_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        通行許可を評価する

        Args:
            file_path: 対象ファイルパス
            a_result: Stage A の統合結果（a2_type/a4_layout 含む）

        Returns:
            GatekeeperDecision を dict 化したもの（decision, allowed_processors, ...）
        """
        file_path = Path(file_path)

        # Stage A 結果が無効
        if not a_result or not a_result.get("success"):
            d = GatekeeperDecision(
                decision="BLOCK",
                allowed_processors=[],
                block_code="A_RESULT_INVALID",
                block_reason="Stage A の結果が無効（success=false または a_result なし）",
                evidence={"a_result": bool(a_result)},
                policy_version=self.POLICY_VERSION,
            )
            logger.warning(f"[A-5] BLOCK: {d.block_reason}")
            return asdict(d)

        # PDF 以外は許可しない
        if file_path.suffix.lower() != ".pdf":
            d = GatekeeperDecision(
                decision="BLOCK",
                allowed_processors=[],
                block_code="EXT_NOT_ALLOWED",
                block_reason=f"拡張子が許可されていない: {file_path.suffix}",
                evidence={"suffix": file_path.suffix},
                policy_version=self.POLICY_VERSION,
            )
            logger.warning(f"[A-5] BLOCK: {d.block_reason}")
            return asdict(d)

        # ---- A-2/A-4 の正本を取得（互換キーにも対応）----
        a2 = a_result.get("a2_type") or {}
        origin_app = a2.get("origin_app") or a_result.get("origin_app") or a_result.get("document_type")
        confidence = a2.get("confidence") or a_result.get("confidence") or "NONE"

        a4 = a_result.get("a4_layout") or {}
        layout_profile = a4.get("layout_profile") or a_result.get("layout_profile") or "FLOW"
        layout_metrics = a4.get("layout_metrics") or a_result.get("layout_metrics") or {}

        # "絶対" の核：HIGH 以外は全部 BLOCK
        if confidence != "HIGH":
            d = GatekeeperDecision(
                decision="BLOCK",
                allowed_processors=[],
                block_code="LOW_CONFIDENCE",
                block_reason=f"confidence!=HIGH のため遮断（confidence={confidence}）",
                evidence={"origin_app": origin_app, "layout_profile": layout_profile, "confidence": confidence},
                policy_version=self.POLICY_VERSION,
            )
            logger.warning(f"[A-5] BLOCK: {d.block_reason}")
            return asdict(d)

        # v1 allowlist：WORD（FLOW/FIXED）、GOOGLE_DOCS（FLOW/FIXED）を通す
        allowed_combinations = [
            ("WORD", "FLOW"),
            ("WORD", "FIXED"),
            ("GOOGLE_DOCS", "FLOW"),
            ("GOOGLE_DOCS", "FIXED"),
        ]
        if (origin_app, layout_profile) not in allowed_combinations:
            d = GatekeeperDecision(
                decision="BLOCK",
                allowed_processors=[],
                block_code="NOT_ALLOWLISTED",
                block_reason=f"allowlist外（origin_app={origin_app}, layout_profile={layout_profile}）",
                evidence={"origin_app": origin_app, "layout_profile": layout_profile, "confidence": confidence},
                policy_version=self.POLICY_VERSION,
            )
            logger.warning(f"[A-5] BLOCK: {d.block_reason}")
            return asdict(d)

        # ---- 追加プローブ（pdfplumber）----
        probe = self._probe_pdf(file_path)

        # ---- Stage A メトリクスから閾値評価用の値を取得 ----
        avg_images = float(layout_metrics.get("avg_images_per_page", 0) or 0)
        avg_words = float(layout_metrics.get("avg_words_per_page", 0) or 0)
        avg_x_std = float(layout_metrics.get("avg_x_std", 0) or 0)
        avg_wpl = float(layout_metrics.get("avg_words_per_line", 0) or 0)

        evidence = {
            "origin_app": origin_app,
            "layout_profile": layout_profile,
            "confidence": confidence,
            "avg_images_per_page": avg_images,
            "avg_words_per_page": avg_words,
            "avg_x_std": avg_x_std,
            "avg_words_per_line": avg_wpl,
            **probe,
        }

        # ---- 閾値チェック（GOOGLE_DOCS はスキップ、WORD のみ）----
        failures: List[str] = []
        if origin_app == "WORD":
            if avg_words < self.MIN_AVG_WORDS_PER_PAGE:
                failures.append(f"avg_words_per_page<{self.MIN_AVG_WORDS_PER_PAGE} (actual={avg_words:.1f})")
            if probe["avg_chars_per_page"] < self.MIN_AVG_CHARS_PER_PAGE:
                failures.append(f"avg_chars_per_page<{self.MIN_AVG_CHARS_PER_PAGE} (actual={probe['avg_chars_per_page']:.1f})")
            if avg_images > self.MAX_AVG_IMAGES_PER_PAGE:
                failures.append(f"avg_images_per_page>{self.MAX_AVG_IMAGES_PER_PAGE} (actual={avg_images:.1f})")
            if avg_x_std > self.MAX_AVG_X_STD:
                failures.append(f"avg_x_std>{self.MAX_AVG_X_STD} (actual={avg_x_std:.1f})")
            if avg_wpl < self.MIN_AVG_WORDS_PER_LINE:
                failures.append(f"avg_words_per_line<{self.MIN_AVG_WORDS_PER_LINE} (actual={avg_wpl:.2f})")
            if probe["avg_vectors_per_page"] > self.MAX_AVG_VECTORS_PER_PAGE:
                failures.append(f"avg_vectors_per_page>{self.MAX_AVG_VECTORS_PER_PAGE} (actual={probe['avg_vectors_per_page']:.1f})")

        if failures:
            d = GatekeeperDecision(
                decision="BLOCK",
                allowed_processors=[],
                block_code="GATE_CONDITION_FAILED",
                block_reason="; ".join(failures),
                evidence=evidence,
                policy_version=self.POLICY_VERSION,
            )
            logger.warning(f"[A-5] BLOCK: {d.block_reason}")
            return asdict(d)

        # ---- ALLOW（プロセッサ選択）----
        if origin_app == "GOOGLE_DOCS":
            allowed_procs = ["B11_GOOGLE_DOCS"]
            reason_suffix = f"GOOGLE_DOCS+{layout_profile}"
        else:  # WORD
            if layout_profile == "FIXED":
                allowed_procs = ["B30_DTP"]
                reason_suffix = "WORD+FIXED"
            else:
                allowed_procs = ["B3_PDF_WORD"]
                reason_suffix = "WORD+FLOW"

        d = GatekeeperDecision(
            decision="ALLOW",
            allowed_processors=allowed_procs,
            block_code=None,
            block_reason=f"allowlist条件を満たしたため許可（{reason_suffix} + 安全閾値クリア）",
            evidence=evidence,
            policy_version=self.POLICY_VERSION,
        )
        logger.info(f"[A-5] ALLOW: {allowed_procs} (chars={probe['avg_chars_per_page']:.0f}/page, vectors={probe['avg_vectors_per_page']:.0f}/page)")
        return asdict(d)

    def _probe_pdf(self, file_path: Path) -> Dict[str, Any]:
        """
        pdfplumber で軽量プローブ:
        - avg_chars_per_page: テキスト選択可能な文字数
        - avg_vectors_per_page: 罫線/枠/図形の量（lines+rects+curves）

        pdfplumber が無い場合は安全側（BLOCK になりやすい値）を返す。
        """
        try:
            import pdfplumber
        except ImportError:
            logger.warning("[A-5] pdfplumber が利用できません → 安全側の値で評価")
            return {"probe_ok": False, "avg_chars_per_page": 0.0, "avg_vectors_per_page": 10_000.0}

        chars_total = 0
        vectors_total = 0
        pages = 0

        try:
            with pdfplumber.open(str(file_path)) as pdf:
                pages = len(pdf.pages) or 0
                for p in pdf.pages:
                    chars_total += len(getattr(p, "chars", []) or [])
                    vectors_total += (
                        len(getattr(p, "lines", []) or [])
                        + len(getattr(p, "rects", []) or [])
                        + len(getattr(p, "curves", []) or [])
                    )
        except Exception as e:
            logger.warning(f"[A-5] プローブ失敗: {e} → 安全側の値で評価")
            return {"probe_ok": False, "avg_chars_per_page": 0.0, "avg_vectors_per_page": 10_000.0}

        denom = pages if pages > 0 else 1
        return {
            "probe_ok": True,
            "avg_chars_per_page": chars_total / denom,
            "avg_vectors_per_page": vectors_total / denom,
        }
