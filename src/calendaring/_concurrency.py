"""The hand-written per-mode layer under ``_async/`` (roadmap 0.2, p4).

What has no token-for-token sync equivalent lives here, once per mode, and the
generator's replacement map turns the async name into the sync one
(``agather`` -> ``gather``).  ``asyncio`` must not appear in ``_async/``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import ParamSpec, TypeVar

from .errors import CalendaringError

T = TypeVar("T")
P = ParamSpec("P")


async def agather(calls: Sequence[Callable[[], Awaitable[T]]]) -> list[T | CalendaringError]:
    """Run the calls concurrently.  A ``CalendaringError`` is returned in its slot, anything else propagates."""

    async def one(call: Callable[[], Awaitable[T]]) -> T | CalendaringError:
        try:
            return await call()
        except CalendaringError as exc:
            return exc

    async with asyncio.TaskGroup() as group:
        tasks = [group.create_task(one(call)) for call in calls]
    return [task.result() for task in tasks]


def gather(calls: Sequence[Callable[[], T]]) -> list[T | CalendaringError]:
    """The sync twin of ``agather``: the calls in turn."""
    out: list[T | CalendaringError] = []
    for call in calls:
        try:
            out.append(call())
        except CalendaringError as exc:
            out.append(exc)
    return out


async def ato_thread(call: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
    """Blocking I/O (the files backend's) in a worker thread, so an event loop is not held up."""
    return await asyncio.to_thread(call, *args, **kwargs)


def to_thread(call: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
    """The sync twin of ``ato_thread``: just the call."""
    return call(*args, **kwargs)
