"""Initialize the SQLite database (schema is idempotent)."""
from __future__ import annotations

import argparse

from app.config import load_settings
from app.persistence.db import Database


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=None)
    args = parser.parse_args()
    settings = load_settings()
    path = args.db or settings.db_path
    Database(path)
    print(f"initialized database at {path}")


if __name__ == "__main__":
    main()