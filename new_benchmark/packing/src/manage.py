#!/usr/bin/env python3
"""Run from any directory; only Python's standard library is required."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from nia_bench.cli import main
raise SystemExit(main())
