# Hand-written source; tools/generate_sync.py derives ../_sync/collection.py from this file.
"""The collection ABC (API_DESIGN.md §2.3).

The public methods are written once, here: the capability checks before any
I/O, the loss policy, client-side filtering, binding and ``verify``.  A
backend implements the primitives below them (the ``_``-prefixed abstract
methods, which take and return ``StoredObject``) and inherits the rest.
"""

from __future__ import annotations

import base64
import binascii
import copy
import json
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Generator, Iterable, Mapping, Sequence
from contextlib import contextmanager, suppress
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal, overload

import icalendar
import recurring_ical_events
from icalendar_searcher import Searcher

from .._mapping import Change, differences, plan_calendar_losses, plan_losses
from ..capabilities import BACKEND_FEATURES, Capabilities, Feature, Support
from ..errors import (
    CalendaringError,
    ConflictError,
    InvalidDataError,
    NotFoundError,
    PartialResultError,
    SyncTokenError,
    UnsupportedError,
    VerificationError,
)
from ..items import Event, Item, Journal, Task, _items_of, _parse, split_stored
from ..loss import RAISE, LossPolicy
from ..types import BrokenObject, ChangeSet, Component, Scope, StoredObject, SyncToken
from .items import BOUND, AsyncEvent, AsyncItem, AsyncJournal, AsyncTask

if TYPE_CHECKING:
    from .backend import AsyncBackend

__all__ = ["AsyncCollection"]

COMPONENT_FEATURE = {
    Component.EVENT: Feature.COMPONENT_EVENT,
    Component.TASK: Feature.COMPONENT_TASK,
    Component.JOURNAL: Feature.COMPONENT_JOURNAL,
}


