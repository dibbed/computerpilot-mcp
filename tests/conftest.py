"""Test-suite isolation from a live ComputerPilot runtime.

Pytest is sometimes launched from inside a durable ComputerPilot job so that the
live server can observe the run. That job exports an internal resource capability
which authorizes nested claims against the live workflow store. Tests must never
inherit that authority: a fixture that intentionally creates an unresolved child
capacity would otherwise write it into the live database and permanently fence
the runtime.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

# Remove ambient runtime configuration before any test module imports core.config.
for _name in list(os.environ):
    if _name.startswith(("MCP_", "COMPUTERPILOT_")):
        os.environ.pop(_name, None)


@pytest.fixture(autouse=True)
def _isolate_resource_capability(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from core import resource_claims

    monkeypatch.delenv(resource_claims.RESOURCE_CAPABILITY_ENV, raising=False)
    token = resource_claims._RESOURCE_CAPABILITY.set(None)
    try:
        yield
    finally:
        resource_claims._RESOURCE_CAPABILITY.reset(token)
