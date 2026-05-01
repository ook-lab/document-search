# Deploy scripts (Cloud Build triggers)

## Build policy (mandatory, team-wide)

- **Production image builds and Cloud Run deploys run only via Cloud Build triggers on Git push.** No other path is the default.
- **Do not run `gcloud builds submit` (or equivalent) unless the owner explicitly names one cloudbuild file and one build.** This applies to humans, scripts, and AI assistants. Prevents billing spikes, duplicate builds, and queue floods.
- **One canonical trigger per Cloud Run service:** `services/<name>/cloudbuild.yaml` plus a narrow `includedFiles` glob for that service tree only.

## Per-service triggers (recommended)

- Point each GitHub / Cloud Source trigger at **`services/<service>/cloudbuild.yaml`** only.
- Set **`includedFiles`** to that service tree only, for example `services/doc-processor/**`.
- Do **not** add a second trigger on the same branch that also fires on every push (for example a root **`cloudbuild.yaml`** batch plus a per-service trigger for the same service), or you get duplicate builds.

## Root `cloudbuild.yaml` (batch: doc-processor + html-to-a4)

The repository root file builds two services in one pipeline. Keep it for manual or special batch deploys if you need it, but **do not** mirror the same branch with overlapping triggers. If you use a root trigger, narrow **`includedFiles`** in the console (for example only the two service directories), and avoid **`shared/**`** alone so a shared edit does not fan out to unrelated services.

## Shared libraries

Default trigger filters in `trigger_included_paths.py` **exclude** `shared/**`. If you need a rebuild when only shared code changes, create a **separate** trigger with a **narrow** glob (e.g. `shared/kakeibo/**`), not `shared/**` for all apps.

## pdf-toolbox family (one Cloud Run service)

Triggers whose names contain `ocr-editor`, `pdf-splitter`, or `pdf-toolbox` all map to **`services/pdf-toolbox/**`**. Run `fix_triggers_v3.py` to set **`includedFiles`** and disable duplicate triggers so only one canonical trigger stays enabled.

## Applying trigger fixes

After `gcloud auth login` and project selection:

```bash
python scripts/deploy/fix_triggers_v3.py
```

欠けているリージョナルトリガーをまとめて作成（既存の 1 本を export して複製）:

```bash
python scripts/deploy/create_missing_triggers.py
```

初回は Git 接続付きのトリガーが少なくとも 1 本必要です。`GCP_PROJECT` / `GCP_REGION` で上書き可。

Optional: patch **`includedFiles`** only via API (no duplicate consolidation):

```bash
python scripts/deploy/fix_triggers_api.py
```

Environment variables for `fix_triggers_v3.py`: `GCP_PROJECT`, `GCP_REGION` (defaults match the previous hard-coded project / `asia-northeast1`).
