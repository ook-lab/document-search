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
import yaml

# 判定ルール定義ファイル（TypeAnalyzerと共通・唯一の設定場所）
_RULES_FILE = Path(__file__).parent / 'type_rules.yaml'


def _load_rules() -> dict:
    with open(_RULES_FILE, encoding='utf-8') as f:
        return yaml.safe_load(f)


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

    # 全設定は type_rules.yaml から読む（コードに定数を持たない）
    def __init__(self):
        rules = _load_rules()
        gk = rules.get('gatekeeper', {})

        self.POLICY_VERSION       = gk.get('policy_version', 'A5.v1')
        self.ALLOWED_COMBINATIONS = [tuple(c) for c in gk.get('allowed_combinations', [])]

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

        # Native ファイルは拡張子で確定（PDF固有チェックを全スキップ）
        _NATIVE = {
            '.docx': 'B6_NATIVE_WORD',
            '.xlsx': 'B7_NATIVE_EXCEL',
            '.pptx': 'B8_NATIVE_PPT',
        }
        suffix = file_path.suffix.lower()
        if suffix in _NATIVE:
            proc = _NATIVE[suffix]
            logger.info(f"[A-5 Gatekeeper] ✓ ALLOW (native): {suffix} → {proc}")
            return asdict(GatekeeperDecision(
                decision="ALLOW",
                allowed_processors=[proc],
                block_code=None,
                block_reason=f"native file: {suffix}",
                evidence={"suffix": suffix},
                policy_version=self.POLICY_VERSION,
            ))

        # 未知の拡張子は BLOCK
        if suffix != ".pdf":
            d = GatekeeperDecision(
                decision="BLOCK",
                allowed_processors=[],
                block_code="EXT_NOT_ALLOWED",
                block_reason=f"未対応の拡張子: {file_path.suffix}",
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

        # v1 allowlist：ALLOWED_COMBINATIONS クラス属性を参照（唯一の定義場所）
        logger.info("[A-5 Gatekeeper] Allowlistチェック:")
        logger.info(f"  ├─ 組み合わせ: ({origin_app}, {layout_profile})")
        logger.info(f"  └─ 許可リスト: {self.ALLOWED_COMBINATIONS}")
        if (origin_app, layout_profile) not in self.ALLOWED_COMBINATIONS:
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

        # ---- page_type_map からコンテンツページを特定（evidence 記録用）----
        _EXCLUDE_FROM_METRICS = frozenset({'UNKNOWN', 'SCAN'})
        page_type_map = a_result.get("page_type_map") or {}
        content_pages = {
            int(p): t for p, t in page_type_map.items()
            if t not in _EXCLUDE_FROM_METRICS
        } if page_type_map else {}
        logger.info(
            f"[A-5 Gatekeeper] page_type_map: 全{len(page_type_map)}ページ → "
            f"コンテンツページ {len(content_pages)}ページ "
            f"（除外: {len(page_type_map) - len(content_pages)}ページ）"
        )

        # ---- Stage A メトリクスを evidence 用に取得 ----
        # A4 layout_metrics.per_page があればコンテンツページのみで再計算
        # （a4_layout → layout_metrics → per_page の階層構造）
        per_page = layout_metrics.get("per_page") or []
        if per_page and content_pages:
            content_rows = [
                m for m in per_page
                if m.get("page") in content_pages
            ]
        else:
            content_rows = []

        if content_rows:
            avg_images = sum(r.get("images", 0) for r in content_rows) / len(content_rows)
            avg_words  = sum(r.get("words",  0) for r in content_rows) / len(content_rows)
            avg_x_std  = sum(r.get("x_std",  0) for r in content_rows) / len(content_rows)
            logger.info("[A-5 Gatekeeper] メトリクス（コンテンツページのみ再計算）:")
        else:
            avg_images = float(layout_metrics.get("avg_images_per_page", 0) or 0)
            avg_words  = float(layout_metrics.get("avg_words_per_page",  0) or 0)
            avg_x_std  = float(layout_metrics.get("avg_x_std",           0) or 0)
            logger.info("[A-5 Gatekeeper] メトリクス（全ページ平均 ※per_page_metricsなし）:")

        logger.info(f"  ├─ avg_images_per_page: {avg_images:.1f}")
        logger.info(f"  ├─ avg_words_per_page: {avg_words:.1f}")
        logger.info(f"  └─ avg_x_std: {avg_x_std:.1f}")

        evidence = {
            "origin_app": origin_app,
            "layout_profile": layout_profile,
            "confidence": confidence,
            "avg_images_per_page": avg_images,
            "avg_words_per_page": avg_words,
            "avg_x_std": avg_x_std,
            **probe,
        }

        # ---- ALLOW（プロセッサ選択）----
        # 各分岐の根拠: origin_app は A2 メタデータ一致 or ページフォント解析で確定したもの
        # （具体的な根拠は上の A2 ログ参照）
        a2_reason = a_result.get("reason", "")
        logger.info("[A-5 Gatekeeper] プロセッサ選択:")
        logger.info(f"  ├─ 判定根拠: {a2_reason}")
        if origin_app == "GOOGLE_DOCS":
            # メタデータ Creator/Producer に 'google docs' が含まれていたため
            allowed_procs = ["B11_GOOGLE_DOCS"]
            reason_suffix = f"GOOGLE_DOCS+{layout_profile}"
            logger.info(f"  ├─ origin_app=GOOGLE_DOCS → B11_GOOGLE_DOCS")
        elif origin_app == "REPORT":
            # ページフォントに WING (WINJr帳票システム固有) が含まれていたため
            allowed_procs = ["B42_MULTICOLUMN"]
            reason_suffix = f"REPORT+{layout_profile}"
            logger.info(f"  ├─ origin_app=REPORT → B42_MULTICOLUMN")
        elif origin_app == "ILLUSTRATOR":
            # メタデータ Creator/Producer に 'adobe illustrator' が含まれていたため
            # REPORTページ（WING帳票）が混在する場合は B42 も許可
            _type_groups = a_result.get("type_groups") or {}
            if "REPORT" in _type_groups:
                allowed_procs = ["B30_ILLUSTRATOR", "B42_MULTICOLUMN"]
                reason_suffix = f"ILLUSTRATOR+REPORT混在+{layout_profile}"
                logger.info(f"  ├─ origin_app=ILLUSTRATOR + REPORTページあり → B30_ILLUSTRATOR + B42_MULTICOLUMN")
            else:
                allowed_procs = ["B30_ILLUSTRATOR"]
                reason_suffix = f"ILLUSTRATOR+{layout_profile}"
                logger.info(f"  ├─ origin_app=ILLUSTRATOR → B30_ILLUSTRATOR")
        elif origin_app == "INDESIGN":
            # メタデータ Creator/Producer に 'adobe indesign' が含まれていたため
            allowed_procs = ["B31_INDESIGN"]
            reason_suffix = f"INDESIGN+{layout_profile}"
            logger.info(f"  ├─ origin_app=INDESIGN → B31_INDESIGN")
        elif origin_app == "EXCEL":
            allowed_procs = ["B4_PDF_EXCEL"]
            reason_suffix = f"EXCEL+{layout_profile}"
            logger.info(f"  ├─ origin_app=EXCEL → B4_PDF_EXCEL")
        elif origin_app == "GOOGLE_SHEETS":
            allowed_procs = ["B12_GOOGLE_SHEETS"]
            reason_suffix = f"GOOGLE_SHEETS+{layout_profile}"
            logger.info(f"  ├─ origin_app=GOOGLE_SHEETS → B12_GOOGLE_SHEETS")
        elif origin_app == "GOODNOTES":
            allowed_procs = ["B14_GOODNOTES"]
            reason_suffix = f"GOODNOTES+{layout_profile}"
            logger.info(f"  ├─ origin_app=GOODNOTES → B14_GOODNOTES")
        elif origin_app == "POWERPOINT":
            # メタデータ Creator/Producer に 'powerpoint' が含まれていたため
            allowed_procs = ["B5_PDF_PPT"]
            reason_suffix = f"POWERPOINT+{layout_profile}"
            logger.info(f"  ├─ origin_app=POWERPOINT → B5_PDF_PPT")
        elif origin_app == "ACROBAT_PDFMAKER":
            # Word文書をAcrobatアドイン経由でPDF化 → Word系プロセッサで処理
            allowed_procs = ["B3_PDF_WORD"]
            reason_suffix = f"ACROBAT_PDFMAKER+{layout_profile}"
            logger.info(f"  ├─ origin_app=ACROBAT_PDFMAKER → B3_PDF_WORD")
        elif origin_app == "MIXED":
            allowed_procs = ["B3_PDF_WORD", "B61_PDF_WORD_LTSC", "B62_PDF_WORD_2019",
                             "B4_PDF_EXCEL", "B5_PDF_PPT", "B30_ILLUSTRATOR", "B31_INDESIGN",
                             "B42_MULTICOLUMN", "B11_GOOGLE_DOCS", "B12_GOOGLE_SHEETS",
                             "B80_SCAN_OCR"]
            reason_suffix = f"MIXED+{layout_profile}"
            logger.info(f"  ├─ origin_app=MIXED → 全プロセッサ許可（B1 が type_groups で選択）")
        elif origin_app == "CANVA":
            allowed_procs = ["B16_CANVA"]
            reason_suffix = f"CANVA+{layout_profile}"
            logger.info(f"  ├─ origin_app=CANVA → B16_CANVA")
        elif origin_app == "STUDYAID":
            allowed_procs = ["B17_STUDYAID"]
            reason_suffix = f"STUDYAID+{layout_profile}"
            logger.info(f"  ├─ origin_app=STUDYAID → B17_STUDYAID")
        elif origin_app == "IOS_QUARTZ":
            allowed_procs = ["B18_IOS_QUARTZ"]
            reason_suffix = f"IOS_QUARTZ+{layout_profile}"
            logger.info(f"  ├─ origin_app=IOS_QUARTZ → B18_IOS_QUARTZ")
        elif origin_app == "ACROBAT":
            allowed_procs = ["B39_ACROBAT"]
            reason_suffix = f"ACROBAT+{layout_profile}"
            logger.info(f"  ├─ origin_app=ACROBAT → B39_ACROBAT")
        elif origin_app == "SCAN":
            # B80_SCAN_OCR（実装済み）でスキャン文書を処理。
            # 本番ブロックは B1Controller.PRODUCTION_ALLOWED_PROCESSORS で担保。
            allowed_procs = ["B80_SCAN_OCR"]
            reason_suffix = f"SCAN+{layout_profile}"
            logger.info(f"  ├─ origin_app=SCAN → B80_SCAN_OCR")
        elif origin_app == "UNKNOWN":
            # 推論エンジン未実装 → BLOCK（allowed_combinations に含まれていないため通常ここには到達しない）
            d = GatekeeperDecision(
                decision="BLOCK",
                allowed_processors=[],
                block_code="UNKNOWN_NOT_SUPPORTED",
                block_reason="origin_app=UNKNOWN: 推論エンジン未実装。InferenceEngine 実装後に解禁する。",
                evidence={"origin_app": origin_app, "layout_profile": layout_profile},
                policy_version=self.POLICY_VERSION,
            )
            logger.warning(f"[A-5 Gatekeeper] ✗ BLOCK: {d.block_reason}")
            return asdict(d)
        elif origin_app == "WORD_LTSC":
            allowed_procs = ["B61_PDF_WORD_LTSC"]
            reason_suffix = f"WORD_LTSC+{layout_profile}"
            logger.info(f"  ├─ origin_app=WORD_LTSC → B61_PDF_WORD_LTSC")
        elif origin_app == "WORD_2019":
            allowed_procs = ["B62_PDF_WORD_2019"]
            reason_suffix = f"WORD_2019+{layout_profile}"
            logger.info(f"  ├─ origin_app=WORD_2019 → B62_PDF_WORD_2019")
        else:  # WORD
            # メタデータ or ページフォント（MS-PGothic/Meiryo等）でWORDと判定
            # Word PDF は標準テキストオブジェクト構造 → B3 一択（FLOW/FIXED 不問）
            allowed_procs = ["B3_PDF_WORD"]
            reason_suffix = f"WORD+{layout_profile}"
            logger.info(f"  ├─ origin_app=WORD → B3_PDF_WORD")

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
