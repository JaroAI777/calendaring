# Hand-written source; tools/generate_sync.py derives ../_sync/dav.py from this file.
"""The per-mode layer over caldav (roadmap 2.1): the only code that calls it.  See ``_caldav_io.py``."""

from __future__ import annotations

from typing import Any, cast

from .._caldav_io import (
    ETAG_PROPFIND,
    URL,
    AsyncDAVClient,
    Calendar,
    CalendarObjectResource,
    DisplayName,
    SyncResult,
    _awaited,
    _etags,
    _raw,
    _resource,
    _strong,
    _sync_result,
    creating,
    missing_if,
    translated,
)
from ..errors import ConfigurationError

__all__ = ["AsyncDAV"]


class AsyncDAV:
    """caldav, typed honestly: each call resolves caldav's ``X | Coroutine`` once."""

    def __init__(self, url: str, **options: Any) -> None:
        with translated():
            try:
                self.client = AsyncDAVClient(url=url, **options)
            except TypeError as exc:  # an option the client does not take
                raise ConfigurationError(f"caldav: {exc}") from exc

    async def close(self) -> None:
        with translated():
            await self.client.close()

    async def calendars(self) -> list[Calendar]:
        with translated():
            principal = await self.client.get_principal()
            return await _awaited(principal.get_calendars())

    async def make_calendar(self, name: str, components: list[str]) -> Calendar:
        with translated():
            principal = await self.client.get_principal()
            return await _awaited(principal.make_calendar(name=name, supported_calendar_component_set=components))

    async def delete_calendar(self, calendar: Calendar) -> None:
        with translated():
            await _awaited(calendar.delete())  # type: ignore[no-untyped-call]  # caldav leaves delete() unannotated

    async def supported_components(self, calendar: Calendar) -> list[str]:
        with translated():
            return [str(c) for c in await _awaited(calendar.get_supported_components())]

    async def display_name(self, calendar: Calendar) -> str | None:
        with translated():
            return await _awaited(calendar.get_display_name())

    async def set_display_name(self, calendar: Calendar, name: str) -> None:
        with translated():
            await _awaited(calendar.set_properties([DisplayName(name)]))

    async def search(self, calendar: Calendar, **kwargs: Any) -> list[CalendarObjectResource]:
        with translated():
            return list(await _awaited(calendar.search(**kwargs)))

    async def by_uid(self, calendar: Calendar, uid: str) -> CalendarObjectResource:
        with translated():
            return await _awaited(calendar.get_object_by_uid(uid))

    async def multiget(self, calendar: Calendar, urls: list[str]) -> list[CalendarObjectResource]:
        # multiget() is annotated Iterable[_CC], but returns a coroutine to an async client.
        with translated():
            found: Any = calendar.multiget([URL.objectify(u) for u in urls])
            return list(await _awaited(cast(Any, found)))

    async def etags(self, url: str) -> dict[str, str]:
        with translated():
            # Positional: the sync client's second parameter is props=, which also takes an XML body.
            return _etags(await self.client.propfind(url, ETAG_PROPFIND, 1), url)

    async def sync(self, calendar: Calendar, token: str | None) -> SyncResult:
        with translated():
            found = calendar.get_objects_by_sync_token(token, load_objects=False, disable_fallback=True)
            return _sync_result(await _awaited(found))

    async def create(self, calendar: Calendar, url: str, data: bytes, kind: str, *, raw: bool = False) -> str | None:
        """Store a new object at ``url``: ``AlreadyExistsError`` if the name or the UID is taken.  Its ETag, if strong.

        With ``raw``, ``data`` is sent byte for byte, not re-serialised by caldav.
        """
        obj = _resource(self.client, calendar, url, data, kind)
        with translated(), creating():
            await _awaited(
                obj.save(no_overwrite=True, increase_seqno=False, only_this_recurrence=False, **_raw(data, raw))
            )
        return _strong(obj.etag)

    async def update(
        self, calendar: Calendar, url: str, data: bytes, kind: str, etag: str | None, *, raw: bool = False
    ) -> str | None:
        """Replace the object at ``url``, if it still has ``etag`` (any version without one).  Its new ETag, if strong."""
        obj = _resource(self.client, calendar, url, data, kind, etag)
        with translated():
            await _awaited(
                obj.save(increase_seqno=False, only_this_recurrence=False, precondition="etag", **_raw(data, raw))
            )
        return _strong(obj.etag)

    async def delete(self, calendar: Calendar, url: str, etag: str | None) -> None:
        """Remove the object at ``url``: ``NotFoundError`` if it is gone.  With ``etag``, ``ConflictError`` if it changed."""
        obj = _resource(self.client, calendar, url)
        with translated(), missing_if(etag is None):
            # caldav counts a 404 as success only without a precondition; If-Match: * keeps it an error.
            await _awaited(obj.delete(if_match=etag if etag is not None else "*"))
