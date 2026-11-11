#!/usr/bin/env python3
"""trace_20261105.py -- importable alias for trace-20261105.py (method 09, 2026-11-05).

Root cause fixed here: the shared production trace module lives in
``scripts/trace-20261105.py`` (hyphen), which Python can NEVER import
(``import trace_20261105`` raises ModuleNotFoundError: the file on disk is
``trace-20261105.py``, not ``trace_20261105.py``). All four M5-3 patched
call points do ``import trace_20261105`` inside a guarded try/except, so
every emit silently vanished and ``state/m5-20261105/trace/`` stayed empty
(zero lines since the 2026-11-05 20:35 patch, verified 2026-11-05 21:0x CST).

This file is the importable name. Single source of truth REMAINS
``scripts/trace-20261105.py``: this shim loads it via importlib and
re-exports emit / classify / verify / self_test / main plus the path
constants. No logic is duplicated here, so the two files cannot drift.
CLI also works: ``python3 scripts/trace_20261105.py --verify/--test``.

Backups: new file, nothing overwritten. The hyphen file is untouched.
"""
import importlib.util
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_IMPL_PATH = os.path.join(_HERE, "trace-20261105.py")

_spec = importlib.util.spec_from_file_location("trace_20261105_impl", _IMPL_PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

emit = _mod.emit
classify = _mod.classify
verify = _mod.verify
self_test = _mod.self_test
main = _mod.main
HUB = _mod.HUB
TRACE_DIR = _mod.TRACE_DIR
TRACE = _mod.TRACE
CATEGORIES = _mod.CATEGORIES

if __name__ == "__main__":
    sys.exit(main())