def _occurs_at(item: Item) -> datetime:
    """Where an occurrence sorts: its ``RECURRENCE-ID`` as an aware instant (floating counts as UTC)."""
    rid = item.component.get("RECURRENCE-ID")
    if rid is None:
        return datetime.min.replace(tzinfo=UTC)
    value = rid.dt
    if not isinstance(value, datetime):
        value = datetime(value.year, value.month, value.day)
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class AsyncCollection(ABC):
    """What holds items: a calendar, a task list, a directory, a repository."""

    id: str
    backend_id: str
    name: str | None
    color: str | None
    components: frozenset[Component]
    capabilities: Capabilities
    native: object

    def __init__(
        self,
        backend: AsyncBackend,
        *,
        id: str,
        components: Iterable[Component],
        capabilities: Capabilities,
        name: str | None = None,
        color: str | None = None,
        native: object = None,
    ) -> None:
        self._backend = backend
        self.id = id
        self.backend_id = backend.id
        self.name = name
        self.color = color
        self.components = frozenset(components)
        self.capabilities = capabilities
        self.native = native

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.backend_id}/{self.id}>"

    # === primitives a backend implements ===============================

    @abstractmethod
    async def _query(self, searcher: Searcher | None) -> Sequence[StoredObject | BrokenObject]:
        """The stored objects that may match: over-matching is fine, under-matching is a bug (A3).

        An object that does not parse is returned as a ``BrokenObject``, so
        one bad object leaves the rest readable.  The same goes for every
        primitive that lists: ``_iter_query``, ``_fetch_many``, ``_changes``.
        """

    @abstractmethod
    async def _fetch(self, native_id: str) -> StoredObject:
        """One stored object; ``NotFoundError`` if there is none."""

    @abstractmethod
    async def _create(self, calendar: icalendar.Calendar, *, native_id: str | None, wire: bytes | None) -> StoredObject:
        """Store a new object; ``AlreadyExistsError`` on a clash.

        ``wire``, when given, is what to send instead of serialising
        ``calendar``.  Return what was sent, with the identity the backend
        assigned (``native_id``, ``etag``, and the ``UID`` where it assigns one).
        """

    @abstractmethod
    async def _update(
        self, native_id: str, calendar: icalendar.Calendar, *, etag: str | None, wire: bytes | None
    ) -> StoredObject:
        """Replace a stored object; ``ConflictError`` if ``etag`` is given and no longer matches."""

    @abstractmethod
    async def _delete(self, native_id: str, *, etag: str | None) -> None:
        """Remove a stored object; ``ConflictError`` if ``etag`` is given and no longer matches."""

    async def _iter_query(self, searcher: Searcher | None) -> AsyncIterator[StoredObject | BrokenObject]:
        """Override to stream pages; the default yields what ``_query`` returns."""
        for stored in await self._query(searcher):
            yield stored

    async def _find_uid(self, uid: str) -> StoredObject:
        """Override where the backend can look a UID up; the default reads everything.

        ``PartialResultError`` if the UID is not found but some object could not be read.
        """
        broken = []
        for stored in await self._query(None):
            if isinstance(stored, BrokenObject):
                broken.append(stored)
            elif any(str(c.get("UID")) == uid for c in _items_of(stored.calendar)):
                return stored
        if broken:
            raise PartialResultError([], broken, f"no readable item with UID {uid!r}; {len(broken)} unreadable")
        raise NotFoundError(f"no item with UID {uid!r}")

    async def _list_etags(self) -> dict[str, str]:
        """``{native_id: etag}`` for every stored object; override where it can be listed without content."""
        # A broken object listed without an etag is still there: never report it deleted.
        return {s.native_id: s.etag or "" for s in await self._query(None)}

    async def _fetch_many(self, native_ids: Iterable[str]) -> Sequence[StoredObject | BrokenObject]:
        """Override with a bulk fetch (CalDAV ``calendar-multiget``); objects gone meanwhile are left out.

        The default cannot keep the bytes of an object that does not parse;
        a backend that has them overrides this to return them.
        """
        out: list[StoredObject | BrokenObject] = []
        for native_id in native_ids:
            try:
                out.append(await self._fetch(native_id))
            except NotFoundError:
                pass
            except InvalidDataError as exc:
                out.append(BrokenObject(native_id, None, None, exc))
        return out

    async def _changes(
        self, token: SyncToken | None, known: Mapping[str, str] | None
    ) -> tuple[Sequence[StoredObject | BrokenObject], list[str], SyncToken]:
        """Override with a native change feed; the default diffs an etag listing (``EMULATED``)."""
        if token is not None:
            base = self._decode_token(token)
        else:
            base = dict(known or {})
        current = await self._list_etags()
        changed = [n for n, etag in current.items() if base.get(n) != etag]
        deleted = sorted(n for n in base if n not in current)
        fetched = await self._fetch_many(changed)
        for stored in fetched:
            if isinstance(stored, BrokenObject) and stored.etag is None:
                stored.etag = current[stored.native_id] or None
        return fetched, deleted, self._encode_token(current)

    async def _move(self, item: Item, target: AsyncCollection) -> StoredObject | None:
        """Override with the backend's own move; ``None`` means add to the target, then delete here."""
        return None

    async def _save_occurrence(
        self, item: Item, *, overwrite: bool, scope: Scope, loss: LossPolicy | None, verify: bool | None
    ) -> AsyncItem:
        """Merge one occurrence into its stored object (§3.7); for backends declaring ``recurrence.edit-occurrence``."""
        raise NotImplementedError(
            f"{type(self).__name__} declares {Feature.RECURRENCE_EDIT_OCCURRENCE} but has no merge"
        )

    async def _complete_recurring(
        self, task: Task, at: datetime | None, mode: str, *, loss: LossPolicy | None, verify: bool | None
    ) -> AsyncTask:
        """Complete one occurrence of a recurring task; for backends declaring ``task.complete-recurring``."""
        raise NotImplementedError(f"{type(self).__name__} declares {Feature.TASK_COMPLETE_RECURRING} but cannot")

    async def _set_name(self, name: str) -> None:
        raise NotImplementedError(f"{type(self).__name__} declares {Feature.COLLECTION_SET_NAME} but cannot")

    async def _delete_collection(self) -> None:
        raise NotImplementedError(f"{type(self).__name__} declares {Feature.DELETE_COLLECTION} but cannot")

    def _wrap_native(self, native_item: object) -> StoredObject:
        raise UnsupportedError(None, f"{type(self).__name__} cannot wrap {type(native_item).__name__}")

    def _plan_losses(self, component: icalendar.Component) -> list[Change]:
        """Override for mapping rules beyond the capability table's ``values`` and ``map``."""
        return plan_losses(component, self.capabilities)

    # === the generic layer =============================================

    @contextmanager
    def _context(self) -> Generator[None]:
        """Every error leaving a public method says which backend and collection it came from."""
        try:
            yield
        except CalendaringError as exc:
            if exc.backend is None:
                exc.backend = self.backend_id
            if exc.collection_id is None:
                exc.collection_id = self.id
            raise

    def _require(self, feature: Feature) -> None:
        table = self._backend.capabilities if feature in BACKEND_FEATURES else self.capabilities
        if table.level(feature) is Support.UNSUPPORTED:
            raise UnsupportedError(feature, backend=self.backend_id, collection_id=self.id)

    def _require_component(self, item: Item) -> None:
        kind = Component(item.component.name)
        if kind not in self.components:
            raise UnsupportedError(COMPONENT_FEATURE[kind], backend=self.backend_id, collection_id=self.id)

    def _require_writable(self, item: Item | None = None) -> None:
        self._require(Feature.WRITE)
        if item is not None:
            self._require_component(item)

    def _loss_policy(self, loss: LossPolicy | None) -> LossPolicy:
        """The call's, else the backend's, else its workspace's, else ``RAISE``."""
        workspace = self._backend._workspace
        return loss or self._backend.loss or (workspace.loss if workspace else None) or RAISE

    def _verifying(self, verify: bool | None) -> bool:
        for setting in (verify, self._backend.verify, self._backend._workspace and self._backend._workspace.verify):
            if setting is not None:
                return bool(setting)
        return False

    def _token_scope(self) -> str:
        """What an emulated token is bound to: by default the backend's kind and id, and the collection's id.

        The backend id is the caller's label for it, so two configurations
        that give different stores the same id accept each other's tokens,
        and renaming a backend invalidates its tokens.  A backend that has a
        locator (a directory, a URL) overrides this with it.
        """
        return f"{self._backend.kind}:{self.backend_id}:{self.id}"

    def _encode_token(self, etags: Mapping[str, str]) -> SyncToken:
        raw = json.dumps({"collection": self._token_scope(), "etags": etags}, sort_keys=True).encode()
        return SyncToken(base64.urlsafe_b64encode(raw).decode())

    def _decode_token(self, token: SyncToken) -> dict[str, str]:
        try:
            data = json.loads(base64.urlsafe_b64decode(token.encode()))
            etags = data["etags"]
            issued_by = data["collection"]
        except (binascii.Error, ValueError, TypeError, KeyError) as exc:
            raise SyncTokenError(f"not a token this collection issued: {token!r}") from exc
        if issued_by != self._token_scope() or not isinstance(etags, dict):
            raise SyncTokenError(f"a token from another collection: {issued_by!r}")
        return etags

    def _bound(self, stored: StoredObject, *, written: bool = False) -> list[AsyncItem]:
        """The items one stored object holds, bound here (several only for overrides without a master)."""
        parts = split_stored(stored.calendar)
        out = []
        for part in parts:
            cls = BOUND[Item._class_for(part)]
            item = cls(
                part,
                collections=(self,),
                native_id=stored.native_id,
                etag=stored.etag,
                wire=None if written or len(parts) > 1 else stored.wire,
                native=stored.native,
            )
            if isinstance(item, Task):
                item.native_status = stored.native_status
                item.native_priority = stored.native_priority
            out.append(item)
        return out

    def _one(self, stored: StoredObject, *, written: bool = False) -> AsyncItem:
        return self._bound(stored, written=written)[0]

    def _prepare(
        self, item: Item, wire: bytes | None, loss: LossPolicy | None
    ) -> tuple[icalendar.Calendar, bytes | None]:
        """What to send: a copy with the losses applied, after the loss policy agreed.  No I/O."""
        if wire is not None:
            self._require(Feature.WRITE_WIRE)
            if item.is_occurrence:
                raise ValueError("wire= is a whole stored object; an occurrence is not one")
            calendar = _parse(wire)
            if Item._class_for(calendar) is not type(item)._data_class():
                raise ValueError("wire= holds a different component type than the item")
            if {str(c.get("UID")) for c in _items_of(calendar)} != {item.uid}:
                raise ValueError("wire= holds a different UID than the item")
        else:
            calendar = copy.deepcopy(item.icalendar)
        changes = [change for c in _items_of(calendar) for change in self._plan_losses(c)]
        changes += plan_calendar_losses(calendar, self.capabilities)
        losses = [loss_ for loss_, _ in changes]
        if losses and wire is not None:
            raise UnsupportedError(Feature.WRITE_WIRE, "wire= bytes the backend cannot store as they are")
        if losses:
            self._loss_policy(loss)(losses, item)
            for _, apply in changes:
                apply()
        return calendar, wire

    async def _check(self, sent: icalendar.Calendar, result: AsyncItem) -> None:
        assert result.native_id is not None
        stored = await self._fetch(result.native_id)
        found = differences(sent, stored.calendar)
        if found:
            raise VerificationError(found)

    def _searcher(self, searcher: Searcher | None, filters: Mapping[str, Any]) -> Searcher:
        if searcher is not None and filters:
            raise TypeError("give a Searcher or keyword filters, not both")
        return searcher if searcher is not None else Searcher(**filters)

    def _matches(
        self, searcher: Searcher, stored: StoredObject | BrokenObject, broken: list[BrokenObject]
    ) -> list[AsyncItem]:
        """Run the client-side filter over one stored object (A3); expand into occurrences if asked.

        An object that is broken, or that the searcher cannot match, goes to ``broken``.
        """
        if isinstance(stored, BrokenObject):
            broken.append(stored)
            return []
        out: list[AsyncItem] = []
        parts = split_stored(stored.calendar)
        for part in parts:
            try:
                matched = searcher.check_component(part)
            except ValueError as exc:  # icalendar-searcher on what is no valid recurrence set
                error = InvalidDataError(f"the searcher cannot match it: {exc}")
                error.__cause__ = exc
                broken.append(BrokenObject(stored.native_id, stored.etag, stored.wire, error))
                return []
            if not matched:
                continue
            components = [c for c in matched if c.name != "VTIMEZONE"]
            if not components:
                continue
            if not searcher.expand or all("RECURRENCE-ID" not in c for c in components):
                whole = StoredObject(
                    stored.native_id,
                    stored.etag,
                    part,
                    stored.wire if len(parts) == 1 else None,
                    stored.native,
                    stored.native_status,
                    stored.native_priority,
                )
                out.append(self._one(whole))
                continue
            out.extend(self._occurrence(stored, part, component) for component in components)
        return out

    def _occurrence(self, stored: StoredObject, part: icalendar.Calendar, component: icalendar.Component) -> AsyncItem:
        """One occurrence of a stored series as an item: the series' calendar properties and timezones kept."""
        occurrence = icalendar.Calendar()
        for k, v in part.items():
            occurrence[k] = v
        for tz in part.subcomponents:
            if tz.name == "VTIMEZONE":
                occurrence.add_component(copy.deepcopy(tz))
        occurrence.add_component(copy.deepcopy(component))
        return self._one(
            StoredObject(
                stored.native_id,
                stored.etag,
                occurrence,
                None,
                stored.native,
                stored.native_status,
                stored.native_priority,
            )
        )

    @staticmethod
    def _sorted(searcher: Searcher, items: list[AsyncItem]) -> list[AsyncItem]:
        if getattr(searcher, "_sort_keys", None):
            return sorted(items, key=lambda i: searcher.sorting_value(i.component))
        return items

    # === reading =======================================================

    async def search(self, searcher: Searcher | None = None, **filters: Any) -> list[AsyncItem]:
        with self._context():
            searcher = self._searcher(searcher, filters)
            broken: list[BrokenObject] = []
            found = [i for stored in await self._query(searcher) for i in self._matches(searcher, stored, broken)]
            items = self._sorted(searcher, found)
            if broken:
                raise PartialResultError(items, broken)
            return items

    async def iter_search(self, searcher: Searcher | None = None, **filters: Any) -> AsyncIterator[AsyncItem]:
        """``search()`` one item at a time; with sort keys, everything is read before the first.

        Objects left out raise ``PartialResultError`` at the end, with empty
        ``results``: they were yielded.
        """
        with self._context():
            searcher = self._searcher(searcher, filters)
            if getattr(searcher, "_sort_keys", None):
                for item in await self.search(searcher):
                    yield item
                return
            broken: list[BrokenObject] = []
            async for stored in self._iter_query(searcher):
                for item in self._matches(searcher, stored, broken):
                    yield item
            if broken:
                raise PartialResultError([], broken)  # the caller has the results already

    async def tasks(self, **filters: Any) -> list[AsyncTask]:
        if Component.TASK not in self.components:
            return []
        return [i for i in await self.search(todo=True, **filters) if isinstance(i, AsyncTask)]

    async def events(self, **filters: Any) -> list[AsyncEvent]:
        if Component.EVENT not in self.components:
            return []
        return [i for i in await self.search(event=True, **filters) if isinstance(i, AsyncEvent)]

    async def journals(self, **filters: Any) -> list[AsyncJournal]:
        if Component.JOURNAL not in self.components:
            return []
        return [i for i in await self.search(journal=True, **filters) if isinstance(i, AsyncJournal)]

    async def get(self, uid: str) -> AsyncItem:
        """The item with this UID.  Overrides stored without their master: the earliest occurrence (§3.1)."""
        with self._context():
            return min(self._bound(await self._find_uid(uid)), key=_occurs_at)

    async def get_by_native_id(self, native_id: str) -> AsyncItem:
        with self._context():
            return self._one(await self._fetch(native_id))

    @overload
    async def reload(self, item: Task) -> AsyncTask: ...
    @overload
    async def reload(self, item: Event) -> AsyncEvent: ...
    @overload
    async def reload(self, item: Journal) -> AsyncJournal: ...
    @overload
    async def reload(self, item: Item) -> AsyncItem: ...
    async def reload(self, item: Item) -> AsyncItem:
        """A fresh copy, with a new etag.  On an occurrence, the same occurrence."""
        with self._context():
            if item.native_id is None:
                raise ValueError("reload(): the item was never stored")
            stored = await self._fetch(item.native_id)
            if not item.is_occurrence:
                return self._one(stored)
            wanted = item.component["RECURRENCE-ID"]
            # An override first: a moved one does not occur at its RECURRENCE-ID.
            for component in _items_of(stored.calendar):
                if component.get("RECURRENCE-ID") == wanted:
                    return self._occurrence(stored, stored.calendar, component)
            rid = item.component.decoded("RECURRENCE-ID")
            for component in recurring_ical_events.of(stored.calendar).between(rid, rid + timedelta(seconds=1)):
                if component.get("RECURRENCE-ID") == wanted:
                    return self._occurrence(stored, stored.calendar, component)
            raise NotFoundError(f"the occurrence {rid} of {item.uid!r} is gone")

    async def relatives(self, item: Item, reltype: str | None = None) -> list[AsyncItem]:
        """What ``RELATED-TO`` points at, and for ``PARENT`` / ``CHILD`` the items pointing back."""
        with self._context():
            wanted = None if reltype is None else reltype.upper()
            related = item.component.get("RELATED-TO", [])
            related = related if isinstance(related, list) else [related]
            out: dict[str, AsyncItem] = {}
            broken: list[BrokenObject] = []
            for value in related:
                kind = str(value.params.get("RELTYPE", "PARENT")).upper()
                if wanted is None or kind == wanted:
                    found = await self._lookup_relative(str(value), broken)
                    if found is not None:
                        out.setdefault(found.uid, found)
            back = {"PARENT": "CHILD", "CHILD": "PARENT"}
            if wanted is None or wanted in back:
                try:
                    listed = await self.search()
                except PartialResultError as exc:
                    broken.extend(exc.broken)
                    listed = list(exc.results)
                for other in listed:
                    for value in self._related_to(other):
                        kind = str(value.params.get("RELTYPE", "PARENT")).upper()
                        if str(value) == item.uid and kind in back and (wanted is None or back[kind] == wanted):
                            out.setdefault(other.uid, other)
            if broken:
                raise PartialResultError(list(out.values()), broken)
            return list(out.values())

    @staticmethod
    def _related_to(item: Item) -> list[Any]:
        related = item.component.get("RELATED-TO", [])
        return related if isinstance(related, list) else [related]

    async def _lookup_relative(self, uid: str, broken: list[BrokenObject]) -> AsyncItem | None:
        """Here first, then in the backend's other collections; past objects that cannot be read."""
        others = [c for c in await self._backend.collections() if c.id != self.id]
        for collection in [self, *others]:
            try:
                return await collection.get(uid)
            except PartialResultError as exc:  # not found among what could be read
                broken.extend(exc.broken)
            except NotFoundError:
                pass
        return None  # a dangling relation

    # === writing =======================================================

    @overload
    async def add(
        self,
        item: Task,
        *,
        native_id: str | None = None,
        wire: bytes | None = None,
        loss: LossPolicy | None = None,
        verify: bool | None = None,
    ) -> AsyncTask: ...
    @overload
    async def add(
        self,
        item: Event,
        *,
        native_id: str | None = None,
        wire: bytes | None = None,
        loss: LossPolicy | None = None,
        verify: bool | None = None,
    ) -> AsyncEvent: ...
    @overload
    async def add(
        self,
        item: Journal,
        *,
        native_id: str | None = None,
        wire: bytes | None = None,
        loss: LossPolicy | None = None,
        verify: bool | None = None,
    ) -> AsyncJournal: ...
    @overload
    async def add(
        self,
        item: Item,
        *,
        native_id: str | None = None,
        wire: bytes | None = None,
        loss: LossPolicy | None = None,
        verify: bool | None = None,
    ) -> AsyncItem: ...
    async def add(
        self,
        item: Item,
        *,
        native_id: str | None = None,
        wire: bytes | None = None,
        loss: LossPolicy | None = None,
        verify: bool | None = None,
    ) -> AsyncItem:
        """Store a new item; ``AlreadyExistsError`` if its UID or ``native_id`` is taken."""
        with self._context():
            self._require_writable(item)
            if native_id is not None:
                self._require(Feature.IDENTITY_CLIENT_NATIVE_ID)
            if item.is_occurrence:
                raise ValueError("add(): an occurrence is part of a stored object; save() it instead")
            calendar, wire = self._prepare(item, wire, loss)
            stored = await self._create(calendar, native_id=native_id, wire=wire)
            result = self._one(stored, written=True)
            if self._verifying(verify):
                await self._check(calendar, result)
            return result

    async def add_task(
        self, *, loss: LossPolicy | None = None, verify: bool | None = None, **properties: Any
    ) -> AsyncTask:
        result = await self.add(Task.new(**properties), loss=loss, verify=verify)
        assert isinstance(result, AsyncTask)
        return result

    async def add_event(
        self, *, loss: LossPolicy | None = None, verify: bool | None = None, **properties: Any
    ) -> AsyncEvent:
        result = await self.add(Event.new(**properties), loss=loss, verify=verify)
        assert isinstance(result, AsyncEvent)
        return result

    async def add_journal(
        self, *, loss: LossPolicy | None = None, verify: bool | None = None, **properties: Any
    ) -> AsyncJournal:
        result = await self.add(Journal.new(**properties), loss=loss, verify=verify)
        assert isinstance(result, AsyncJournal)
        return result

    @overload
    async def save(
        self,
        item: Task,
        *,
        overwrite: bool = False,
        scope: Scope = Scope.THIS,
        wire: bytes | None = None,
        loss: LossPolicy | None = None,
        verify: bool | None = None,
    ) -> AsyncTask: ...
    @overload
    async def save(
        self,
        item: Event,
        *,
        overwrite: bool = False,
        scope: Scope = Scope.THIS,
        wire: bytes | None = None,
        loss: LossPolicy | None = None,
        verify: bool | None = None,
    ) -> AsyncEvent: ...
    @overload
    async def save(
        self,
        item: Journal,
        *,
        overwrite: bool = False,
        scope: Scope = Scope.THIS,
        wire: bytes | None = None,
        loss: LossPolicy | None = None,
        verify: bool | None = None,
    ) -> AsyncJournal: ...
    @overload
    async def save(
        self,
        item: Item,
        *,
        overwrite: bool = False,
        scope: Scope = Scope.THIS,
        wire: bytes | None = None,
        loss: LossPolicy | None = None,
        verify: bool | None = None,
    ) -> AsyncItem: ...
    async def save(
        self,
        item: Item,
        *,
        overwrite: bool = False,
        scope: Scope = Scope.THIS,
        wire: bytes | None = None,
        loss: LossPolicy | None = None,
        verify: bool | None = None,
    ) -> AsyncItem:
        """Update a stored item, unless it changed since it was read (``ConflictError``)."""
        with self._context():
            self._require_writable(item)
            if item.native_id is None:
                raise ValueError("save(): the item was never stored; add() it")
            if item.is_occurrence and wire is not None:
                raise ValueError("wire= is a whole stored object; an occurrence is not one")
            if scope is Scope.THIS_AND_FUTURE:
                self._require(Feature.RECURRENCE_EDIT_THIS_AND_FUTURE)
            if item.is_occurrence:
                self._require(Feature.RECURRENCE_EDIT_OCCURRENCE)
                return await self._save_occurrence(item, overwrite=overwrite, scope=scope, loss=loss, verify=verify)
            if scope is not Scope.THIS:
                raise ValueError("scope= applies to an occurrence")
            if item.etag is None and not overwrite:
                raise ValueError("save(): no etag to check against; pass overwrite=True to write anyway")
            calendar, wire = self._prepare(item, wire, loss)
            stored = await self._update(item.native_id, calendar, etag=None if overwrite else item.etag, wire=wire)
            result = self._one(stored, written=True)
            if self._verifying(verify):
                await self._check(calendar, result)
            return result

    async def delete(self, item: Item | str, *, overwrite: bool = False) -> None:
        """Remove a stored item.  Given an item, unless it changed since it was read."""
        with self._context():
            self._require(Feature.WRITE)
            if isinstance(item, str):
                await self._delete(item, etag=None)
                return
            if item.native_id is None:
                raise ValueError("delete(): the item was never stored")
            if item.is_occurrence:
                # EXDATE or STATUS:CANCELLED is undecided (API_DESIGN.md Q12).
                raise UnsupportedError(None, "deleting one occurrence is undecided (API_DESIGN.md Q12)")
            await self._delete(item.native_id, etag=None if overwrite else item.etag)

    async def complete(
        self,
        task: Task,
        at: datetime | None = None,
        mode: Literal["safe", "this_and_future"] = "safe",
        *,
        loss: LossPolicy | None = None,
        verify: bool | None = None,
    ) -> AsyncTask:
        with self._context():
            self._require_writable(task)
            if not isinstance(task, Task):
                raise TypeError("complete(): not a task")
            if task.is_occurrence or "RRULE" in task.component or "RDATE" in task.component:
                self._require(Feature.TASK_COMPLETE_RECURRING)
                return await self._complete_recurring(task, at, mode, loss=loss, verify=verify)
            data = task.as_data()
            data.component.pop("STATUS", None)
            data.component.add("STATUS", "COMPLETED")
            if self.capabilities.level(Feature.TASK_COMPLETED) is not Support.UNSUPPORTED:
                data.component.pop("COMPLETED", None)
                data.component.add("COMPLETED", (at or datetime.now(UTC)).astimezone(UTC))
            result = await self.save(data, loss=loss, verify=verify)
            assert isinstance(result, AsyncTask)
            return result

    async def uncomplete(self, task: Task, *, loss: LossPolicy | None = None, verify: bool | None = None) -> AsyncTask:
        with self._context():
            self._require_writable(task)
            if not isinstance(task, Task):
                raise TypeError("uncomplete(): not a task")
            data = task.as_data()
            data.component.pop("STATUS", None)
            data.component.add("STATUS", "NEEDS-ACTION")
            data.component.pop("COMPLETED", None)
            result = await self.save(data, loss=loss, verify=verify)
            assert isinstance(result, AsyncTask)
            return result

    @overload
    async def move(
        self, item: Task, target: AsyncCollection, *, loss: LossPolicy | None = None, verify: bool | None = None
    ) -> AsyncTask: ...
    @overload
    async def move(
        self, item: Event, target: AsyncCollection, *, loss: LossPolicy | None = None, verify: bool | None = None
    ) -> AsyncEvent: ...
    @overload
    async def move(
        self, item: Journal, target: AsyncCollection, *, loss: LossPolicy | None = None, verify: bool | None = None
    ) -> AsyncJournal: ...
    @overload
    async def move(
        self, item: Item, target: AsyncCollection, *, loss: LossPolicy | None = None, verify: bool | None = None
    ) -> AsyncItem: ...
    async def move(
        self, item: Item, target: AsyncCollection, *, loss: LossPolicy | None = None, verify: bool | None = None
    ) -> AsyncItem:
        """Move to ``target``; between backends, add there and then delete here (``EMULATED``, not atomic)."""
        with self._context():
            self._require_writable(item)
            self._require(Feature.MOVE)
            target._require_writable(item)
            if item.native_id is None:
                raise ValueError("move(): the item was never stored")
            if target._backend is self._backend:
                stored = await self._move(item, target)
                if stored is not None:
                    return target._one(stored, written=True)
            if item.etag is not None and (await self._fetch(item.native_id)).etag != item.etag:
                raise ConflictError(f"{item.native_id!r} changed since it was read")
            moved = await target.add(item.as_data(), loss=loss, verify=verify)
            try:
                await self.delete(item)
            except ConflictError:
                with suppress(CalendaringError):  # best effort: the conflict is what to report
                    await target.delete(moved, overwrite=True)
                raise
            return moved

    # === binding, no I/O ===============================================

    def wrap(self, native_item: object) -> AsyncItem:
        """The backend's own object as an item bound here; see the escape hatch (§8)."""
        return self._one(self._wrap_native(native_item))

    @overload
    def bind(self, item: Task) -> AsyncTask: ...
    @overload
    def bind(self, item: Event) -> AsyncEvent: ...
    @overload
    def bind(self, item: Journal) -> AsyncJournal: ...
    @overload
    def bind(self, item: Item) -> AsyncItem: ...
    def bind(self, item: Item) -> AsyncItem:
        """A copy of ``item`` bound to this collection, replacing any collection it had."""
        data = item.as_data()
        cls = BOUND[type(data)]
        bound = cls.__new__(cls)
        bound.__dict__.update(data.__dict__)
        bound.collections = (self,)
        bound.collection_id = self.id
        return bound

    # === change detection ==============================================

    async def changes(
        self, token: SyncToken | None = None, *, known: Mapping[str, str] | None = None
    ) -> ChangeSet[AsyncItem]:
        """What changed since ``token``; without one, everything (or what differs from ``known``)."""
        with self._context():
            self._require(Feature.CHANGES)
            if known is not None:
                if token is not None:
                    raise ValueError("changes(): known= with a token; an incremental report has its own baseline")
                self._require(Feature.CHANGES_KNOWN)
            changed, deleted, new_token = await self._changes(token, known)
            broken = [s for s in changed if isinstance(s, BrokenObject)]
            items = [i for s in changed if not isinstance(s, BrokenObject) for i in self._bound(s)]
            result = ChangeSet(items, deleted, new_token)
            if broken:
                raise PartialResultError(result, broken)
            return result

    # === the collection itself =========================================

    async def set_name(self, name: str) -> None:
        with self._context():
            self._require(Feature.COLLECTION_SET_NAME)
            await self._set_name(name)
            self.name = name

    async def delete_collection(self) -> None:
        with self._context():
            self._require(Feature.DELETE_COLLECTION)
            await self._delete_collection()
