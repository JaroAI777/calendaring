"""The caldav shim's generated sync mode still matches the async one, method for method (roadmap 2.1)."""

from __future__ import annotations

import inspect

import pytest

pytest.importorskip("caldav")

from calendaring._async.dav import AsyncDAV  # noqa: E402
from calendaring._sync.dav import DAV  # noqa: E402


def methods(cls: type) -> dict[str, inspect.Signature]:
    return {
        name: inspect.signature(member)
        for name, member in vars(cls).items()
        if inspect.isfunction(member) and (not name.startswith("_") or name == "__init__")
    }


def test_both_modes_have_the_same_methods_and_signatures() -> None:
    assert methods(AsyncDAV) == methods(DAV)


@pytest.mark.parametrize("name", sorted(set(methods(AsyncDAV)) - {"__init__"}))
def test_only_the_async_mode_is_async(name: str) -> None:
    assert inspect.iscoroutinefunction(getattr(AsyncDAV, name))
    assert not inspect.iscoroutinefunction(getattr(DAV, name))
