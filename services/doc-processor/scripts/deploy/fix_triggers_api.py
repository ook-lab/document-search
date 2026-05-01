"""Delegate to repository scripts/deploy/fix_triggers_api.py (single source of truth)."""
import runpy
import sys
from pathlib import Path

if __name__ == "__main__":
    root = Path(__file__).resolve().parents[4]
    script = root / "scripts" / "deploy" / "fix_triggers_api.py"
    if not script.is_file():
        raise SystemExit(f"canonical script not found: {script}")
    sys.argv[0] = str(script)
    runpy.run_path(str(script), run_name="__main__")
