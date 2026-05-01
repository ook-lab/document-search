"""
既存のいずれかのリージョナル Cloud Build トリガーを export し、
同一接続（GitHub 等）で「欠けている」サービス用トリガーを import で複製する。

前提:
  - gcloud 認証済み、`GCP_PROJECT`（省略時は既定）で対象プロジェクトが選ばれていること
  - 複製元になるトリガーが少なくとも 1 本あること（なければ先にコンソールで 1 本作成）

使い方（リポジトリルートから）:

  python scripts/deploy/create_missing_triggers.py

方針:
  - リポジトリ内の各 `services/<name>/cloudbuild.yaml` / `portal-app/cloudbuild.yaml` /
    `my-calendar-app/cloudbuild.yaml` に対し、同じ filename のトリガーが無ければ作成
  - ルートの `cloudbuild.yaml`（バッチ）はトリガー自動作成対象外（README の二重ビルド回避）
  - import 後のトリガーは `includedFiles` をそのサービスツリーだけに設定
  - テンプレートからコピーした `substitutions` は削除し、各 cloudbuild.yaml 側の既定に任せる
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

REGION = os.environ.get("GCP_REGION", "asia-northeast1")
PROJECT_ID = os.environ.get("GCP_PROJECT", "consummate-yew-479020-u2")
REPO_ROOT = Path(__file__).resolve().parents[2]

# ルートからの cloudbuild パス → GCP トリガー名（既存運用と揃えた名前）
# filename はリポジトリルートからの POSIX 相対パス
_TRIGGER_SPECS: tuple[tuple[str, str, str], ...] = (
    ("ai-cost-tracker", "services/ai-cost-tracker/cloudbuild.yaml", "services/ai-cost-tracker/**"),
    ("calendar-register", "services/calendar-register/cloudbuild.yaml", "services/calendar-register/**"),
    ("daily-report-deploy", "services/daily-report/cloudbuild.yaml", "services/daily-report/**"),
    ("data-ingestion-deploy-trigger", "services/data-ingestion/cloudbuild.yaml", "services/data-ingestion/**"),
    ("doc-processor", "services/doc-processor/cloudbuild.yaml", "services/doc-processor/**"),
    ("doc-search", "services/doc-search/cloudbuild.yaml", "services/doc-search/**"),
    ("doda-scraper", "services/doda-scraper/cloudbuild.yaml", "services/doda-scraper/**"),
    ("drive-checker-deploy", "services/drive-duplicate-checker/cloudbuild.yaml", "services/drive-duplicate-checker/**"),
    ("gmail-service", "services/gmail-service/cloudbuild.yaml", "services/gmail-service/**"),
    ("html-to-a4-deploy", "services/html-to-a4/cloudbuild.yaml", "services/html-to-a4/**"),
    ("kakeibo-ui", "services/kakeibo/cloudbuild.yaml", "services/kakeibo/**"),
    ("my-calendar-app", "my-calendar-app/cloudbuild.yaml", "my-calendar-app/**"),
    ("pdf-toolbox", "services/pdf-toolbox/cloudbuild.yaml", "services/pdf-toolbox/**"),
    ("portal-deploy", "portal-app/cloudbuild.yaml", "portal-app/**"),
    ("rag-prepare", "services/rag-prepare/cloudbuild.yaml", "services/rag-prepare/**"),
    ("tenshoku-tool", "services/tenshoku-tool/cloudbuild.yaml", "services/tenshoku-tool/**"),
)

# export に使う候補（先頭から順に試す）
_EXPORT_TEMPLATE_CANDIDATES: tuple[str, ...] = (
    "doc-processor",
    "ai-cost-tracker",
    "calendar-register",
    "kakeibo-ui",
    "doc-search",
)


def _gcloud() -> str:
    exe = shutil.which("gcloud")
    if exe:
        return exe
    win = Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Cloud SDK/google-cloud-sdk/bin/gcloud.cmd"
    if win.is_file():
        return str(win)
    raise FileNotFoundError("gcloud が見つかりません。Google Cloud SDK を PATH に通してください。")


def run_gcloud(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_gcloud(), "--project", PROJECT_ID, *args],
        capture_output=True,
        text=True,
        check=False,
    )


def list_triggers() -> list[dict]:
    res = run_gcloud(["builds", "triggers", "list", f"--region={REGION}", "--format=json"])
    if res.returncode != 0:
        raise RuntimeError(f"triggers list failed: {res.stderr or res.stdout}")
    data = json.loads(res.stdout or "[]")
    return data if isinstance(data, list) else []


def existing_filenames(triggers: list[dict]) -> set[str]:
    out: set[str] = set()
    for t in triggers:
        fn = (t.get("filename") or "").replace("\\", "/").lstrip("./")
        if fn:
            out.add(fn)
    return out


def existing_trigger_names(triggers: list[dict]) -> set[str]:
    return {t.get("name") for t in triggers if t.get("name")}


def pick_export_template_name(triggers: list[dict]) -> str:
    names = {t.get("name") for t in triggers if t.get("name")}
    for cand in _EXPORT_TEMPLATE_CANDIDATES:
        if cand in names:
            return cand
    for t in triggers:
        n = t.get("name")
        if n and (t.get("filename") or "").endswith("cloudbuild.yaml"):
            return n
    raise RuntimeError(
        "複製元トリガーがありません。コンソールで Git 接続付きのリージョナルトリガーを 1 本作成してから再実行してください。"
    )


def import_trigger_clone_from_template(
    template_name: str, name: str, filename: str, included: str
) -> None:
    fd_e, export_path = tempfile.mkstemp(suffix="_export.yaml", prefix="cb_trigger_")
    fd_i, out_path = tempfile.mkstemp(suffix="_import.yaml", prefix="cb_trigger_")
    os.close(fd_e)
    os.close(fd_i)
    try:
        res = run_gcloud(
            [
                "beta",
                "builds",
                "triggers",
                "export",
                template_name,
                f"--region={REGION}",
                f"--destination={export_path}",
            ]
        )
        if res.returncode != 0:
            raise RuntimeError(f"export {template_name} failed: {res.stderr or res.stdout}")

        raw = Path(export_path).read_text(encoding="utf-8")
        cfg = yaml.safe_load(raw)
        if not isinstance(cfg, dict):
            raise RuntimeError(f"unexpected export YAML: {cfg!r}")

        for k in ("resourceName", "id", "createTime"):
            cfg.pop(k, None)

        cfg["name"] = name
        cfg["filename"] = filename
        cfg["includedFiles"] = [included]
        cfg["description"] = f"Push to main: {filename}"
        cfg.pop("substitutions", None)

        tags = list(cfg.get("tags") or [])
        tags = [name if t == template_name else t for t in tags]
        if name not in tags:
            tags.append(name)
        cfg["tags"] = tags

        with open(out_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

        res = run_gcloud(
            [
                "beta",
                "builds",
                "triggers",
                "import",
                f"--region={REGION}",
                f"--source={out_path}",
            ]
        )
        if res.returncode != 0:
            raise RuntimeError(f"import {name} failed: stderr={res.stderr!r} stdout={res.stdout!r}")
    finally:
        for p in (export_path, out_path):
            try:
                os.unlink(p)
            except OSError:
                pass


def main() -> None:
    os.chdir(REPO_ROOT)
    triggers = list_triggers()
    have_files = existing_filenames(triggers)
    have_names = existing_trigger_names(triggers)
    template = pick_export_template_name(triggers)
    print(f"Project={PROJECT_ID} region={REGION} template={template}")

    created = 0
    skipped = 0
    for name, filename, included in _TRIGGER_SPECS:
        path = REPO_ROOT / filename.replace("/", os.sep)
        if not path.is_file():
            print(f"skip (no file): {filename}")
            skipped += 1
            continue
        if filename in have_files:
            print(f"exists: {name} ({filename})")
            skipped += 1
            continue
        if name in have_names:
            print(f"exists by name (filename mismatch?): {name}")
            skipped += 1
            continue
        print(f"creating: {name} <- {template}")
        import_trigger_clone_from_template(template, name, filename, included)
        have_files.add(filename)
        have_names.add(name)
        created += 1
        # 次回以降は自分自身をテンプレにできるが、接続情報は同一なのでそのまま template 継続でよい

    print(f"done. created={created} skipped_or_missing_file={skipped}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
