"""The mode-free half of the bridge to caldav (roadmap 2.1, 0.2 *What is not settled*, 5).

caldav's domain objects are dual-mode: with an async client a method returns
a coroutine, with a sync one the value, and the annotation says
``X | Coroutine``.  Neither type checker can tell a missing ``await`` from a
used value through that, so the backend never calls caldav itself.  It calls
``AsyncDAV`` (``_async/dav.py``), whose sync twin ``DAV`` is generated like
the rest: ``_awaited`` becomes ``_returned`` and ``AsyncDAVClient`` becomes
``DAVClient``.  Each wrapper resolves the union once, and fails loudly if the
client is of the wrong mode.  What both modes share lives here.

Writes go through caldav's own ``save()`` and ``delete()``: ``save(no_overwrite=True)``
sends ``If-None-Match: *`` where the server honours it (caldav 3.6), and an
update carries the ETag it was read with.  ``SEQUENCE`` is left as given.
"""

from __future__ import annotations

import inspect
from collections.abc import Coroutine, Iterator
from contextlib import contextmanager
from typing import Any, TypeVar, cast

from caldav.aio import AsyncDAVClient
from caldav.calendarobjectresource import CalendarObjectResource, Event, Journal, Todo
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

__all__ = [
    "ETAG_PROPFIND",
    "URL",
    "AsyncDAVClient",
    "Calendar",
    "CalendarObjectResource",
    "DAVClient",
    "DisplayName",
    "SyncResult",
    "creating",
    "missing_if",
    "translated",
]

T = TypeVar("T")


# A Depth: 1 PROPFIND for the ETag of every object in a calendar (RFC 4791 §8.2.1.1 lists hrefs this way).
ETAG_PROPFIND = (
    '<?xml version="1.0" encoding="utf-8"?><D:propfind xmlns:D="DAV:"><D:prop><D:getetag/></D:prop></D:propfind>'
)

VALID_SYNC_TOKEN = "{DAV:}valid-sync-token"  # RFC 6578 §3.2

# A sync-collection report: hrefs changed, hrefs removed, the new token (None if the server gave
# none), and whether it is incomplete (truncated beyond what caldav itself follows, RFC 6578 §3.6).
SyncResult = tuple[list[str], list[str], str | None, bool]


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
        return RateLimitError(message, retry_after=getattr(exc, "retry_after_seconds", None))
    return BackendError(message)


# The caldav class for each component a calendar object holds.
RESOURCE: dict[str, type[CalendarObjectResource]] = {"VEVENT": Event, "VTODO": Todo, "VJOURNAL": Journal}


def _resource(
    client: Any,
    calendar: Calendar,
    url: str,
    data: bytes | None = None,
    kind: str | None = None,
    etag: str | None = None,
) -> CalendarObjectResource:
    """A caldav object for ``url``, holding ``data`` and the ETag it was read with, ready to save or delete."""
    props = {} if etag is None else {GetEtag.tag: etag}
    cls = RESOURCE.get(kind or "", CalendarObjectResource)
    return cls(client=client, url=url, data=None if data is None else data.decode(), parent=calendar, props=props)


def _strong(etag: str | None) -> str | None:
    """The ETag a write left cached, if it is strong (RFC 4791 §5.3.4: a changed object gets none)."""
    return None if etag is None or etag.startswith("W/") else etag


@contextmanager
def creating() -> Iterator[None]:
    """caldav's refusals of a create-only save, as ``AlreadyExistsError``."""
    try:
        yield
    except dav_error.UidConflictError as exc:  # another object holds the UID (RFC 4791 §5.3.2.1)
        raise AlreadyExistsError(str(exc), taken="uid") from exc
    except dav_error.ConsistencyError as exc:  # If-None-Match failed, or caldav's search found it
        raise AlreadyExistsError(str(exc), taken="native_id") from exc


@contextmanager
def missing_if(any_version: bool) -> Iterator[None]:
    """With ``If-Match: *``, a failed precondition means there was nothing there (RFC 9110 §13.1.1)."""
    try:
        yield
    except dav_error.ETagMismatchError as exc:
        if not any_version:
            raise
        raise NotFoundError(str(exc)) from exc


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
