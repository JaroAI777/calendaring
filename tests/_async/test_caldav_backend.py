# Hand-written source; tools/generate_sync.py derives ../_sync/ from this file.
"""CalDAV backend behaviour the conformance suite cannot set up: servers misbehaving (roadmap 2.1)."""

from __future__ import annotations

import base64
import dataclasses
from collections.abc import AsyncIterator
from typing import Any

import pytest

from calendaring import (
    AsyncBackend,
    AsyncCollection,
    Component,
    ConfigurationError,
    ConflictError,
    NotFoundError,
    SyncTokenError,
    Task,
)

pytest.importorskip("caldav")

from calendaring._async.caldav import _name_of  # noqa: E402


@pytest.fixture
async def collection(xandikos_server: Any) -> AsyncIterator[AsyncCollection]:
    backend = await AsyncBackend.open(
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
    backend = await AsyncBackend.open("https://dav.example.com/", kind="caldav", token="s3cret")
    async with backend:
        assert backend.native.auth_type == "bearer"  # type: ignore[attr-defined]
        assert backend.native.password in ("s3cret", b"s3cret")  # type: ignore[attr-defined]  # bytes in sync mode


async def test_an_option_caldav_does_not_take_is_a_configuration_error() -> None:
    with pytest.raises(ConfigurationError, match="no_such_option"):
        await AsyncBackend.open("https://dav.example.com/", kind="caldav", no_such_option=1)


async def test_wire_bytes_are_sent_as_given(collection: AsyncCollection, monkeypatch: pytest.MonkeyPatch) -> None:
    # icalendar would fold this line.  Xandikos refolds what it stores, so what is checked is the PUT.
    client = collection._backend._dav.client  # type: ignore[attr-defined]
    put = client.put
    sent: list[bytes] = []

    async def spy(url: str, body: bytes, headers: Any = None) -> Any:
        sent.append(bytes(body))
        return await put(url, body, headers)

    monkeypatch.setattr(client, "put", spy)
    data = Task.new(summary="placeholder")
    payload = data.to_ical().replace(b"SUMMARY:placeholder", b"SUMMARY:" + b"x" * 100)
    written = await collection.add(data, wire=payload)
    written = await collection.save(written, wire=payload.replace(b"SUMMARY:", b"SUMMARY;X-SAVED=1:"))
    assert sent == [payload, payload.replace(b"SUMMARY:", b"SUMMARY;X-SAVED=1:")]
    assert written.native_id is not None
    reread = await collection.get_by_native_id(written.native_id)
    assert reread.wire is not None
    assert Task.from_ical(reread.wire) == reread


def weakened(collection: AsyncCollection, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every ETag read from the server weak, as GMX hands them out."""
    stored = collection._stored  # type: ignore[attr-defined]
    monkeypatch.setattr(
        collection, "_stored", lambda obj: dataclasses.replace(stored(obj), etag="W/" + stored(obj).etag)
    )


async def test_a_weak_etag_still_refuses_a_stale_write(
    collection: AsyncCollection, monkeypatch: pytest.MonkeyPatch
) -> None:
    stored = await collection.add_task(summary="weak")
    weakened(collection, monkeypatch)
    mine = await collection.get_by_native_id(stored.native_id or "")
    theirs = await collection.get_by_native_id(stored.native_id or "")
    assert (mine.etag or "").startswith("W/")
    theirs.component["SUMMARY"] = "theirs"
    await theirs.save()
    mine.component["SUMMARY"] = "mine"
    with pytest.raises(ConflictError):
        await mine.save()
    with pytest.raises(ConflictError):
        await collection.delete(mine)
    fresh = await collection.get_by_native_id(stored.native_id or "")
    fresh.component["SUMMARY"] = "fresh"
    await fresh.save()
    await collection.delete(await collection.get_by_native_id(stored.native_id or ""))
    with pytest.raises(NotFoundError):
        await collection.get_by_native_id(stored.native_id or "")


def test_a_native_id_with_a_slash_round_trips(collection: AsyncCollection) -> None:
    # Xandikos refuses such a name, so this checks the mapping, not a server.
    caldav_collection: Any = collection
    href = caldav_collection._href("a/b.ics")
    assert href == caldav_collection.url + "a%2Fb.ics"
    assert _name_of(href) == "a/b.ics"


async def test_a_token_from_elsewhere_is_refused(collection: AsyncCollection) -> None:
    caldav_collection: Any = collection
    foreign = base64.urlsafe_b64encode(b'{"collection": "other", "sync-token": "t1"}').decode()
    for token in (foreign, "not base64!", base64.urlsafe_b64encode(b"[]").decode()):
        with pytest.raises(SyncTokenError):
            caldav_collection._decode_server_token(token)
    assert caldav_collection._decode_server_token(caldav_collection._encode_server_token("t1")) == "t1"
