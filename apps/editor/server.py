#!/usr/bin/env python3
"""
Compat entry — Product B (HWP Editing Assistant).

Preferred:  python3 apps/editor/server.py
Also works: python3 HWP_v2/server.py
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_V2 = _ROOT / "HWP_v2"
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
if str(_V2) not in sys.path:
    sys.path.insert(0, str(_V2))

runpy.run_path(str(_V2 / "server.py"), run_name="__main__")
