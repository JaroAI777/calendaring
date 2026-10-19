"""The hand-written per-mode layer over caldav (roadmap 2.1, 0.2 *What is not settled*, 5).

caldav's domain objects are dual-mode: with an async client a method returns
a coroutine, with a sync one the value, and the annotation says
``X | Coroutine``.  Neither type checker can tell a missing ``await`` from a
used value through that, so the generated backend never calls caldav itself.
It calls ``AsyncDAV`` here, whose twin ``DAV`` the generator's replacement map
substitutes.  Each wrapper resolves the union once: ``_awaited`` /
``_returned`` also fail loudly if the client is of the wrong mode.

Writes go through the client's ``put()`` / ``delete()`` with the
preconditions this library needs (``If-None-Match: *`` to create,
``If-Match`` to update or delete), since ``CalendarObjectResource.save()``
sends no ``If-None-Match`` and bumps ``SEQUENCE``.  Those return the status
instead of raising, and ``_status()`` maps it.
"""

from __future__ import annotations

import inspect
from collections.abc import Coroutine, Iterator, Mapping
from contextlib import contextmanager
from typing import Any, TypeVar, cast

from caldav.aio import AsyncDAVClient
from caldav.calendarobjectresource import CalendarObjectResource
from caldav.collection import Calendar
from caldav.davclient import DAVClient
from caldav.elements.dav import DisplayName, GetEtag
from caldav.lib import error as dav_error
from caldav.lib.url import URL

from .errors import (
    AlreadyExistsError,
    AuthenticationError,
    AuthorizationError,
    BackendError,
    CalendaringError,
    ConflictError,
    NotFoundError,
    RateLimitError,
    SyncTokenError,
    TransportError,
)

__all__ = ["DAV", "AsyncDAV", "Calendar", "SyncResult", "WriteResult"]

T = TypeVar("T")

CONTENT_TYPE = {"Content-Type": "text/calendar; charset=utf-8"}

# A Depth: 1 PROPFIND for the ETag of every object in a calendar (RFC 4791 §8.2.1.1 lists hrefs this way).
ETAG_PROPFIND = (
    '<?xml version="1.0" encoding="utf-8"?><D:propfind xmlns:D="DAV:"><D:prop><D:getetag/></D:prop></D:propfind>'
)

VALID_SYNC_TOKEN = "{DAV:}valid-sync-token"  # RFC 6578 §3.2

# A sync-collection report: hrefs changed, hrefs removed, the new token (None if the server gave
# none), and whether it is incomplete (truncated beyond what caldav itself follows, RFC 6578 §3.6).
SyncResult = tuple[list[str], list[str], str | None, bool]

WriteResult = tuple[int, str | None]  # HTTP status, and the ETag the server returned, if any


async def _awaited(value: T | Coroutine[Any, Any, T]) -> T:
    if not inspect.iscoroutine(value):
        raise TypeError(f"caldav returned {type(value).__name__} where an async client returns a coroutine")
    return await cast("Coroutine[Any, Any, T]", value)


def _returned(value: T | Coroutine[Any, Any, T]) -> T:
    if inspect.iscoroutine(value):
        value.close()
        raise TypeError("caldav returned a coroutine to a sync client")
    return cast(T, value)


@contextmanager
def translated() -> Iterator[None]:
    """caldav's exceptions as this library's (API_DESIGN.md §6), with caldav's as ``__cause__``."""
    try:
        yield
    except CalendaringError:
        raise
    except dav_error.DAVError as exc:
        raise _translate(exc) from exc
    except OSError as exc:
        raise TransportError(str(exc)) from exc


def _translate(exc: dav_error.DAVError) -> CalendaringError:
    message = str(exc)
    if VALID_SYNC_TOKEN in getattr(exc, "preconditions", ()):
        return SyncTokenError(f"the server no longer honours the sync token: {message}")
    if isinstance(exc, dav_error.NotFoundError):
        return NotFoundError(message)
    if isinstance(exc, (dav_error.ETagMismatchError, dav_error.ScheduleTagMismatchError)):
        return ConflictError(message)
    if isinstance(exc, dav_error.AuthorizationError):
        status = getattr(exc, "status", None) or getattr(getattr(exc, "response", None), "status", None)
        return (AuthenticationError if status == 401 else AuthorizationError)(message)
    if isinstance(exc, dav_error.RateLimitError):
        return RateLimitError(message, retry_after=getattr(exc, "retry_after", None))
    return BackendError(message)


def _status(status: int, what: str, *, creating: bool = False, body: bytes | str = b"") -> None:
    """Raise for a write's status; 2xx passes."""
    if 200 <= status < 300:
        return
    if creating and status == 412:
        raise AlreadyExistsError(f"{what}: the name is taken (412)", taken="native_id")
    if creating and status in (403, 409) and "no-uid-conflict" in (body if isinstance(body, str) else body.decode()):
        raise AlreadyExistsError(f"{what}: another object holds the UID (RFC 4791 §5.3.2.1)", taken="uid")
    if status == 412:
        raise ConflictError(f"{what}: changed since it was read (412)")
    if status == 404:
        raise NotFoundError(f"{what}: not found (404)")
    if status == 401:
        raise AuthenticationError(f"{what}: 401")
    if status == 403:
        raise AuthorizationError(f"{what}: 403")
    if status == 429:
        raise RateLimitError(f"{what}: 429")
    raise BackendError(f"{what}: HTTP {status}")


