import json
import subprocess
from pathlib import Path

import yaml

PROJECT_ID = "consummate-yew-479020-u2"
REGION = "asia-northeast1"
REPO_OWNER = "ook-lab"
REPO_NAME = "document-management-system"
# 既存トリガーから export して複製する（手書き JSON は regional で INVALID_ARGUMENT になりやすい）
EXPORT_TEMPLATE_TRIGGER = "doc-processor"


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def cloudbuild_substitutions_from_dotenv(env: dict[str, str]) -> dict[str, str]:
    """
    .env の実キー → cloudbuild.yaml の ${_NAME} と一致する substitution 名。
    （.env に _SUPABASE_URL は無いため、従来スクリプトでは substitutions が空になりがち）
    """
    out: dict[str, str] = {}
    if v := env.get("SUPABASE_URL"):
        out["_SUPABASE_URL"] = v
    if v := env.get("SUPABASE_KEY"):
        out["_SUPABASE_KEY"] = v
    if v := env.get("SUPABASE_SERVICE_ROLE_KEY"):
        out["_SUPABASE_SERVICE_ROLE_KEY"] = v
    if v := env.get("OPENAI_API_KEY"):
        out["_OPENAI_API_KEY"] = v
    gem = env.get("GOOGLE_AI_API_KEY") or env.get("GOOGLE_API_KEY")
    if gem:
        out["_GOOGLE_AI_API_KEY"] = gem
    return out


def run_gcloud(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            r"C:\Users\ookub\AppData\Local\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd",
            "--project",
            PROJECT_ID,
            *args,
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def list_trigger_filenames() -> set[str]:
    res = run_gcloud(
        ["builds", "triggers", "list", f"--region={REGION}", "--format=json"]
    )
    if res.returncode != 0:
        raise RuntimeError(f"Failed to list triggers: {res.stderr}")
    triggers = json.loads(res.stdout or "[]")
    return {t.get("filename", "") for t in triggers}


def import_trigger_clone_from_template(
    name: str, filename: str, included: str, substitutions: dict[str, str]
) -> None:
    """
    動いている既存トリガーを export し、name / filename / includedFiles / substitutions だけ差し替えて import。
    """
    export_path = Path(f"tmp_export_{EXPORT_TEMPLATE_TRIGGER}.yaml")
    out_path = Path(f"tmp_{name}_trigger.yaml")
    try:
        res = run_gcloud(
            [
                "beta",
                "builds",
                "triggers",
                "export",
                EXPORT_TEMPLATE_TRIGGER,
                f"--region={REGION}",
                f"--destination={str(export_path)}",
            ]
        )
        if res.returncode != 0:
            raise RuntimeError(
                f"export {EXPORT_TEMPLATE_TRIGGER} failed: {res.stderr or res.stdout}"
            )
        raw = export_path.read_text(encoding="utf-8")
        cfg = yaml.safe_load(raw)
        if not isinstance(cfg, dict):
            raise RuntimeError(f"unexpected export YAML: {cfg!r}")

        for k in ("resourceName", "id", "createTime"):
            cfg.pop(k, None)

        cfg["name"] = name
        cfg["filename"] = filename
        cfg["includedFiles"] = [included]
        cfg["description"] = f"main へ push でビルド（{filename}）"

        if substitutions:
            cfg["substitutions"] = substitutions
        else:
            cfg.pop("substitutions", None)

        tags = list(cfg.get("tags") or [])
        tags = [name if t == EXPORT_TEMPLATE_TRIGGER else t for t in tags]
        if name not in tags:
            tags.append(name)
        cfg["tags"] = tags

        with out_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(
                cfg,
                f,
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True,
            )

        res = run_gcloud(
            [
                "beta",
                "builds",
                "triggers",
                "import",
                f"--region={REGION}",
                f"--source={str(out_path)}",
            ]
        )
        if res.returncode != 0:
            raise RuntimeError(
                f"Failed to import {name}: stderr={res.stderr!r} stdout={res.stdout!r}"
            )
    finally:
        for p in (export_path, out_path):
            if p.exists():
                p.unlink()


def main() -> None:
    env = load_env(Path(".env"))
    existing_filenames = list_trigger_filenames()

    subs_common = cloudbuild_substitutions_from_dotenv(env)

    candidates = [
        (
            "doda-scraper",
            "services/doda-scraper/cloudbuild.yaml",
            "services/doda-scraper/**",
            {k: subs_common[k] for k in ("_SUPABASE_URL", "_SUPABASE_SERVICE_ROLE_KEY") if k in subs_common},
        ),
        (
            "rag-prepare",
            "services/rag-prepare/cloudbuild.yaml",
            "services/rag-prepare/**",
            subs_common,
        ),
    ]

    for name, filename, included, substitutions in candidates:
        if filename in existing_filenames:
            print(f"exists: {name}")
            continue
        import_trigger_clone_from_template(name, filename, included, substitutions)
        print(f"created: {name}")


if __name__ == "__main__":
    main()
