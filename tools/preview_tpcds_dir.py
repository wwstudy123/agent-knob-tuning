#!/usr/bin/env python3
"""Preview which tpcds/ queries will run under current config."""
from __future__ import annotations

import configparser
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "configuration recommender"))
os.chdir(ROOT)
os.environ.setdefault("AGENTTUNE_CONFIG", str(ROOT / "config.ini"))

# Import after chdir/env so DB_client reads the right config.
import DB_client as client  # noqa: E402

entries = client._workload_entries(client.config_parser["workload analyzer"]["workload_file"])
print(f"executable_statements={len(entries)}")
print(f"skip_queries={sorted(client.TPCDS_SKIP_QUERIES)}")
for e in entries:
    head = " ".join(e["sql"].split())[:80]
    print(f"{e['source']}: {head}")
