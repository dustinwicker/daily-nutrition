#!/usr/bin/env python3
"""Run google_cloud/read_food_sheet.py from this folder (same env vars)."""
import subprocess
import sys
from pathlib import Path

_script = Path(__file__).resolve().parent.parent / "google_cloud" / "read_food_sheet.py"
raise SystemExit(subprocess.call([sys.executable, str(_script)] + sys.argv[1:]))
