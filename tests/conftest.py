from __future__ import annotations

import pytest

from app.service import open115
from app.service.token_store import token_store


@pytest.fixture(autouse=True)
def clear_token_store():
    token_store.clear()
    open115.clear_token_cache()
    yield
    token_store.clear()
    open115.clear_token_cache()
