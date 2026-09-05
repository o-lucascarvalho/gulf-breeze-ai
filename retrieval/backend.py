from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"


def ensure_backend_path() -> None:
    backend = str(BACKEND_DIR)
    if backend not in sys.path:
        sys.path.insert(0, backend)
