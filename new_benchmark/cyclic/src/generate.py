#!/usr/bin/env python3
"""Generate this application through the shared, reproducible framework."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'packing/src'))
from nia_bench.cli import main
raise SystemExit(main(['generate', 'cyclic', *sys.argv[1:]]))
