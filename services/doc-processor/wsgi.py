"""WSGI entry: document hub + optional pipeline studio at /pipeline (same process).

Set PIPELINE_STUDIO=0 (or false) to disable mounting /pipeline.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

from werkzeug.middleware.dispatcher import DispatcherMiddleware

_here = Path(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))
_pr = _here.parent.parent
if str(_pr) not in sys.path:
    sys.path.insert(0, str(_pr))
if str(_pr.parent) not in sys.path:
    sys.path.insert(0, str(_pr.parent))

_spec_app = importlib.util.spec_from_file_location("doc_processor_main_app", _here / "app.py")
_app_mod = importlib.util.module_from_spec(_spec_app)
assert _spec_app.loader is not None
_spec_app.loader.exec_module(_app_mod)
main_app = _app_mod.app


def _debug_subapp():
    flag = (os.environ.get("PIPELINE_STUDIO") or "1").strip().lower()
    if flag in ("0", "false", "no", "off"):
        return None
    pdebug = _here / "pipeline_debug"
    if not (pdebug / "debug_web.py").is_file():
        return None
    if str(pdebug) not in sys.path:
        sys.path.insert(0, str(pdebug))
    root = _here.parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    import debug_web  # noqa: WPS433

    return debug_web.app


_dbg = _debug_subapp()
if _dbg is not None:
    application = DispatcherMiddleware(main_app, {"/pipeline": _dbg})
else:
    application = main_app
