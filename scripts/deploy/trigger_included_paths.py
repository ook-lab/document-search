"""
Cloud Build trigger name -> single includedFiles glob.

Rules:
- Use only services/<name>/** or portal-app/** or my-calendar-app/** by default.
- Do not include shared/** (avoids monorepo-wide builds on every shared edit).
- For shared-only rebuilds, create a separate GCP trigger with a narrow glob
  (e.g. services/kakeibo/kakeibo_lib/**), never a broad shared/** that fans out to all apps.

pdf-toolbox Cloud Run: trigger names ocr-editor / pdf-splitter / pdf-toolbox
all map to the same path services/pdf-toolbox/**.
"""

from __future__ import annotations

PDF_TOOLBOX_CLOUD_RUN_INCLUDED = "services/pdf-toolbox/**"

_SERVICE_RULES: tuple[tuple[str, str], ...] = (
    ("ai-cost-tracker", "services/ai-cost-tracker/**"),
    ("calendar-register", "services/calendar-register/**"),
    ("my-calendar-app", "my-calendar-app/**"),
    ("drive-duplicate-checker", "services/drive-duplicate-checker/**"),
    ("drive-checker", "services/drive-duplicate-checker/**"),
    ("gmail-service", "services/gmail-service/**"),
    ("data-ingestion", "services/data-ingestion/**"),
    ("doc-processor", "services/doc-processor/**"),
    ("daily-report", "services/daily-report/**"),
    ("doda-scraper", "services/doda-scraper/**"),
    # 旧 doc-review トリガー名は document-hub（doc-processor）と同一ツリーへマップ
    ("doc-review", "services/doc-processor/**"),
    ("doc-search", "services/doc-search/**"),
    ("html-to-a4", "services/html-to-a4/**"),
    ("kakeibo-view", "services/kakeibo/**"),
    ("kakeibo_view", "services/kakeibo/**"),
    ("kakeibo-ui", "services/kakeibo/**"),
    ("kakeibo", "services/kakeibo/**"),
    ("rag-prepare", "services/rag-prepare/**"),
    ("tenshoku-tool", "services/tenshoku-tool/**"),
    ("portal-app", "portal-app/**"),
    ("portal-deploy", "portal-app/**"),
    ("resume-maker", "services/resume-maker/**"),
    ("debug-pipeline", "services/doc-processor/**"),
    ("pdf-toolbox", PDF_TOOLBOX_CLOUD_RUN_INCLUDED),
    ("pdf-splitter", PDF_TOOLBOX_CLOUD_RUN_INCLUDED),
    ("ocr-editor", PDF_TOOLBOX_CLOUD_RUN_INCLUDED),
)

_SERVICE_RULES_SORTED: tuple[tuple[str, str], ...] = tuple(
    sorted(_SERVICE_RULES, key=lambda x: len(x[0]), reverse=True)
)


def included_glob_for_trigger_name(trigger_name: str) -> str | None:
    """Return one includedFiles glob for this trigger name, or None if unknown."""
    n = trigger_name.lower()
    for needle, path in _SERVICE_RULES_SORTED:
        if needle.lower() in n:
            return path
    return None


def deploy_dir_for_import() -> str:
    from pathlib import Path as _P

    return str(_P(__file__).resolve().parent)
