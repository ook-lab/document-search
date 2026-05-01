"""
Cloud Build トリガーの includedFiles を「サービスディレクトリのみ」に揃える。

方針（ユーザー要件）:
- 変更のあったアプリ配下だけをビルド対象にする（shared/** で全サービス連鎖させない）
- 同一 included パスに複数トリガーがある場合は canonical 以外を disabled にする
  （例: ocr-editor / pdf-splitter / pdf-toolbox → いずれも services/pdf-toolbox/**）
- ルート cloudbuild.yaml 用トリガーは手動運用（README 参照）。このスクリプトでは export/import しない。

マッピングの単一ソース: trigger_included_paths.py

使い方:
  python scripts/deploy/fix_triggers_v3.py

前提: gcloud が PATH にあり、該当プロジェクトにログイン済み。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

import yaml

_deploy = Path(__file__).resolve().parent
if str(_deploy) not in sys.path:
    sys.path.insert(0, str(_deploy))
from trigger_included_paths import included_glob_for_trigger_name

PROJECT = os.environ.get("GCP_PROJECT", "consummate-yew-479020-u2")
REGION = os.environ.get("GCP_REGION", "asia-northeast1")
ROOT_BATCH_FILE = "cloudbuild.yaml"


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
        [_gcloud(), *args],
        capture_output=True,
        text=True,
        shell=False,
    )


def resolve_path(trigger_name: str) -> str | None:
    return included_glob_for_trigger_name(trigger_name)


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
        fn = (t.get("filename") or "").replace("\\", "/").lstrip("./")
        if fn == ROOT_BATCH_FILE:
            continue
        p = resolve_path(name)
        if p:
            by_path[p].append(name)

    canonical_for_path: dict[str, str] = {
        path: canonical_trigger_name(names, path)
        for path, names in by_path.items()
    }

    for t in triggers:
        name = t.get("name") or ""
        if not name:
            continue

        list_fn = (t.get("filename") or "").replace("\\", "/").lstrip("./")
        if list_fn == ROOT_BATCH_FILE:
            print(
                f"[{name}] skip root batch trigger ({ROOT_BATCH_FILE}); "
                f"see scripts/deploy/README.md (use per-service triggers)."
            )
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
                print(
                    f"[{name}] skip root batch ({ROOT_BATCH_FILE}) after export; "
                    f"see scripts/deploy/README.md."
                )
                continue
            if target_path:
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
                print(f"[{name}] no trigger_included_paths match → skip")
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
