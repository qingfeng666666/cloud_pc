from __future__ import annotations

import os
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_SHARED_DIR = os.path.normpath(os.path.join(_THIS_DIR, "..", "..", "shared_components"))
if _SHARED_DIR not in sys.path:
    sys.path.insert(0, _SHARED_DIR)
