# Hand-written source; tools/generate_sync.py derives ../_sync/workspace.py from this file.
"""A set of backends, and operations that fan out across them (API_DESIGN.md §2.1).

One unreachable server must not blank an agenda, so fan-out returns partial
results with the failures beside them, never a plain raise.  Async mode runs
the calls concurrently; sync mode runs them in turn (``_concurrency.py``).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from functools import partial
from pathlib import Path
from typing import Any, Self

from icalendar_searcher import Searcher

from .._concurrency import agather
from ..errors import AmbiguousError, CalendaringError, NotFoundError
from ..loss import LossPolicy
from ..types import MultiResult
from .backend import AsyncBackend
from .collection import AsyncCollection
from .items import AsyncItem

__all__ = ["AsyncWorkspace"]


class AsyncWorkspace:
    backends: Sequence[AsyncBackend]
    loss: LossPolicy | None
    verify: bool | None

    @classmethod
    def from_config(cls, path: str | Path | None = None, *, section: str | None = None) -> Self:
        raise NotImplementedError("the configuration file is roadmap 1.4")

    def __init__(
        self, backends: Iterable[AsyncBackend] = (), *, loss: LossPolicy | None = None, verify: bool | None = None
    ) -> None:
        """``loss`` and ``verify`` are the defaults for backends that set none of their own."""
        self.backends = list(backends)
        self.loss = loss
        self.verify = verify
        for backend in self.backends:
            backend._workspace = self

    async def collections(self) -> MultiResult[AsyncCollection]:
        """Every backend's collections; failures keyed by backend id."""
        results = await agather([b.collections for b in self.backends])
        out: MultiResult[AsyncCollection] = MultiResult()
        errors: dict[str, CalendaringError] = {}
        for backend, result in zip(self.backends, results, strict=True):
            if isinstance(result, CalendaringError):
                errors[backend.id] = result
            else:
                out.items.extend(result)
        out.errors = errors
        return out

    async def collection(self, name_or_id: str) -> AsyncCollection:
        """By id or name across all backends; ``NotFoundError`` or ``AmbiguousError``."""
        listed = await self.collections()
        matches = [c for c in listed.items if c.id == name_or_id] or [c for c in listed.items if c.name == name_or_id]
        if len(matches) > 1:
            raise AmbiguousError(f"{len(matches)} collections match {name_or_id!r}")
        if not matches:
            raise NotFoundError(f"no collection {name_or_id!r}") from (
                ExceptionGroup("unreachable backends", list(listed.errors.values())) if listed.errors else None
            )
        return matches[0]

    async def search(self, searcher: Searcher | None = None, **filters: Any) -> MultiResult[AsyncItem]:
        """Search every collection; failures keyed by collection id (or backend id, if it could not list)."""
        listed = await self.collections()
        if searcher is None:
            searcher = Searcher(**filters)
        elif filters:
            raise TypeError("give a Searcher or keyword filters, not both")
        results = await agather([partial(c.search, searcher) for c in listed.items])
        out: MultiResult[AsyncItem] = MultiResult()
        errors: dict[str, CalendaringError] = dict(listed.errors)
        for collection, result in zip(listed.items, results, strict=True):
            if isinstance(result, CalendaringError):
                errors[collection.id] = result
            else:
                out.items.extend(result)
        out.errors = errors
        return out

    async def close(self) -> None:
        for backend in self.backends:
            await backend.close()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()
