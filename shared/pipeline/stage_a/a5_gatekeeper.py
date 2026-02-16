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

        logger.info("[A-5 Gatekeeper] 通行許可評価開始")
        logger.info(f"  ├─ ファイル: {file_path.name}")
        logger.info(f"  └─ ポリシーバージョン: {self.POLICY_VERSION}")

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
            logger.warning(f"[A-5 Gatekeeper] ✗ BLOCK: {d.block_reason}")
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
            logger.warning(f"[A-5 Gatekeeper] ✗ BLOCK: {d.block_reason}")
            return asdict(d)

        # ---- A-2/A-4 の正本を取得（互換キーにも対応）----
        a2 = a_result.get("a2_type") or {}
        origin_app = a2.get("origin_app") or a_result.get("origin_app") or a_result.get("document_type")
        confidence = a2.get("confidence") or a_result.get("confidence") or "NONE"

        a4 = a_result.get("a4_layout") or {}
        layout_profile = a4.get("layout_profile") or a_result.get("layout_profile") or "FLOW"
        layout_metrics = a4.get("layout_metrics") or a_result.get("layout_metrics") or {}

        logger.info("[A-5 Gatekeeper] Stage A 結果の確認:")
        logger.info(f"  ├─ origin_app: {origin_app}")
        logger.info(f"  ├─ confidence: {confidence}")
        logger.info(f"  └─ layout_profile: {layout_profile}")

        # "絶対" の核：HIGH 以外は全部 BLOCK
        logger.info("[A-5 Gatekeeper] 信頼度チェック:")
        logger.info(f"  ├─ 現在の信頼度: {confidence}")
        logger.info(f"  └─ 必須信頼度: HIGH")
        if confidence != "HIGH":
            d = GatekeeperDecision(
                decision="BLOCK",
                allowed_processors=[],
                block_code="LOW_CONFIDENCE",
                block_reason=f"confidence!=HIGH のため遮断（confidence={confidence}）",
                evidence={"origin_app": origin_app, "layout_profile": layout_profile, "confidence": confidence},
                policy_version=self.POLICY_VERSION,
            )
            logger.warning(f"[A-5 Gatekeeper] ✗ BLOCK: {d.block_reason}")
            return asdict(d)
        logger.info("  ✓ 信頼度チェック通過")

        # v1 allowlist：WORD（FLOW/FIXED）、GOOGLE_DOCS（FLOW/FIXED）を通す
        allowed_combinations = [
            ("WORD", "FLOW"),
            ("WORD", "FIXED"),
            ("GOOGLE_DOCS", "FLOW"),
            ("GOOGLE_DOCS", "FIXED"),
        ]
        logger.info("[A-5 Gatekeeper] Allowlistチェック:")
        logger.info(f"  ├─ 組み合わせ: ({origin_app}, {layout_profile})")
        logger.info(f"  └─ 許可リスト: {allowed_combinations}")
        if (origin_app, layout_profile) not in allowed_combinations:
            d = GatekeeperDecision(
                decision="BLOCK",
                allowed_processors=[],
                block_code="NOT_ALLOWLISTED",
                block_reason=f"allowlist外（origin_app={origin_app}, layout_profile={layout_profile}）",
                evidence={"origin_app": origin_app, "layout_profile": layout_profile, "confidence": confidence},
                policy_version=self.POLICY_VERSION,
            )
            logger.warning(f"[A-5 Gatekeeper] ✗ BLOCK: {d.block_reason}")
            return asdict(d)
        logger.info("  ✓ Allowlistチェック通過")

        # ---- 追加プローブ（pdfplumber）----
        logger.info("[A-5 Gatekeeper] PDFプローブ実行（文字数・ベクトル数）:")
        probe = self._probe_pdf(file_path)
        logger.info(f"  ├─ probe_ok: {probe['probe_ok']}")
        logger.info(f"  ├─ avg_chars_per_page: {probe['avg_chars_per_page']:.1f}")
        logger.info(f"  └─ avg_vectors_per_page: {probe['avg_vectors_per_page']:.1f}")

        # ---- Stage A メトリクスから閾値評価用の値を取得 ----
        avg_images = float(layout_metrics.get("avg_images_per_page", 0) or 0)
        avg_words = float(layout_metrics.get("avg_words_per_page", 0) or 0)
        avg_x_std = float(layout_metrics.get("avg_x_std", 0) or 0)
        avg_wpl = float(layout_metrics.get("avg_words_per_line", 0) or 0)

        logger.info("[A-5 Gatekeeper] Stage A メトリクス:")
        logger.info(f"  ├─ avg_images_per_page: {avg_images:.1f}")
        logger.info(f"  ├─ avg_words_per_page: {avg_words:.1f}")
        logger.info(f"  ├─ avg_x_std: {avg_x_std:.1f}")
        logger.info(f"  └─ avg_words_per_line: {avg_wpl:.2f}")

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
            logger.info("[A-5 Gatekeeper] 閾値チェック（WORD のみ）:")
            logger.info(f"  ├─ avg_words_per_page >= {self.MIN_AVG_WORDS_PER_PAGE}: {avg_words:.1f} {'✓' if avg_words >= self.MIN_AVG_WORDS_PER_PAGE else '✗'}")
            logger.info(f"  ├─ avg_chars_per_page >= {self.MIN_AVG_CHARS_PER_PAGE}: {probe['avg_chars_per_page']:.1f} {'✓' if probe['avg_chars_per_page'] >= self.MIN_AVG_CHARS_PER_PAGE else '✗'}")
            logger.info(f"  ├─ avg_images_per_page <= {self.MAX_AVG_IMAGES_PER_PAGE}: {avg_images:.1f} {'✓' if avg_images <= self.MAX_AVG_IMAGES_PER_PAGE else '✗'}")
            logger.info(f"  ├─ avg_x_std <= {self.MAX_AVG_X_STD}: {avg_x_std:.1f} {'✓' if avg_x_std <= self.MAX_AVG_X_STD else '✗'}")
            logger.info(f"  ├─ avg_words_per_line >= {self.MIN_AVG_WORDS_PER_LINE}: {avg_wpl:.2f} {'✓' if avg_wpl >= self.MIN_AVG_WORDS_PER_LINE else '✗'}")
            logger.info(f"  └─ avg_vectors_per_page <= {self.MAX_AVG_VECTORS_PER_PAGE}: {probe['avg_vectors_per_page']:.1f} {'✓' if probe['avg_vectors_per_page'] <= self.MAX_AVG_VECTORS_PER_PAGE else '✗'}")

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
        else:
            logger.info(f"[A-5 Gatekeeper] 閾値チェックスキップ（{origin_app} は WORD 以外）")

        if failures:
            d = GatekeeperDecision(
                decision="BLOCK",
                allowed_processors=[],
                block_code="GATE_CONDITION_FAILED",
                block_reason="; ".join(failures),
                evidence=evidence,
                policy_version=self.POLICY_VERSION,
            )
            logger.warning(f"[A-5 Gatekeeper] ✗ BLOCK（閾値チェック失敗）:")
            for failure in failures:
                logger.warning(f"    - {failure}")
            return asdict(d)

        # ---- ALLOW（プロセッサ選択）----
        logger.info("[A-5 Gatekeeper] プロセッサ選択:")
        if origin_app == "GOOGLE_DOCS":
            allowed_procs = ["B11_GOOGLE_DOCS"]
            reason_suffix = f"GOOGLE_DOCS+{layout_profile}"
            logger.info(f"  ├─ origin_app=GOOGLE_DOCS → B11_GOOGLE_DOCS")
        else:  # WORD
            if layout_profile == "FIXED":
                allowed_procs = ["B30_DTP"]
                reason_suffix = "WORD+FIXED"
                logger.info(f"  ├─ origin_app=WORD, layout=FIXED → B30_DTP")
            else:
                allowed_procs = ["B3_PDF_WORD"]
                reason_suffix = "WORD+FLOW"
                logger.info(f"  ├─ origin_app=WORD, layout=FLOW → B3_PDF_WORD")

        d = GatekeeperDecision(
            decision="ALLOW",
            allowed_processors=allowed_procs,
            block_code=None,
            block_reason=f"allowlist条件を満たしたため許可（{reason_suffix} + 安全閾値クリア）",
            evidence=evidence,
            policy_version=self.POLICY_VERSION,
        )
        logger.info(f"[A-5 Gatekeeper] ✓ ALLOW: {allowed_procs}")
        logger.info(f"  ├─ 理由: {reason_suffix} + 安全閾値クリア")
        logger.info(f"  ├─ chars: {probe['avg_chars_per_page']:.0f}/page")
        logger.info(f"  ├─ vectors: {probe['avg_vectors_per_page']:.0f}/page")
        logger.info(f"  └─ evidence: {evidence}")
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
            logger.warning("[A-5 Gatekeeper] pdfplumber が利用できません → 安全側の値で評価")
            return {"probe_ok": False, "avg_chars_per_page": 0.0, "avg_vectors_per_page": 10_000.0}

        chars_total = 0
        vectors_total = 0
        pages = 0

        try:
            logger.info("[A-5 Gatekeeper] pdfplumberでプローブ実行中...")
            with pdfplumber.open(str(file_path)) as pdf:
                pages = len(pdf.pages) or 0
                for idx, p in enumerate(pdf.pages):
                    page_chars = len(getattr(p, "chars", []) or [])
                    page_lines = len(getattr(p, "lines", []) or [])
                    page_rects = len(getattr(p, "rects", []) or [])
                    page_curves = len(getattr(p, "curves", []) or [])
                    page_vectors = page_lines + page_rects + page_curves

                    chars_total += page_chars
                    vectors_total += page_vectors

                    # 最初の3ページの詳細をログ出力
                    if idx < 3:
                        logger.debug(
                            f"  Page {idx}: chars={page_chars}, "
                            f"vectors={page_vectors} (lines={page_lines}, rects={page_rects}, curves={page_curves})"
                        )

                if pages > 3:
                    logger.debug(f"  ... ({pages - 3} ページ省略)")

        except Exception as e:
            logger.warning(f"[A-5 Gatekeeper] プローブ失敗: {e} → 安全側の値で評価", exc_info=True)
            return {"probe_ok": False, "avg_chars_per_page": 0.0, "avg_vectors_per_page": 10_000.0}

        denom = pages if pages > 0 else 1
        result = {
            "probe_ok": True,
            "avg_chars_per_page": chars_total / denom,
            "avg_vectors_per_page": vectors_total / denom,
        }
        logger.info(f"[A-5 Gatekeeper] プローブ完了: chars_total={chars_total}, vectors_total={vectors_total}, pages={pages}")
        return result