def _write_headers(*, create: bool, etag: str | None) -> dict[str, str]:
    headers = dict(CONTENT_TYPE)
    if create:
        headers["If-None-Match"] = "*"
    elif etag is not None:
        headers["If-Match"] = etag
    return headers


def _body(response: Any) -> bytes | str:
    return getattr(response, "_raw", b"") or b""  # DAVResponse keeps the body only privately


def _etags(response: Any, url: str) -> dict[str, str]:
    """``{href: etag}`` from an ``ETAG_PROPFIND`` response, without the calendar itself."""
    out = {}
    for href, props in response.expand_simple_props([GetEtag()]).items():
        etag = props.get(GetEtag.tag)
        if etag and str(URL.objectify(href).path).rstrip("/") != str(URL.objectify(url).path).rstrip("/"):
            out[str(href)] = etag
    return out


def _sync_result(found: Any) -> SyncResult:
    deleted = {str(u) for u in found.deleted_urls}
    changed = [str(o.url) for o in found.objects if str(o.url) not in deleted]
    token = None if found.sync_token is None else str(found.sync_token)
    return changed, sorted(deleted), token, bool(found.truncated)


def _etag(headers: Mapping[str, str]) -> str | None:
    """The ETag of a write's response, if the server sent a strong one (RFC 4791 §5.3.4)."""
    for name, value in headers.items():
        if name.lower() == "etag":
            return None if value.startswith("W/") else value
    return None


class AsyncDAV:
    """caldav, async, typed honestly."""

    def __init__(self, url: str, **options: Any) -> None:
        with translated():
            self.client = AsyncDAVClient(url=url, **options)

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
            return _etags(await self.client.propfind(url, body=ETAG_PROPFIND, depth=1), url)

    async def sync(self, calendar: Calendar, token: str | None) -> SyncResult:
        with translated():
            found = calendar.get_objects_by_sync_token(token, load_objects=False, disable_fallback=True)
            return _sync_result(await _awaited(found))

    async def put(self, url: str, data: bytes, *, create: bool, etag: str | None) -> WriteResult:
        with translated():
            response = await self.client.put(url, data.decode(), _write_headers(create=create, etag=etag))
        _status(response.status, f"PUT {url}", creating=create, body=_body(response))
        return response.status, _etag(response.headers)

    async def delete(self, url: str, *, etag: str | None) -> None:
        with translated():
            response = await self.client.delete(url, None if etag is None else {"If-Match": etag})
        _status(response.status, f"DELETE {url}")


class DAV:
    """caldav, sync, typed honestly: ``AsyncDAV``'s twin, method for method."""

    def __init__(self, url: str, **options: Any) -> None:
        with translated():
            self.client = DAVClient(url=url, **options)

    def close(self) -> None:
        with translated():
            self.client.close()

    def calendars(self) -> list[Calendar]:
        with translated():
            principal = self.client.get_principal()
            return _returned(principal.get_calendars())

    def make_calendar(self, name: str, components: list[str]) -> Calendar:
        with translated():
            principal = self.client.get_principal()
            return _returned(principal.make_calendar(name=name, supported_calendar_component_set=components))

    def delete_calendar(self, calendar: Calendar) -> None:
        with translated():
            _returned(calendar.delete())  # type: ignore[no-untyped-call]  # caldav leaves delete() unannotated

    def supported_components(self, calendar: Calendar) -> list[str]:
        with translated():
            return [str(c) for c in _returned(calendar.get_supported_components())]

    def display_name(self, calendar: Calendar) -> str | None:
        with translated():
            return _returned(calendar.get_display_name())

    def set_display_name(self, calendar: Calendar, name: str) -> None:
        with translated():
            _returned(calendar.set_properties([DisplayName(name)]))

    def search(self, calendar: Calendar, **kwargs: Any) -> list[CalendarObjectResource]:
        with translated():
            return list(_returned(calendar.search(**kwargs)))

    def by_uid(self, calendar: Calendar, uid: str) -> CalendarObjectResource:
        with translated():
            return _returned(calendar.get_object_by_uid(uid))

    def multiget(self, calendar: Calendar, urls: list[str]) -> list[CalendarObjectResource]:
        with translated():
            return list(_returned(calendar.multiget([URL.objectify(u) for u in urls])))

    def etags(self, url: str) -> dict[str, str]:
        with translated():
            return _etags(self.client.propfind(url, ETAG_PROPFIND, depth=1), url)

    def sync(self, calendar: Calendar, token: str | None) -> SyncResult:
        with translated():
            found = calendar.get_objects_by_sync_token(token, load_objects=False, disable_fallback=True)
            return _sync_result(_returned(found))

    def put(self, url: str, data: bytes, *, create: bool, etag: str | None) -> WriteResult:
        with translated():
            response = self.client.put(url, data.decode(), _write_headers(create=create, etag=etag))
        _status(response.status, f"PUT {url}", creating=create, body=_body(response))
        return response.status, _etag(response.headers)

    def delete(self, url: str, *, etag: str | None) -> None:
        with translated():
            response = self.client.delete(url, None if etag is None else {"If-Match": etag})
        _status(response.status, f"DELETE {url}")
