"""pytest configuration — ensures the backend package is importable."""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure the backend directory is on sys.path so that `app` can be imported
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
