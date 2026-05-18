from __future__ import annotations

from pathlib import Path

import pytest

from six2one.storage import create_storage
from tests.factories import FakeE621


@pytest.fixture
def store(tmp_path: Path):
    storage = create_storage(tmp_path / "six2one.sqlite")
    try:
        yield storage
    finally:
        storage.close()


@pytest.fixture
def fake_e621() -> FakeE621:
    return FakeE621()
