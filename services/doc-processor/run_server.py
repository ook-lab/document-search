#!/usr/bin/env python
"""ローカル起動: Document Hub + /pipeline スタジオ（wsgi.application）。"""
import importlib.util
import os
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent.parent
services_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(services_root))

_here = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("doc_processor_wsgi", _here / "wsgi.py")
_wsgi = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_wsgi)
application = _wsgi.application

if __name__ == "__main__":
    from werkzeug.serving import run_simple

    port = int(os.environ.get("PORT", 5000))
    run_simple("0.0.0.0", port, application, use_reloader=False, threaded=True)
