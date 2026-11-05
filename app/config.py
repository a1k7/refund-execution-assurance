"""Runtime configuration. Values are read from environment variables with safe defaults."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    env: str = field(default_factory=lambda: _env("RAA_ENV", "dev"))
    log_level: str = field(default_factory=lambda: _env("RAA_LOG_LEVEL", "INFO"))
    db_path: str = field(default_factory=lambda: _env("RAA_DB_PATH", "./data/raa.db"))
    parmana_key_id: str = field(default_factory=lambda: _env("RAA_PARMANA_KEY_ID", "parmana-demo-key-1"))
    parmana_private_key_path: str = field(
        default_factory=lambda: _env("RAA_PARMANA_PRIVATE_KEY_PATH", "./data/parmana_private.pem")
    )
    parmana_trust_store_path: str = field(
        default_factory=lambda: _env("RAA_PARMANA_TRUST_STORE_PATH", "./data/parmana_trust.json")
    )
    receiver_default_behavior: str = field(
        default_factory=lambda: _env("RAA_RECEIVER_DEFAULT_BEHAVIOR", "normal")
    )

    def ensure_dirs(self) -> None:
        for p in (self.db_path, self.parmana_private_key_path, self.parmana_trust_store_path):
            Path(p).parent.mkdir(parents=True, exist_ok=True)


def load_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s