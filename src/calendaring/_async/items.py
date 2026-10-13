# Hand-written source; tools/generate_sync.py derives ../_sync/items.py from this file.
"""Bound items: the mode-free data plus the collection it came from (API_DESIGN.md §1.1, A1).

Each I/O method calls the collection method of the same name and then
updates the item in place, so ``task.save(); ...; task.save()`` works
without a stale etag.  The collection methods are the primitive.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal, NoReturn, Self

import icalendar

from ..errors import UnsupportedError
from ..items import Event, Item, Journal, Task
from ..loss import LossPolicy
from ..types import Scope

if TYPE_CHECKING:
    from .collection import AsyncCollection

__all__ = ["AsyncEvent", "AsyncItem", "AsyncJournal", "AsyncTask"]


class AsyncItem(Item):
    collections: tuple[AsyncCollection, ...]  # exactly one for now (Q9)

    def __init__(self, calendar: icalendar.Calendar, *, collections: Sequence[AsyncCollection], **state: Any) -> None:
        super().__init__(calendar, **state)
        if len(collections) != 1:
            raise UnsupportedError(None, "an item is bound to exactly one collection for now (API_DESIGN.md Q9)")
        self.collections = tuple(collections)
        self.collection_id = self.collections[0].id

    @property
    def collection(self) -> AsyncCollection:
        return self.collections[0]

    def _take(self, other: Item) -> None:
        """Become ``other``: its data, its etag and identity, its collections."""
        self.__dict__.update(other.__dict__)

    async def save(
        self,
        *,
        overwrite: bool = False,
        scope: Scope = Scope.THIS,
        loss: LossPolicy | None = None,
        verify: bool | None = None,
    ) -> None:
        self._take(await self.collection.save(self, overwrite=overwrite, scope=scope, loss=loss, verify=verify))

    async def delete(self) -> None:
        await self.collection.delete(self)

    async def reload(self) -> None:
        self._take(await self.collection.reload(self))

    async def relatives(self, reltype: str | None = None) -> list[AsyncItem]:
        return await self.collection.relatives(self, reltype)

    def __reduce_ex__(self, protocol: Any) -> NoReturn:
        raise TypeError("a bound item carries a live session; pickle item.as_data() instead")

    def __deepcopy__(self, memo: dict[int, Any]) -> Self:
        return self.copy()


class AsyncTask(Task, AsyncItem):
    async def complete(
        self,
        at: datetime | None = None,
        mode: Literal["safe", "this_and_future"] = "safe",
        *,
        loss: LossPolicy | None = None,
        verify: bool | None = None,
    ) -> None:
        self._take(await self.collection.complete(self, at, mode, loss=loss, verify=verify))

    async def uncomplete(self, *, loss: LossPolicy | None = None, verify: bool | None = None) -> None:
        self._take(await self.collection.uncomplete(self, loss=loss, verify=verify))


class AsyncEvent(Event, AsyncItem):
    pass


class AsyncJournal(Journal, AsyncItem):
    pass


BOUND: dict[type[Item], type[AsyncItem]] = {Task: AsyncTask, Event: AsyncEvent, Journal: AsyncJournal}
