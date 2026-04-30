import json
import subprocess
from pathlib import Path


PROJECT_ID = "consummate-yew-479020-u2"
REGION = "asia-northeast1"
REPO_OWNER = "ook-lab"
REPO_NAME = "document-management-system"
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


def build_trigger_payload(
    name: str, filename: str, included: str, substitutions: dict[str, str]
) -> dict:
    payload = {
        "name": name,
        "filename": filename,
        "github": {
            "owner": REPO_OWNER,
            "name": REPO_NAME,
            "push": {"branch": "^main$"},
        },
        "includedFiles": [included],
        # トリガーに build 用 serviceAccount を付けると、GCP 側で logs_bucket /
        # defaultLogsBucketBehavior / CLOUD_LOGGING_ONLY 等の組み合わせが必須になる。
        # 既定の Cloud Build SA で十分なら付けない（コンソールで付けた場合は cloudbuild と整合させる）。
    }
    if substitutions:
        payload["substitutions"] = substitutions
    return payload


def import_trigger(payload: dict, temp_path: Path) -> None:
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    res = run_gcloud(
        [
            "beta",
            "builds",
            "triggers",
            "import",
            f"--region={REGION}",
            f"--source={str(temp_path)}",
        ]
    )
    if res.returncode != 0:
        raise RuntimeError(f"Failed to import {payload['name']}: {res.stderr}")


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
            "fast-indexer",
            "services/fast-indexer/cloudbuild.yaml",
            "services/fast-indexer/**",
            subs_common,
        ),
    ]

    for name, filename, included, substitutions in candidates:
        if filename in existing_filenames:
            print(f"exists: {name}")
            continue
        payload = build_trigger_payload(name, filename, included, substitutions)
        temp_file = Path(f"tmp_{name}_trigger.json")
        import_trigger(payload, temp_file)
        print(f"created: {name}")


if __name__ == "__main__":
    main()
