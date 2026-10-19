# Hand-written source; tools/generate_sync.py derives ../_sync/ from this file.
"""CalDAV backend behaviour the conformance suite cannot set up: servers misbehaving (roadmap 2.1)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from calendaring import AsyncBackend, AsyncCollection, Component, ConfigurationError, SyncTokenError

pytest.importorskip("caldav")


@pytest.fixture
async def collection(xandikos_server: Any) -> AsyncIterator[AsyncCollection]:
    backend = await AsyncBackend.connect(
        xandikos_server.url,
        kind="caldav",
        username=xandikos_server.username,
        password=xandikos_server.password,
        features=xandikos_server.config["features"],
    )
    async with backend:
        made = await backend.create_collection("backend-tests", components=(Component.TASK,))
        yield made
        await made.delete_collection()


async def incomplete(*args: Any) -> tuple[list[str], list[str], str | None, bool]:
    """A sync report caldav gave up on: no token, truncated (Stalwart's initial sync)."""
    return [], [], None, True


async def test_an_incomplete_initial_sync_falls_back_to_listing_etags(
    collection: AsyncCollection, monkeypatch: pytest.MonkeyPatch
) -> None:
    stored = await collection.add_task(summary="listed anyway")
    monkeypatch.setattr(collection._backend._dav, "sync", incomplete)  # type: ignore[attr-defined]
    result = await collection.changes()
    assert [i.native_id for i in result.changed] == [stored.native_id]
    later = await collection.add_task(summary="later")
    assert [i.native_id for i in (await collection.changes(result.token)).changed] == [later.native_id]


async def test_an_incomplete_incremental_sync_asks_to_start_again(
    collection: AsyncCollection, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = (await collection.changes()).token
    monkeypatch.setattr(collection._backend._dav, "sync", incomplete)  # type: ignore[attr-defined]
    with pytest.raises(SyncTokenError):
        await collection.changes(token)


async def test_a_token_is_bearer_auth() -> None:
    backend = await AsyncBackend.connect("https://dav.example.com/", kind="caldav", token="s3cret")
    async with backend:
        assert backend.native.auth_type == "bearer"  # type: ignore[attr-defined]
        assert backend.native.password in ("s3cret", b"s3cret")  # type: ignore[attr-defined]  # bytes in sync mode


async def test_an_option_caldav_does_not_take_is_a_configuration_error() -> None:
    with pytest.raises(ConfigurationError, match="no_such_option"):
        await AsyncBackend.connect("https://dav.example.com/", kind="caldav", no_such_option=1)
