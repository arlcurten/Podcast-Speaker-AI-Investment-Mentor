from __future__ import annotations

import runpy
import sys
from pathlib import Path


PACKAGE_DIR = Path(__file__).resolve().parent
if str(PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGE_DIR))

runpy.run_path(str(PACKAGE_DIR / "main.py"), run_name="__main__")
