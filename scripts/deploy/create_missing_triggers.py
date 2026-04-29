import json
import subprocess
from pathlib import Path


PROJECT_ID = "consummate-yew-479020-u2"
REGION = "asia-northeast1"
REPO_OWNER = "ook-lab"
REPO_NAME = "document-management-system"
SERVICE_ACCOUNT = (
    f"projects/{PROJECT_ID}/serviceAccounts/"
    f"document-management-system@{PROJECT_ID}.iam.gserviceaccount.com"
)


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
        "serviceAccount": SERVICE_ACCOUNT,
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

    candidates = [
        (
            "doda-scraper",
            "services/doda-scraper/cloudbuild.yaml",
            "services/doda-scraper/**",
            {
                k: env[k]
                for k in ["_SUPABASE_URL", "_SUPABASE_SERVICE_ROLE_KEY"]
                if k in env
            },
        ),
        (
            "fast-indexer",
            "services/fast-indexer/cloudbuild.yaml",
            "services/fast-indexer/**",
            {
                k: env[k]
                for k in [
                    "_GOOGLE_API_KEY",
                    "_OPENAI_API_KEY",
                    "_SUPABASE_URL",
                    "_SUPABASE_KEY",
                    "_SUPABASE_SERVICE_ROLE_KEY",
                ]
                if k in env
            },
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
