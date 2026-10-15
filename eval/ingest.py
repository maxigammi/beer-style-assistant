"""Индексация базы знаний: тонкая обёртка над боевым scripts/ingest.py (та же логика, что у бота)."""

import runpy
import sys
from pathlib import Path

script = Path(__file__).resolve().parents[1] / "scripts" / "ingest.py"
sys.argv[0] = str(script)
runpy.run_path(str(script), run_name="__main__")
