#!/usr/bin/env python
"""Bootstrap: init DB, seed, then run the app."""
from __future__ import annotations
import os
from pathlib import Path

# Ensure the app package is importable
BASE = Path(__file__).resolve().parent
os.environ.setdefault("FLASK_APP", str(BASE / "app" / "__init__.py"))

import sys
sys.path.insert(0, str(BASE))

from manage import cli

if __name__ == "__main__":
    # Idempotent first-run: init db + seed, then start server
    try:
        from app.db import init_db, table_exists
        if not table_exists("scenarios"):
            init_db()
            from app.seed import seed
            seed()
            print("Database initialised and seeded.")
    except Exception as e:
        print(f"Bootstrap warning (non-fatal): {e}", file=sys.stderr)
    cli()
