"""
Cloud Build トリガーの includedFiles を「サービスディレクトリのみ」に揃える。

方針（ユーザー要件）:
- 変更のあったアプリ配下だけをビルド対象にする（shared/** で全サービス連鎖させない）
- 同一ディレクトリに複数トリガーがぶら下がっている場合は、canonical 以外を disabled にする
  （例: ocr-editor / pdf-splitter / pdf-toolbox がすべて services/pdf-toolbox/** のとき 3 重起動する）
- リポジトリルートの cloudbuild.yaml 用トリガーは「まとめビルド」用のため、
  個別サービス運用と二重になる。デフォルトではそのトリガーを disabled にする
  （環境変数 KEEP_ROOT_BATCH_TRIGGER=1 のときだけスキップ）

使い方:
  python scripts/deploy/fix_triggers_v3.py

前提: gcloud が PATH にあり、該当プロジェクトにログイン済み。
"""
import json
import os
import subprocess
import tempfile
from collections import defaultdict

import yaml

PROJECT = os.environ.get("GCP_PROJECT", "consummate-yew-479020-u2")
REGION = os.environ.get("GCP_REGION", "asia-northeast1")
ROOT_BATCH_FILE = "cloudbuild.yaml"

# トリガー名に含まれるキー → 監視パス（shared/** は入れない）
SERVICE_MAP: list[tuple[str, str]] = [
    ("doc-processor", "services/doc-processor/**"),
    ("html-to-a4", "services/html-to-a4/**"),
    ("doda-scraper", "services/doda-scraper/**"),
    ("ocr-editor", "services/pdf-toolbox/**"),
    ("pdf-splitter", "services/pdf-toolbox/**"),
    ("pdf-toolbox", "services/pdf-toolbox/**"),
    ("resume-maker", "services/resume-maker/**"),
    ("kakeibo", "services/kakeibo/**"),
    ("calendar-register", "services/calendar-register/**"),
    ("daily-report", "services/daily-report/**"),
    ("ai-cost-tracker", "services/ai-cost-tracker/**"),
    ("my-calendar-app", "my-calendar-app/**"),
    ("portal-app", "portal-app/**"),
    ("drive-checker", "services/drive-duplicate-checker/**"),
    ("doc-review", "services/doc-review/**"),
    ("doc-search", "services/doc-search/**"),
    ("data-ingestion", "services/data-ingestion/**"),
    ("tenshoku-tool", "services/tenshoku-tool/**"),
    ("debug-pipeline", "services/debug-pipeline/**"),
    ("rag-prepare", "services/rag-prepare/**"),
]


def run_gcloud(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["gcloud", *args],
        capture_output=True,
        text=True,
        shell=False,
    )


def resolve_path(trigger_name: str) -> str | None:
    n = trigger_name.lower()
    for key, path in SERVICE_MAP:
        if key in n:
            return path
    return None


def canonical_trigger_name(names: list[str], path: str) -> str:
    """同一 path に複数トリガーがあるとき、有効に残す名前を1つ選ぶ。"""
    if len(names) == 1:
        return names[0]
    # pdf-toolbox 系: 名前に pdf-toolbox を含むものを優先
    if path == "services/pdf-toolbox/**":
        for name in sorted(names):
            if "pdf-toolbox" in name.lower():
                return name
    # それ以外は安定ソートで先頭（運用で差し替え可能）
    return sorted(names)[0]


def main() -> None:
    print(f"Project={PROJECT} region={REGION}")
    res = run_gcloud(
        [
            "builds",
            "triggers",
            "list",
            f"--region={REGION}",
            f"--project={PROJECT}",
            "--format=json",
        ]
    )
    if res.returncode != 0:
        print(f"Error listing triggers: {res.stderr}")
        return

    triggers = json.loads(res.stdout or "[]")
    # path -> [trigger_name, ...]
    by_path: dict[str, list[str]] = defaultdict(list)
    for t in triggers:
        name = t.get("name") or ""
        if not name:
            continue
        p = resolve_path(name)
        if p:
            by_path[p].append(name)

    canonical_for_path: dict[str, str] = {
        path: canonical_trigger_name(names, path)
        for path, names in by_path.items()
    }

    keep_root = os.environ.get("KEEP_ROOT_BATCH_TRIGGER", "").strip() == "1"

    for t in triggers:
        name = t.get("name") or ""
        if not name:
            continue

        target_path = resolve_path(name)
        tmp_file = None
        try:
            fd, tmp_file = tempfile.mkstemp(suffix="_trigger.yaml")
            os.close(fd)

            exp = run_gcloud(
                [
                    "beta",
                    "builds",
                    "triggers",
                    "export",
                    name,
                    f"--region={REGION}",
                    f"--project={PROJECT}",
                    f"--destination={tmp_file}",
                ]
            )
            if exp.returncode != 0 or not os.path.exists(tmp_file):
                print(f"  Skip export failed: {name} {exp.stderr}")
                continue

            with open(tmp_file, encoding="utf-8") as f:
                cfg = yaml.safe_load(f)

            filename = (cfg.get("filename") or "").replace("\\", "/").lstrip("./")
            is_root_batch = filename == ROOT_BATCH_FILE

            if is_root_batch:
                if keep_root:
                    print(
                        f"[{name}] root batch {ROOT_BATCH_FILE}: KEEP_ROOT_BATCH_TRIGGER=1 → leave enabled"
                    )
                    continue
                cfg["disabled"] = True
                print(
                    f"[{name}] DISABLE root batch trigger ({ROOT_BATCH_FILE}). "
                    f"Use services/*/cloudbuild.yaml triggers only. Set KEEP_ROOT_BATCH_TRIGGER=1 to skip."
                )
            elif target_path:
                cfg["includedFiles"] = [target_path]
                dup = len(by_path.get(target_path, [])) > 1
                winner = canonical_for_path.get(target_path)
                if dup and winner:
                    cfg["disabled"] = name != winner
                    if name != winner:
                        print(
                            f"[{name}] duplicate path {target_path} → disabled "
                            f"(canonical={winner})"
                        )
                    else:
                        print(
                            f"[{name}] duplicate path {target_path} → canonical stays enabled"
                        )
                else:
                    cfg["disabled"] = False
                print(f"[{name}] includedFiles={cfg['includedFiles']}")
            else:
                print(f"[{name}] no SERVICE_MAP match → skip")
                continue

            with open(tmp_file, "w", encoding="utf-8") as f:
                yaml.safe_dump(cfg, f, sort_keys=False)

            imp = run_gcloud(
                [
                    "beta",
                    "builds",
                    "triggers",
                    "import",
                    f"--region={REGION}",
                    f"--project={PROJECT}",
                    f"--source={tmp_file}",
                ]
            )
            if imp.returncode != 0:
                print(f"  Import failed {name}: {imp.stderr}")
            else:
                print(f"  Imported OK: {name}")
        finally:
            if tmp_file and os.path.exists(tmp_file):
                os.remove(tmp_file)


if __name__ == "__main__":
    main()
