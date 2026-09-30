from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_langfuse_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests hermetic: never talk to a real Langfuse project, even if the shell exports keys."""
    for name in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)
