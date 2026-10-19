# -*- coding: utf-8 -*-
"""Put the project root (the folder that holds cboe/ and tests/) on sys.path for ``import cboe``."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
