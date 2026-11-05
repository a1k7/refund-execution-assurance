"""Shared pytest fixtures. Each test gets a fresh, isolated ``World``."""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.fixtures.world import World, build_world


@pytest.fixture
def world(tmp_path: Path) -> World:
    w = build_world(tmp_path)
    try:
        yield w
    finally:
        w.db.close()