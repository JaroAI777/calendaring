# Unified API specification

**Roadmap item:** [1.1 Unified API design and peer review](ROADMAP.md#11-unified-api-design-and-peer-review)

**Status:** draft by Claude Opus 5.5, 2026-10-08. Reviewed and edited by the author on the following days, and his comments applied. The A1 rework was accepted on 2026-10-10. Peer-reviewed from 2026-10-10 in [PR 5](https://github.com/pycalendar/calendaring/pull/5) and in video calls; the review was considered complete on 2026-10-13 ([§11 Peer review](#11-peer-review)).

**Inputs:** [0.1 task model survey](TASK_MODEL_SURVEY.md), [0.2 sync/async decision](SYNC_ASYNC_ARCHITECTURE.md#11-decision), [0.3 decisions D1–D7](PRIOR_ART_AND_DECISIONS.md#part-3-project-decisions)

**Consumers:** [roadmap 1.2](ROADMAP.md#12-abstract-base-classes-and-the-backend-conformance-suite) writes the conformance suite against this document, [roadmap 1.3](ROADMAP.md#13-syncasync-scaffolding) generates the sync copy of what it calls async, [roadmap 1.6](ROADMAP.md#16-time-tracking-model-and-api) adds the time log to the task model in [§3.4 Task](#34-task).

The test the roadmap sets: does it fit CalDAV *and* a Gitea issue tracker without lying about either? Each section ends with how it does that.

Import names below say `calendaring`. The name may still change (see the note under [roadmap 0.3](ROADMAP.md#03-prior-art-standards-and-project-decisions)); nothing in the design depends on it, and the one place where a name is written into user data, the `X-` prefix ([§3.6 Properties with no standard home](#36-properties-with-no-standard-home)), is deliberately not the package name.

---

## Overview

One Python API over many places calendar data lives: CalDAV servers,
`.ics` files and directories, read-only feeds, JMAP, and issue trackers
such as Gitea. A program written against it should behave the same
whichever of these it is pointed at, and should be told, rather than
find out later, when a backend cannot do what it asked. The decisions
below are labelled A1–A9 so the rest of the document can refer to them.

**The objects.** A `Workspace` holds `Backend`s (one server, directory,
file or feed each), a backend holds `Collection`s (a calendar, a task list, a
repository), and a collection holds items: `Event`, `Task`, `Journal`.
The I/O classes come in a sync and an async version. The items are plain
data, so helpers are written once for both modes; what a collection hands
out is that data bound to it (`AsyncTask`, `SyncTask`), which adds
`save()`, `complete()` and the like (**A1**, [§1](#1-layers-and-modes),
[§2](#2-the-io-classes)). An item wraps an `icalendar.Calendar`, and its
iCalendar properties are read and written through `icalendar`'s own typed
properties (`item.component.summary`). The item adds identity and the few
fields iCalendar has no property for, and redefines none of the others
(**A2**, [§3](#3-items)).

**Same answer everywhere.** Backends differ in how well they can search,
so the server's answer is never trusted alone: a backend may filter
server-side, but only more loosely, and the client always re-filters with
`icalendar_searcher` (**A3**, [§4](#4-search)). Likewise, every item has an
`etag` and every collection can list changes since a token, natively or by
emulation, so a sync tool does not need to know which backend it talks to
(**A7**, [§7](#7-change-detection)).

**No silent data loss.** Each collection carries a table of what it
supports, and how well (**A4**, [§5](#5-capabilities)). An operation it
cannot do raises before any I/O. A write that would lose data, such as a
fourth task status on a tracker with three, also raises by default; the
caller can lower that to a warning or allow it. Emulation is used only when
the result is the same or the weaker guarantee is documented (an emulated
conditional write can race), and `verify=True` reads a write
back to check it (**A5**, [§5.3](#53-what-happens-when-the-caller-asks-for-something-unsupported)).
Fields iCalendar has no property for go to RFC 9253 or the tasks draft
first, and `X-PYCAL-*` only as a last resort (**A9**,
[§3.6](#36-properties-with-no-standard-home)).

**When the abstraction is not enough.** Errors form one hierarchy under
`CalendaringError`, with the backend's own exception kept as `__cause__`
(**A6**, [§6](#6-errors)), every object exposes the backend's own
object as `.native`, and tasks keep the backend's own status and priority
as `native_status` / `native_priority` (**A8**, [§8](#8-the-escape-hatch)).

### Glossary

Terms used before the section that defines them:

| Term | Meaning |
|---|---|
| backend | one configured place data lives: a CalDAV account, a JMAP account, a directory, a file, a feed, a Gitea instance ([§1](#1-layers-and-modes)) |
| collection | what holds items: a calendar, a task list, a directory, a repository |
| item | one event, task or journal: one `VCALENDAR` with the components of one `UID` ([§3.1](#what-an-item-contains)) |
| occurrence | one instance of a recurring item; an item can be a single occurrence (`is_occurrence`) |
| data item / bound item | `Task` is plain data and does no I/O; `AsyncTask` / `SyncTask` is the same data plus the collection it came from, and has `save()` ([§1.1](#11-sync-and-async)) |
| mode | sync or async. The async code is written by hand, the sync code generated from it by unasync |
| `native_id` | the backend's own key for a stored item: a CalDAV href, a file name, an issue number ([§3.2](#32-identity)) |
| `etag` | a value that changes whenever the stored item changes; the server's ETag, or one made up by the library ([§7](#7-change-detection)) |
| `wire` | the stored object exactly as the backend returned it, bytes and all; kept beside the parsed item ([§3.1](#the-stored-form)) |
| token, change set | `changes()` returns what changed (a `ChangeSet`) and a token to pass next time, to get what changed since ([§7](#7-change-detection)) |
| capability, feature, support level | per collection, a table of what it can do (`Feature`) and how well (`FULL`, `LOSSY`, `EMULATED`, `UNSUPPORTED`, `UNKNOWN`) ([§5](#5-capabilities)) |
| lossy write, loss policy | a write the backend can store only by changing or dropping a value; the loss policy says whether it raises, warns or goes ahead ([§5.3](#53-what-happens-when-the-caller-asks-for-something-unsupported)) |
| escape hatch, `.native` | the backend's own object behind ours, for what this API does not cover ([§8](#8-the-escape-hatch)) |

---

## 1. Layers and modes

```
Workspace ──< Backend ──────< Collection ──────────< item: Event | Task | Journal
(config,      (one store:      (calendar, task list,  (data over
 fan-out)      server, dir,     repo, directory,       icalendar.Calendar,
               file, feed)      feed)                  bound per mode)
```

- **Workspace** — a set of backends, usually all the ones a configuration
  file ([roadmap 1.4](ROADMAP.md#14-configuration-and-credentials)) lists,
  and operations that fan out across them. Optional: a caller with one
  server never needs it.
- **Backend** — one configured store with its credentials, if any: a
  CalDAV principal, a Gitea instance and token, a directory root, a feed
  URL. Owns the HTTP session where there is one. Its class says which kind
  (`CalDAVBackend`, `FilesBackend`, `GiteaBackend`); `kind` says the same as
  a string.
- **Collection** — the unit that holds items: a CalDAV calendar, a directory
  of `.ics` files or a single `.ics` file, a feed, a JMAP calendar, a Gitea
  repository. Owns the capability table.
- **Item** — `Event`, `Task` or `Journal`, sharing the base class
  `Item`. One item is one `VCALENDAR` holding components of one
  `UID`: a non-recurring item, a full recurrence set, or a single
  occurrence. It is never a calendar of several items
  ([§3.1, What an item contains](#what-an-item-contains)).
  These are mode-free data; what a collection hands out is the same data
  bound to it (`AsyncTask`, `SyncTask`, …, [§1.1](#11-sync-and-async)).

"Collection" rather than "Calendar" because a Gitea repository is not
a calendar.  "Collection" is also used in the CalDAV RFC, as a
calendar is a WebDAV collection, so a CalDAV user loses nothing by the
word. "Task" rather than caldav's "Todo" because the trackers say task
or issue, and only iCalendar says to-do.

"Item" rather than caldav's `CalendarObjectResource` or a shorter
`CalendarObject`: RFC 4791 calls an item a "calendar object resource", but
"calendar object" reads as a calendar, a collection of items, and a PR 5
reviewer read it that way. "Item" is also what this document calls them
throughout.

"Backend" rather than "Account" or a per-backend "Client" (as in caldav's
`DAVClient`), because a directory or a single `.ics` file has neither an
account nor a server to be a client of. The cost is that "backend" now
names both the kind (the CalDAV backend) and an instance of it (this
`CalDAVBackend` pointed at that server). In practice the class name carries
the kind and the variable carries the instance, and the [roadmap 1.2](ROADMAP.md#12-abstract-base-classes-and-the-backend-conformance-suite) ABC that a
backend author subclasses is the same `Backend`, so there is one word for
it instead of two. The aggregator is a `Workspace`: "client" is an HTTP
word that could mean anything, and a workspace is everything you have
configured (chosen by the author, 2026-10-09; see
[§10 Open questions](#10-open-questions), Q2).

### 1.1 Sync and async

It's been decided ([0.2 decision](SYNC_ASYNC_ARCHITECTURE.md#11-decision)) that the async classes are the source and the sync classes are to be
generated. The public names follow httpx's pattern: `Workspace` / `AsyncWorkspace`,
`Backend` / `AsyncBackend`, `Collection` / `AsyncCollection`, all importable
from `calendaring`. That costs one replacement-map entry per class in [roadmap 1.3](ROADMAP.md#13-syncasync-scaffolding)'s
generator (unasync would otherwise produce `SyncWorkspace`).

**Items: mode-free data classes, plus a bound class per mode (A1).**

- `Item`, `Event`, `Task` and `Journal` are the mode-free data
  classes of [§3 Items](#3-items). They do no I/O, and helpers annotate
  against them, so a helper is written once for both modes.
- `AsyncEvent`, `AsyncTask` and `AsyncJournal` subclass them (via
  `AsyncItem`), hold the `AsyncCollection` they belong to (a
  tuple, with one entry for now; [§10](#10-open-questions), Q9), and
  add `save()`, `delete()`, `reload()` and `relatives()`, plus `complete()`
  and `uncomplete()` on tasks. Each calls the collection method of the same
  name and then updates the item in place ([§2.3 Collection](#23-collection)).
  unasync generates `SyncEvent`, `SyncTask` and `SyncJournal`.
- The collection methods are the primitive: `task.save()` is
  `task.collection.save(task)` plus the in-place update.

**How items are made.** Library users do not call the bound classes'
constructors:
- A data item comes from a factory, not a constructor:
  `Task.new(summary=..., end=...)`, `Task.from_ical(data)` or
  `Task.from_icalendar(calendar)` ([§3.1](#31-the-base-a-typed-view-over-icalendar)).
- A bound item comes from a collection: from a read (`search`, `get`, …),
  from a write (`add`, `save`, …), from `collection.add_task(**properties)`
  (shorthand for `add(Task.new(**properties))`, as caldav's `add_todo`), or
  from `collection.bind(item)`, which **elevates** a data item, or an item
  bound elsewhere, to a copy bound to that collection, without I/O. On a
  bound item, `bind()` replaces its collections rather than adding to them.
- `item.as_data()` goes the other way: a mode-free copy, with no
  collection inside, to hand to the other mode, to pickle, or to keep.

**Why this shape, in short.** Data-only items (the first draft) would make
callers keep track of which collection every item came from: `Workspace.search`
returns items from many collections, so saving one would mean looking its
collection up first. They would also break caldav's `task.save()` and
`todo.complete()`, which plann calls 13 and 4 times. Bound-only items (no
data class) would make every helper choose between `AsyncTask` and
`SyncTask`, and would carry a live session into every copy and pickle. The
back reference itself is harmless under unasync, because the "annotations
that lie" came from caldav's *dual-mode* methods, not from the item knowing
its client. The cost of having both is two more classes per item type, and
two ways to save; the bound method is the thin one, so the two cannot
disagree. *Decided by the author, 2026-10-10.*

**Placement under unasync.** The data classes are hand-written outside
`_async/` and shared by both modes. The bound classes live in `_async/`, and
unasync's default `AsyncX` → `SyncX` rename produces the sync ones with no
replacement-map entry.

**A naming asymmetry, accepted.** The I/O classes follow httpx: the
sync one is unprefixed (`Collection` / `AsyncCollection`). For items, the
unprefixed name goes to the data class, and the sync bound class is
`SyncTask`. A reader who generalises from `Collection` will take `Task` to
be the sync bound class, and `AsyncTask` also reads like `asyncio.Task`.
The rejected alternative was `TaskData` for the data class and `Task` /
`AsyncTask` for the bound ones, at one replacement-map entry per class.
*Decided by the author after peer review, 2026-10-13:* `Item` / `SyncItem`
/ `AsyncItem`, and likewise for each item type
([§10 Open questions](#10-open-questions), Q10).

**Mirroring one item to several backends** is
[§10 Open questions](#10-open-questions), Q9.

Lifecycle: `Workspace` and `Backend`, which own sessions, are context
managers (`with` / `async with`) and have `close()`, awaited in async mode.
A `Collection` is a cheap handle onto its backend's session and has none. The name is `close` in both modes, not
`aclose`, because unasync does not rewrite `aclose` ([sync/async comparison §9](SYNC_ASYNC_ARCHITECTURE.md#9-result-pagination-and-the-filesystem-backend)).

Iteration: `search` returns a list and has an `iter_search` twin that
returns an `Iterator` / `AsyncIterator`, for result sets too large to hold
at once. The other listing methods (`collections`, `tasks`, `events`,
`journals`) return lists; `changes()` returns its set in one piece. `async for` → `for` is in
unasync's table; [roadmap 0.2](ROADMAP.md#02-syncasync-architecture) did not probe it, so [roadmap 1.3](ROADMAP.md#13-syncasync-scaffolding) adds it to the freshness test.

---

## 2. The I/O classes

Signatures are given in their async form. The sync form is the same
without `async`/`await`, with `AsyncX` renamed to `SyncX` for items and to
the unprefixed name for the I/O classes. Types not defined here are in [§3 Items](#3-items)–[§7 Change detection](#7-change-detection).

### 2.1 Workspace

```python
class AsyncWorkspace:
    @classmethod
    def from_config(cls, path: str | Path | None = None, *, section: str | None = None) -> Self: ...
    def __init__(self, backends: Iterable[AsyncBackend] = ()) -> None: ...

    backends: Sequence[AsyncBackend]

    async def collections(self) -> MultiResult[AsyncCollection]: ...
    async def collection(self, name_or_id: str) -> AsyncCollection: ...   # NotFoundError, AmbiguousError
    async def search(self, searcher: Searcher | None = None, **filters: Any) -> MultiResult[AsyncItem]: ...
    # MultiResult[T]: dataclass with items: list[T], errors: Mapping[str, CalendaringError]
    # (keyed by backend id for collections(), by collection id for search()), raise_for_errors()
    async def close(self) -> None: ...
```

`from_config` is specified by [roadmap 1.4](ROADMAP.md#14-configuration-and-credentials); this document only fixes that it exists and returns
a workspace.

**Fan-out returns partial results.** `Workspace.collections` asks every
backend, and `Workspace.search` every collection, and both return a
`MultiResult` (a generic dataclass): `.items`, `.errors` (keyed by backend
or collection id), and `.raise_for_errors()`, which raises an
`ExceptionGroup` (new in Python 3.11, which is the supported minimum: [D4](PRIOR_ART_AND_DECISIONS.md#d4-python-version-floor)) if any collection failed. One unreachable server
must not blank a calendar application's agenda, which is what a plain raise
would do; but the failure must not be silent either, and the caller decides
which matters. Async mode runs the collections concurrently with a
`TaskGroup`, in the hand-written per-mode layer ([sync/async comparison §9](SYNC_ASYNC_ARCHITECTURE.md#9-result-pagination-and-the-filesystem-backend)); sync mode runs
them in turn.

### 2.2 Backend

```python
class AsyncBackend:
    @classmethod
    async def connect(cls, url: str, *, username: str | None = None,
                      password: str | None = None, token: str | None = None,
                      kind: str | None = None, session: Any = None, **options: Any) -> AsyncBackend: ...

    id: str                      # stable, from config or derived from the URL
    kind: str                    # "caldav", "files", "feed", "jmap", "gitea", ...
    capabilities: Capabilities   # backend-level: create-collection, ...
    native: object               # see "The escape hatch"

    async def collections(self) -> list[AsyncCollection]: ...
    async def collection(self, name_or_id: str) -> AsyncCollection: ...
    async def create_collection(self, name: str, components: Iterable[Component] = (Component.EVENT,)) -> AsyncCollection: ...
    async def close(self) -> None: ...
```

- `connect` returns the subclass for the kind it picks from the URL when
  `kind` is not given:
  `file:` or a path → files; `webcal:` → feed (fetched as `https:`, [prior art
  Part 2](PRIOR_ART_AND_DECISIONS.md#part-2-standards)); `http(s):` → probe, which is [roadmap 2.3](ROADMAP.md#23-icalendar-feed-backend-read-only-http)'s "feed or CalDAV" detection;
  RFC 6764 discovery for a bare domain comes from `caldav`. Trackers cannot
  be detected and need `kind=`.
- `session` is the caller-owned HTTP session that Home Assistant's
  `inject-websession` rule asks for ([prior art §1.4, Home Assistant](PRIOR_ART_AND_DECISIONS.md#14-home-assistant)). A backend that cannot use
  it raises `ConfigurationError` instead of silently making its own.
- Credentials are never required in the URL or in plain-text configuration
  ([roadmap 1.4](ROADMAP.md#14-configuration-and-credentials)'s keyring work); `connect`'s keyword arguments are for programmatic
  use.

### 2.3 Collection

```python
class AsyncCollection:
    # Items taken as arguments may be any Item (data or bound, either mode);
    # items returned are bound to this collection. add, save, reload and move are
    # overloaded per item type exactly as bind() is: Task -> AsyncTask, and so on.
    id: str
    backend_id: str
    name: str | None
    color: str | None
    components: frozenset[Component]       # what it can hold; class Component(Enum): EVENT, TASK, JOURNAL
    capabilities: Capabilities
    native: object

    # reading
    async def search(self, searcher: Searcher | None = None, **filters: Any) -> list[AsyncItem]: ...
    def iter_search(self, searcher: Searcher | None = None, **filters: Any) -> AsyncIterator[AsyncItem]: ...
    async def tasks(self, **filters: Any) -> list[AsyncTask]: ...      # search(todo=True, ...)
    async def events(self, **filters: Any) -> list[AsyncEvent]: ...
    async def journals(self, **filters: Any) -> list[AsyncJournal]: ...
    async def get(self, uid: str) -> AsyncItem: ...           # NotFoundError
    async def get_by_native_id(self, native_id: str) -> AsyncItem: ...
    async def reload(self, item: Item) -> AsyncItem: ...   # fresh copy, new etag
    async def relatives(self, item: Item, reltype: str | None = None) -> list[AsyncItem]: ...

    # writing
    async def add(self, item: Item, *, native_id: str | None = None,
                  wire: bytes | None = None,
                  loss: LossPolicy | None = None,
                  verify: bool | None = None) -> AsyncItem: ...
    async def add_task(self, *, loss: LossPolicy | None = None, verify: bool | None = None, **properties: Any) -> AsyncTask: ...   # add(Task.new(**properties))
    async def add_event(self, *, loss: LossPolicy | None = None, verify: bool | None = None, **properties: Any) -> AsyncEvent: ...
    async def add_journal(self, *, loss: LossPolicy | None = None, verify: bool | None = None, **properties: Any) -> AsyncJournal: ...
    async def save(self, item: Item, *, overwrite: bool = False, scope: Scope = Scope.THIS,
                   wire: bytes | None = None,
                   loss: LossPolicy | None = None, verify: bool | None = None) -> AsyncItem: ...
                   # scope: occurrences only, see "Recurrence"; verify: see "What happens when ... unsupported"
    async def delete(self, item: Item | str, *, overwrite: bool = False) -> None: ...
    async def complete(self, task: Task, at: datetime | None = None,
                       mode: Literal["safe", "this_and_future"] = "safe",
                       *, loss: LossPolicy | None = None, verify: bool | None = None) -> AsyncTask: ...
    async def uncomplete(self, task: Task, *, loss: LossPolicy | None = None, verify: bool | None = None) -> AsyncTask: ...
    async def move(self, item: Item, target: AsyncCollection,
                   *, loss: LossPolicy | None = None, verify: bool | None = None) -> AsyncItem: ...   # bound to target
    # every write takes loss= and verify=; None means the workspace's or backend's default

    # binding, no I/O
    def wrap(self, native_item: object) -> AsyncItem: ...    # see "The escape hatch"
    @overload
    def bind(self, item: Task) -> AsyncTask: ...
    @overload
    def bind(self, item: Event) -> AsyncEvent: ...
    @overload
    def bind(self, item: Journal) -> AsyncJournal: ...
    @overload
    def bind(self, item: Item) -> AsyncItem: ...

    # change detection
    async def changes(self, token: SyncToken | None = None, *,
                      known: Mapping[str, str] | None = None) -> ChangeSet[AsyncItem]: ...

    async def set_name(self, name: str) -> None: ...      # collection.set-name; updates self.name
    async def delete_collection(self) -> None: ...
```

**Attributes do no I/O; methods do.** `name` and `color` are what the
backend reported when it listed the collection. Assigning to them would
have to write to the server as a side effect, so renaming is an explicit
`set_name()`, which raises `UnsupportedError` where the backend cannot
rename (a feed, a single `.ics` file). The same goes for items: there is no
`collection.save()` without an argument, and no autosave (but see
[§10](#10-open-questions), Q13). This follows caldav, which settled on
methods for anything that talks to the server. *Proposed by the author in
the [PR 5 review](https://github.com/pycalendar/calendaring/pull/5#discussion_r4237526933);
open to the reviewer.*

**`add` creates, `save` updates.** `add` fails with `AlreadyExistsError` if
the UID or the name is taken (CalDAV `CALDAV:no-uid-conflict` or a 412 on
`If-None-Match: *`; `.taken` below says which). `save` sends the item's `etag`
as a precondition and fails with `ConflictError` if the stored object has
changed since it was read; `overwrite=True` drops the precondition. caldav has
a `save()` that can be used both for adding and saving, causing a risk of 
lost updates.

`add(native_id=...)` names the stored object (a CalDAV href such as
`<uuid>.ics`, a vdir file name), so a caller whose response was lost can
find its own create by that name. Where the backend assigns ids (Gitea,
JMAP) it raises `UnsupportedError(Feature.IDENTITY_CLIENT_NATIVE_ID)`
before any I/O. `AlreadyExistsError.taken` says which clash it was:
`"native_id"` (CalDAV 412 on `If-None-Match: *`) or `"uid"` (the
`no-uid-conflict` precondition: another object holds the UID). A sync tool
handles the two differently. *Asked for by calendaring-sync
([PR 19](https://github.com/pycalendar/calendaring-sync/pull/19#issuecomment-6114023463)).*

**`wire=` sends given bytes instead of a serialisation.** `add(item,
wire=data)` and `save(item, wire=data)` send `data` exactly, where a
plain write would send `item.icalendar.to_ical()`; what the server then
stores may still differ (see "The content after a write" below). Identity
and precondition come from where they always do: on `add`, from
`native_id=` and `If-None-Match`; on `save`, from the item's `native_id`
and `etag`. The library parses `data`, and before any I/O raises
`ValueError` if its `UID` or component type differs from the item's, or if
the item is a single occurrence (the bytes would be a whole stored object, or a merge
input; [§3.7](#37-recurrence)). The capability and loss checks run on what
it parsed, so `wire=` is no way around them; and since nothing is mapped,
a value the backend could only store `LOSSY` raises
`UnsupportedError(Feature.WRITE_WIRE)`: there is no loss to decide on,
only bytes the backend cannot take as they are. The returned item holds what `data` parses to.
It is for a caller that keeps bytes of its own, a sync tool pushing what
an application recorded: parsing and re-serialising would change the
bytes it compares later. Where a backend stores no iCalendar text per
item (a single `.ics` file, a feed, JMAP, Gitea) the write raises
`UnsupportedError(Feature.WRITE_WIRE)` before any I/O. `wire=` is on the
collection's `add` and `save` only: a bound item's `save()` writes the
item as it is.
*Asked for by calendaring-sync
([PR 19](https://github.com/pycalendar/calendaring-sync/pull/19#issuecomment-6114023463));
proposal, open to review.*

**Collection writes return the written item; bound methods update in
place.** `collection.save(item)` and the other collection writes return a new
bound copy with the new `etag`, and, on a backend that cannot store a
caller-chosen UID (Gitea, [§3.2 Identity](#32-identity)), a different `uid`
and a `native_id`. They do not change their argument, which may be a data
item or belong to another collection. A bound item's own methods
(`task.save()`, `task.complete()`, `task.reload()`) update the item itself
with those values, as caldav's do, so `task.save(); ...; task.save()` works
without a stale etag.

**The content after a write is what was sent, not necessarily what is
stored.** Some servers rewrite what they store and still return a strong
ETag for it (Radicale and Xandikos, found by calendaring-sync; RFC 4791
§5.3.4 says they must not). The returned `etag` is kept, because it is
what the next `If-Match` needs, but it may belong to content the item does
not hold, and `changes()` will then never report the difference. A caller
that needs the stored form, a sync tool in particular, calls `reload()`.
`verify=True` only tells it that the server changed something
([§5.3](#53-what-happens-when-the-caller-asks-for-something-unsupported)).

**`complete`** is an I/O method because completing a recurring task may
write two objects: caldav's "safe" mode completes a copy of the occurrence
and moves the master's `DTSTART`. The modes are caldav's, with one
exception: caldav guesses that an `RRULE` without `BY*` parts means "an
interval after the actual completion" (org-mode's `.+1w`). That reads into
`RRULE` something the RFC does not say, and is dropped. The next start
follows the `RRULE`, and the interval-from-completion behaviour applies only
when an `X-` property asks for it
([recurring-ical-events issue 292](https://github.com/niccokunzmann/python-recurring-ical-events/issues/292)).
The `icalendar` manipulation goes to `recurring_ical_events`, and the I/O
stays here ([§10 Open questions](#10-open-questions), Q3).

**`relatives`** fetches the objects an item's `RELATED-TO` points at (and,
for `PARENT`, the children pointing back), as caldav's `get_relatives()`
does. plann calls it 16 times, so it has to be here. Relatives outside
this collection are looked up across the backend.

**`move`** within one backend uses its own move where it has one
(CalDAV `MOVE`); between backends it is `add` to the target, then `delete`
from the source, declared `EMULATED` because it is not atomic: a failure
between the two leaves the item in both places, never in neither.

**How it fits Gitea:** a repository is a collection with `components =
{TASK}`; `events()` returns an empty list rather than raising (there are
none), `add_event(...)` raises `UnsupportedError(Feature.COMPONENT_EVENT)`.

---

## 3. Items

### 3.1 The base: a typed view over iCalendar

```python
class Item:
    icalendar: icalendar.Calendar       # the whole VCALENDAR, VTIMEZONEs included
    component: icalendar.Component      # the one that describes the item; see "What an item contains"
                                        # (a tracked subclass of Todo/Event/Journal, §10 Q13)

    uid: str                            # read-only: component's UID; identity, see "Identity"
    native_id: str | None               # backend's own id; None until stored
    etag: str | None                    # real or synthetic (see "Change detection"); None until stored
    wire: bytes | None                  # read-only: the stored object as read; see "The stored form"
    collection_id: str | None
    native: object | None               # see "The escape hatch"
    is_occurrence: bool                 # True when the item is one occurrence, not the whole stored object

    def copy(self) -> Self: ...
```

**The item does not repeat iCalendar's properties.** Per
[D6](PRIOR_ART_AND_DECISIONS.md#d6-canonical-in-memory-model) the
`icalendar` object is the storage, and its properties are read and written
through `icalendar`'s own typed properties on `component`:

```python
task.component.summary = "Write the report"
task.component.DUE = datetime(2026, 11, 1, 12, tzinfo=oslo)
task.component.end          # DUE, or DTSTART + DURATION
task.component.duration     # DUE - DTSTART
task.component.status       # icalendar.enums.STATUS
event.component.attendees
```

`icalendar` 7.3 has typed properties for nearly all of what the first
draft listed here: `summary`, `description`, `categories`, `uid`,
`organizer`, `attendees`, `created`, `last_modified`, `status`, `priority`,
`start`, `end`, `duration`, `sequence`, `location`, `url`, `related_to`,
`rrules`, `exdates`, and `RECURRENCE_ID` (upper case, like `DUE`). A
property it lacks (`PERCENT-COMPLETE`, `COMPLETED`, `ESTIMATED-DURATION`) is read as plain
iCalendar (`component.get("PERCENT-COMPLETE")`) and proposed to `icalendar`
upstream, not added here. Duplicating each property per item type in this
library would be a maintenance burden for no gain, and two typed APIs for
one value would drift apart. *Decided by the author, 2026-10-10* (in the
[PR 5 review](https://github.com/pycalendar/calendaring/pull/5#discussion_r4237509309)).

What the item does add is what iCalendar has no property for: identity and
backend state (above), `is_occurrence`, and on a task the native values of
[§3.5](#35-native-passthrough) and the `X-PYCAL-*` fields of
[§3.6](#36-properties-with-no-standard-home). There is one source of truth,
so properties the library does not know about survive a round trip
untouched (a conformance test proposed in [prior art Part 2](PRIOR_ART_AND_DECISIONS.md#part-2-standards)).

#### The stored form

`wire` holds the stored object exactly as the backend returned it when the
item was read: the body of a CalDAV `GET`, the `calendar-data` of a
`calendar-query` or `calendar-multiget` response, the content of a vdir
file. On CalDAV that means the raw response body, which `caldav` does not
keep today: it runs incoming data through `vcal.fix()`, and its
`wire_data` re-encodes that normalised copy. The CalDAV backend has to
capture the body itself, or wait for `caldav` to keep it
([caldav issue 763](https://github.com/python-caldav/caldav/issues/763)). It belongs to `etag`, and like `etag` it is a snapshot: an edit to
`component` does not change it, and a write sets it to `None`, because
what the server stores may differ from what was sent (see "The content
after a write" in [§2.3](#23-collection)); `reload()` reads it again.
`copy()` and `as_data()` keep it, as they keep `etag`; equality ignores it.

It is `None` where no bytes are this item's alone: on a data item made
from scratch; on a single occurrence, which is one component of a stored
object (and on anything the server expanded, which is generated data under
the stored object's ETag); and on backends that store no object of their
own iCalendar text per item (a single `.ics` file, a feed, JMAP, Gitea).

This keeps [D6](PRIOR_ART_AND_DECISIONS.md#d6-canonical-in-memory-model):
`icalendar` is still the storage, and nothing reads properties from
`wire`. It is there for a caller that has to compare or keep what the
server sent, a sync tool fingerprinting stored objects in particular, which
a re-serialisation would defeat: `to_ical()` normalises line folding,
property order and parameter quoting. Writing given bytes is `wire=` on
[`add` and `save`](#23-collection). *Asked for by calendaring-sync
([PR 19](https://github.com/pycalendar/calendaring-sync/pull/19#issuecomment-6114023463));
proposal, open to review.*

#### What an item contains

The `icalendar` attribute is always a whole `VCALENDAR`, because that is how
CalDAV stores and transfers an item, but it is never a calendar in the sense
of a collection. An item holds one component type (plus `VTIMEZONE`s) and one
`UID`, as RFC 4791 §4.1 requires of a stored object, and it is exactly one of
these three:

| The item is | `icalendar` holds | `component` | `is_occurrence` | `RECURRENCE-ID` on `component` | caldav [#398](https://github.com/python-caldav/caldav/issues/398) |
|---|---|---|---|---|---|
| a non-recurring item | one component | that component | `False` | absent | 100 |
| a full recurrence set | the master (`RRULE` or `RDATE`) and all its overridden occurrences, if any | the master | `False` | absent | 110, 011 |
| a single occurrence | one component: a generated occurrence, or an override | that occurrence | `True` | set | 101 |

**On a full recurrence set, `component` is the master only, and writing
through it is a trap.** `item.component.summary = x` does what it says on a
non-recurring item, on a single occurrence, and on a series without
overrides. On a full recurrence set with overrides, each override is a
whole component that RFC 5545 uses *instead of* the generated occurrence,
so the write renames the series except for the overridden occurrences,
which keep the old summary. Moving the master's `DTSTART` is worse: the
overrides' `RECURRENCE-ID`s no longer match a generated occurrence.
Nothing raises, and the result looks right until someone opens one of the
overridden occurrences. Until a series edit exists, a caller changing a
series with overrides has to edit the overrides too, through
`item.icalendar`. The fix is left to edit-icalendar
([§10](#10-open-questions), Q14).

**A single occurrence is part of a stored object, not all of it.** The
stored object may also hold the master and other overrides that the item
does not carry. Occurrences come from three places:

- `search(expand=True)`, client-side;
- a server that expanded the series itself. CalDAV's `expand` returns only
  the occurrences in the time range, so tomorrow's agenda does not download
  decades of overrides from a long series. Whether to ask the server is the
  backend's choice ([§9](#9-migrating-from-caldav), `server_expand`);
- a stored object that holds overrides without their master, which RFC 4791
  §4.1 allows, e.g. for an attendee invited to some instances only. The
  backend hands out one occurrence item per override.

Several occurrences of one series are several items. They share a `uid`,
and on a stored series they share a `native_id` and an `etag` too.
`save(occurrence)` merges the occurrence back into the stored object
([§3.7 Recurrence](#37-recurrence)), and how `delete()` and conflict
detection treat siblings is [§10](#10-open-questions), Q12.

What an item never holds:

- **several `UID`s.** A `.ics` file or a feed with many items is a
  collection, and the backend splits it into items.
- **several occurrences without their master** (001 in caldav
  [#398](https://github.com/python-caldav/caldav/issues/398)). A CalDAV
  server answering an expanded time-range query returns, per stored object,
  every occurrence in the window. caldav's `search()` splits that into one
  object per occurrence by default (`split_expanded=True`) and keeps them
  together only on request. Here the split is the only behaviour.
- **no data.** An item comes from a factory or a read, never as an empty
  shell to be loaded later.

caldav [#597](https://github.com/python-caldav/caldav/issues/597) asks for
helpers that tell these cases apart, and the table is the list such helpers
would answer. `is_occurrence` is the one that matters most, because it says
that the item is not the whole stored object. Whether to add `is_recurring`
and the like as convenience properties is left to
[roadmap 1.2](ROADMAP.md#12-abstract-base-classes-and-the-backend-conformance-suite).

These are the mode-free data classes. The bound subclasses add only their collections and the delegating I/O methods
([§1.1 Sync and async](#11-sync-and-async)):

```python
class AsyncItem(Item):                                            # in _async/; unasync makes SyncItem
    collections: tuple[AsyncCollection, ...]                      # exactly one for now (Q9); checked by bind()/wrap()
    collection: AsyncCollection                                   # property: collections[0]
    async def save(self, *, overwrite: bool = False, scope: Scope = Scope.THIS,
                   loss: LossPolicy | None = None, verify: bool | None = None) -> None: ...   # updates self: etag, uid, native_id
    async def delete(self) -> None: ...
    async def reload(self) -> None: ...                           # replaces self's data in place
    async def relatives(self, reltype: str | None = None) -> list[AsyncItem]: ...
    def as_data(self) -> Item: ...                                # mode-free copy, no collection

class AsyncTask(Task, AsyncItem):
    async def complete(self, at: datetime | None = None,
                       mode: Literal["safe", "this_and_future"] = "safe",
                       *, loss: LossPolicy | None = None, verify: bool | None = None) -> None: ...
    async def uncomplete(self, *, loss: LossPolicy | None = None, verify: bool | None = None) -> None: ...
class AsyncEvent(Event, AsyncItem): ...      # exists; no methods beyond the shared ones
class AsyncJournal(Journal, AsyncItem): ...  # likewise
# unasync generates SyncItem, SyncEvent, SyncTask and SyncJournal from these.
```

Rules for bound items:
- `copy()` returns a copy bound to the same collections; `as_data()` returns
  an unbound one.
- Equality compares the data (the iCalendar content), never the collections.
- Pickling a bound item raises `TypeError`, because it would carry a live
  session; pickle `as_data()` instead.
- `relatives()` returns each relative bound to the collection it was found
  in. `collection.move()` returns the item bound to the target.

Factories, which library users call instead of constructors:
`Task.new(summary=..., end=..., **properties)`, `Task.from_ical(data)` (a
string or bytes) and `Task.from_icalendar(calendar)` (an
`icalendar.Calendar` already parsed; the item stores a converted copy, so
later changes to the caller's object do not reach it, §10 Q13). The `uid` defaults to a fresh UUID,
as `icalendar.Todo.new` does. Bound items come only from a collection
([§1.1 Sync and async](#11-sync-and-async)).

### 3.2 Identity

Three concepts, following [survey 3.2](TASK_MODEL_SURVEY.md#32-dimension-by-dimension):

| | Meaning | CalDAV / files | Gitea |
|---|---|---|---|
| `uid` | the iCalendar `UID` | caller-chosen, round-trips | synthesised: `"{issue number}@{host}/{owner}/{repo}"`, stable but not caller-chosen |
| `native_id` | the backend's own key | the resource href | the issue number |
| foreign-id slot | can the backend persist the caller's UID? | — (UID is native) | no; Kanboard's `reference` would be one |

Two capabilities say which applies: `identity.client-uid` (the backend
stores the caller's UID as given) and `identity.foreign-id` (it can store it
in a side slot and find the item by it). A third, `identity.client-native-id`,
says whether the caller may also choose the `native_id`
(`add(native_id=...)`, [§2.3](#23-collection)). Gitea declares all three `UNSUPPORTED`:
a task added with UID `abc` comes back with the synthesised one, and
`get("abc")` raises `NotFoundError`. A caller that needs to recognise its
own items there has to keep the mapping itself. This is the place where a
sync tool built on this library learns that it cannot assume UIDs
round-trip, and the conformance suite asserts it.

### 3.3 Event and Journal

```python
class Event(Item): ...                  # component is an icalendar.Event
class Journal(Item): ...                # component is an icalendar.Journal
```

No fields of their own: `component.start`, `component.end`,
`component.duration`, `component.location` and `component.rrules` are
`icalendar`'s. The classes exist so that a collection can say what it
returns and a helper can say what it takes.

### 3.4 Task

Incorporates [survey Part 4](TASK_MODEL_SURVEY.md#part-4-a-proposed-task-model).

```python
class TaskStatus(StrEnum):              # values are the iCalendar strings
    PENDING = "PENDING"                 # tasks draft
    NEEDS_ACTION = "NEEDS-ACTION"
    IN_PROCESS = "IN-PROCESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"                   # tasks draft

class Task(Item):                       # component is an icalendar.Todo
    native_status: str | None           # see "Native passthrough"; not in the iCalendar data
    native_priority: str | None         # likewise
    planned_start: datetime | None      # X-PYCAL-PLANNED-START (see "Properties with no standard home")
    planned_end: datetime | None        # X-PYCAL-PLANNED-END
    # time_log, time_spent: added by roadmap 1.6

    def set_duration(self, duration: timedelta, keep: Literal["start", "due"] = "due") -> None: ...
```

Everything else is a property of the `VTODO`, read and written through
`component`. `TaskStatus` is the vocabulary backends map `STATUS` to and
from. It is not a field: `icalendar.enums.STATUS` has no `PENDING` or
`FAILED`, which the tasks draft adds, so those two are a candidate for
`icalendar` too. `set_duration()` is a method rather than a property
because it changes two of them.

Decisions in it, each from the survey:

- **`DTSTART` means earliest sensible start.** That is the tasks draft's
  reading and the first in the survey's table; the other senses the survey
  found (planned start, expected completion) get their own properties instead
  of overloading it. Actual start comes from the time log ([roadmap 1.6](ROADMAP.md#16-time-tracking-model-and-api)), not from a field.
- **`duration` is derived, not stored.** On a task, `DURATION` is either
  `DUE − DTSTART` or a misused estimate. `icalendar`'s `Todo.duration`
  reads `DUE − DTSTART` (or `DURATION` when only that is present), and
  `task.set_duration(d, keep="due" | "start")` moves the other end, as
  caldav's `set_duration(movable_attr=…)` does; plann uses both. The
  default keeps `DUE`, as caldav's does, because a deadline is more often
  fixed than a start. The
  estimate is `ESTIMATED-DURATION`, never `DURATION`.
- **`remaining` is left out** ([survey §4.4](TASK_MODEL_SURVEY.md#44-details-to-be-decided-later)). Two of the nine systems carry
  it, neither is a funded backend, and every field the library defines is a
  mapping obligation on every backend. It is reachable through the escape hatch on
  the backends that have it, and can be added later without breaking
  anything.
- **`priority` is iCalendar's 0–9.** The survey found five incompatible
  scales; the mapping to each is the backend's, documented as lossy.
  plann's semantics for 1–9 sit on top of this and are plann's.
- **Assignees are the task's `ATTENDEE`s** (`component.attendees`), as on
  any other component. The tasks draft (section 6) says it in so
  many words: "Tasks are assigned to actors using one or more RFC5545
  'ATTENDEE' properties and/or one or more RFC9073 'PARTICIPANT'
  calendar components." So the standard has no gap here, and no `X-`
  property is needed. What looks like a gap is that a tracker knows a
  login (`alice`), while `ATTENDEE` takes a calendar address. A calendar
  address is any URI, not only `mailto:` (RFC 5545 §3.3.3), so the Gitea
  mapper writes the user's profile URL:
  `ATTENDEE;CN=Alice:https://gitea.example.com/alice`. That is valid,
  unique, resolvable, and round-trips, with no fake e-mail address. Mapping
  logins to and from those URIs is the backend's job, and
  there is no separate assignees field, to avoid two names for one
  thing. The name, role and participation status are the `ATTENDEE`'s
  `CN`, `ROLE` and `PARTSTAT` parameters, on `icalendar`'s `vCalAddress`.

### 3.5 Native passthrough

`native_status` and `native_priority` are a plain `str | None`, set by the
backend that read the item, and **not serialised into the iCalendar
data** — a Kanboard column name means nothing to a CalDAV server. The rule
that makes them round-trip: the backend remembers the `STATUS` (or
`PRIORITY`) it wrote into the item along with the native value. On save,
if the property still has that value, the backend uses the native value;
if the caller changed it, the backend maps the new value. So a card read
from the "Review" column (normalised `IN-PROCESS`) and saved unchanged
stays in "Review", and one that the caller sets to `COMPLETED` goes
wherever the backend maps `COMPLETED`. Comparing on save, rather than
clearing the native value in a setter, also catches a change made through
`item.icalendar` directly.

This answers [survey §4.4](TASK_MODEL_SURVEY.md#44-details-to-be-decided-later)'s question "plain string or typed object": a plain
string. Legal transitions (RT, OpenProject) are reachable through `native`
and are not part of the model.

### 3.6 Properties with no standard home

[D6](PRIOR_ART_AND_DECISIONS.md#d6-canonical-in-memory-model) sets the order: an RFC 5545 property; then RFC 9253 or the tasks draft;
then an `X-` property under one documented prefix. **The prefix is
`X-PYCAL-`**, after the project's name, [pycal.org](https://pycal.org)
(the GitHub organisation is `pycalendar` only because `pycal` was taken),
not `X-CALENDARING-`:

- `plann` and the other pycal tools will write the same properties, and a
  project prefix is not wrong for them;
- the package name may change (see the top of this document), and once
  written into users' calendars a prefix cannot.

In this document: `X-PYCAL-PLANNED-START`, `X-PYCAL-PLANNED-END`.
[roadmap 1.6](ROADMAP.md#16-time-tracking-model-and-api) adds its own. Each gets a line in a
registry table in the user documentation ([roadmap 4.1](ROADMAP.md#41-documentation-structure-and-api-reference)), with the standard property
that would replace it if one appears.

### 3.7 Recurrence

**Words.** A *recurring* object is one with an `RRULE` or `RDATE`. An
*occurrence* is one instance of it: the Wednesday 10:00 meeting on one
particular Wednesday, identified by its `RECURRENCE-ID`. RFC 5545 calls an
occurrence a "recurrence instance", and caldav calls it a "recurrence"
(`save(only_this_recurrence=…)`). This document says "occurrence" because
"recurrence" also means the repetition itself (the rule, "the recurrence
set"), and the two senses get mixed up.

`search(..., expand=True)` returns occurrences, one item per occurrence:
items with `is_occurrence` set and a `RECURRENCE-ID` on `component`, expanded by
`recurring_ical_events` via `icalendar-searcher`. Several occurrences of
one series in the search window are several items with the same `uid`
([What an item contains](#what-an-item-contains)).

**Editing one occurrence works as it does in caldav.** `save(occurrence)`
fetches the stored object, inserts or replaces the override component for
that `RECURRENCE-ID`, bumps `SEQUENCE` and saves the whole object. When the
stored object has no master (overrides only), the override is replaced in
place. That is what
caldav's `save(only_this_recurrence=True)`, its default, does, and what the
recurring-ical-events user guide shows ("Edit one event of an existing
series"). `save(occurrence, scope=Scope.ALL)` (`class Scope(Enum)` with
`THIS`, `ALL` and `THIS_AND_FUTURE`; default `THIS`) applies the change to the
master instead, which is caldav's `all_recurrences=True`; with no master
stored there is nothing to apply it to, and it raises. The merge is
`icalendar` manipulation with no I/O, so it works the same on every backend
that stores `RRULE` (`recurrence` capability). Backends that do not
(Gitea) never produce occurrences.

**Not supported: "this and future" for events**, which splits a series in
two. caldav does not offer it either, `ical`'s `store.py` is the reference
for it ([prior art §1.3, `ical`](PRIOR_ART_AND_DECISIONS.md#13-ical-allen-porter));
by [D1](PRIOR_ART_AND_DECISIONS.md#d1-packaging-principle) it does not belong in `caldav`, and it is assumed to go to [edit-icalendar](https://github.com/pycalendar/edit-icalendar) (a draft) ([§10 Open questions](#10-open-questions), Q3). edit-icalendar is AGPL-3.0 and will be a hard dependency anyway, which is why an AGPL licence for this whole library is being considered ([D3](PRIOR_ART_AND_DECISIONS.md#d3-licence)). `save(occurrence, scope=Scope.THIS_AND_FUTURE)` raises
`UnsupportedError(Feature.RECURRENCE_EDIT_THIS_AND_FUTURE)` until that package can split a series.

**Completing one occurrence of a recurring task** is `task.complete(mode=...)`
([§2.3 Collection](#23-collection)), with caldav's two modes, `safe` and
`this_and_future`. (caldav
declares `"this_and_future"` but only accepts `"thisandfuture"`: it looks
up `_complete_recurring_<mode>`, and that method is spelled
`_complete_recurring_thisandfuture`. The CalDAV backend passes the working
spelling, and the mismatch is reported as
[caldav issue 735](https://github.com/python-caldav/caldav/issues/735).)
Completing the occurrence is `icalendar` manipulation, which goes to
`recurring_ical_events`; fetching and storing stay here
([§10 Open questions](#10-open-questions), Q3).

[§9 Migrating from caldav](#9-migrating-from-caldav) compares the whole of
caldav's API with this one.

---

## 4. Search

```python
await cal.search(todo=True, start=..., end=..., expand=True)

s = Searcher(todo=True, include_completed=False)
s.add_property_filter("CATEGORIES", "work")
s.add_sort_key("DUE")
await cal.search(s)
```

The keyword form builds a `Searcher` from its constructor fields (`todo`,
`event`, `journal`, `start`, `end`, `alarm_start`, `alarm_end`,
`include_completed`, `expand`), as caldav's `search()` does. Anything more
goes through a `Searcher` object. There is no wrapper: the type is
`icalendar_searcher.Searcher`, imported from there, so caldav's
`CalDAVSearcher` subclass works as is.

**The rule (A3):** a backend translates as much of the searcher as it can
into a server-side query, and the generic layer then runs
`searcher.filter()` over everything the server returned. A server-side
query may therefore over-match but never under-match, and the result is the
same whatever the server did. The conformance suite tests exactly this: the
same data and searcher against each backend, the same answer. caldav's
`CalDAVSearcher` already works this way for CalDAV (server query, client
post-filter); [roadmap 2.1](ROADMAP.md#21-caldav-backend) keeps it.

The cost is bandwidth when a server filters badly, and it is the right
trade: a filter that behaves differently per backend is the problem this
library exists to remove. A caller who knows better uses the escape hatch.

Consequences:

- **Sorting** is done client-side by the searcher, so a sorted `search`
  reads every page before returning. `iter_search` with sort keys does the
  same before yielding the first item; without sort keys it streams.
- **Tracker-native filters** (Gitea milestone, assignee by login) are not
  in the searcher's vocabulary. They are reachable through the escape hatch
  until the searcher grows a way to express them.
- **No `limit` / `offset`.** `Searcher` has none, and a limit applied
  before the client-side filter would be wrong. `iter_search` is the way to
  stop early.
- **Naive datetimes are local time**, as `icalendar-searcher` assumes.

**How it fits Gitea:** its issue search API takes state, labels, a
milestone and a since-timestamp. The backend translates `todo=True,
include_completed=False` into `state=open` and `CATEGORIES` into labels,
fetches, and lets the searcher do the rest. Date ranges are client-side
only, and declared so (`search.time-range: EMULATED`).

---

## 5. Capabilities

### 5.1 The table

```python
class Support(Enum):
    FULL = "full"                # works, no information lost
    LOSSY = "lossy"              # works, but a value is mapped or truncated
    EMULATED = "emulated"        # done client-side; same result, more I/O or not atomic
    UNSUPPORTED = "unsupported"  # raises UnsupportedError
    UNKNOWN = "unknown"          # not probed; the operation is attempted

@dataclass(frozen=True)
class Capability:
    level: Support
    details: Mapping[str, Any] = field(default_factory=dict)  # how it is supported; keys per feature
    note: str | None = None               # free text for the capability matrix

class Capabilities(Mapping[Feature, Capability]):
    def supports(self, feature: Feature, *, allow_lossy: bool = False) -> bool: ...  # the boolean view
    def level(self, feature: Feature) -> Support: ...                              # the string view
    def details(self, feature: Feature) -> Mapping[str, Any]: ...                  # the full view
```

`Support` is an enum, not a set of behaviour objects, because it mostly
describes: it feeds the capability matrix and `supports()`, and only
`UNSUPPORTED` changes what an operation does. `LossPolicy`, whose values
*are* behaviours, is an object ([§5.3](#53-what-happens-when-the-caller-asks-for-something-unsupported)).

**A yes/no answer is not enough, and neither is a level alone.** That is
caldav's experience with its compatibility matrix, where a value is a
boolean, a string or a dict, and helper methods reduce a dict to a boolean
or a string for code that only cares about that. This is the same idea with
a fixed shape. Every entry has a level, which callers branch on, and an
optional `details` mapping for how a feature is supported. For example:

| Feature | Level | `details` |
|---|---|---|
| `task.priority` on Taskwarrior | `LOSSY` | `{"values": [1, 5, 9]}` (H/M/L) |
| `task.status` on Gitea | `LOSSY` | `{"values": ["NEEDS-ACTION", "COMPLETED"]}` |
| `changes` on CalDAV without sync-token | `EMULATED` | `{"method": "etag-listing"}` |
| `search.time-range` on a CalDAV server that ignores it for tasks | `EMULATED` | `{"components": ["VEVENT"]}` |

In configuration files a capability can be given in the same three shapes
caldav accepts, `true`/`false`, a level string, or a dict with `level` and
the details, and is normalised to a `Capability` on load. The details keys
are documented per feature, so the capability matrix (roadmap 4.3) can
print them, and the conformance suite can use them. For example, it
asserts that a `LOSSY` priority comes back as one of the declared `values`.

`Feature` is a `StrEnum` with dotted values, so that it can be written
in configuration files and in the generated capability matrix ([roadmap 4.3](ROADMAP.md#43-backend-capability-matrix)). The
initial set:

| Feature | Meaning |
|---|---|
| `write` | create, update and delete items at all |
| `component.event`, `component.task`, `component.journal` | can hold that component |
| `create-collection`, `delete-collection` | backend-level |
| `collection.set-name` | `set_name()` |
| `search.server-side`, `search.time-range`, `search.text` | how much the server filters; never affects results ([§4 Search](#4-search)) |
| `changes` | `changes()`: `FULL` with a native token, `EMULATED` by listing |
| `changes.known` | `changes(known=...)` fetches only what changed: `FULL` where etags can be listed without content (CalDAV, vdir), `EMULATED` where everything is read and compared (JMAP, single `.ics` file, feed, Gitea) |
| `write.wire` | `add(wire=...)`, `save(wire=...)`: given bytes are sent as they are; `FULL` on CalDAV and vdir, `UNSUPPORTED` elsewhere |
| `write.conditional` | `FULL` with an atomic precondition (ETag, `content_version`); `EMULATED` read-compare-write |
| `identity.client-uid`, `identity.foreign-id` | [§3.2 Identity](#32-identity) |
| `identity.client-native-id` | `add(native_id=...)`: the caller may name the stored object (CalDAV href, vdir file name) |
| `properties.passthrough` | unknown properties and components survive a round trip |
| `recurrence`, `recurrence.edit-this-and-future` | stores `RRULE`; [§3.7 Recurrence](#37-recurrence) |
| `move` | [§2.3 Collection](#23-collection) |
| `task.status`, `task.priority`, `task.percent-complete` | `LOSSY` when values are mapped |
| `task.start`, `task.due`, `task.completed`, `task.planned`, `task.estimate` | |
| `task.relations.parent`, `task.relations.depends-on` | |
| `categories`, `attendees` | for every component; on a task, attendees are its assignees |

Feature names are identifiers, not attribute paths: `task.due` means "stores a
task's `DUE`", which is read as `task.component.DUE`, and `task.estimate`
means `ESTIMATED-DURATION`.

[roadmap 1.6](ROADMAP.md#16-time-tracking-model-and-api) adds `task.time-log` and friends. Below 1.0 the list grows as backends need
it ([§10 Open questions](#10-open-questions), Q7). The conformance suite
iterates over `Feature`, and a backend's declaration must cover every member
(missing = test failure, not a silent default).

It is a table rather than an `IntFlag` like Home Assistant's, because a
flag is yes/no and the survey's most common answer is "yes, lossily". It has
fewer levels than caldav's `FeatureSet`, which describes *servers* (including
fragile, broken and ungraceful ones) for the client's workarounds. The CalDAV
backend derives this table from caldav's: `full`/`quirk` → `FULL`; anything
caldav works around client-side → `EMULATED`; `unsupported`, `broken`,
`ungraceful` → `UNSUPPORTED`; `fragile`, `unknown` → `UNKNOWN`.

`UNKNOWN` exists because most CalDAV servers have never been probed, and a
library that refused anything unprobed would be unusable against them.
Non-CalDAV backends are code, not servers, and the conformance suite
rejects `UNKNOWN` from them.

### 5.2 Where it lives

On each collection, and on each backend for the backend-level features. A
collection can narrow its backend's (a feed is read-only; one CalDAV
calendar takes only `VTODO`), never widen it.

### 5.3 What happens when the caller asks for something unsupported

Three cases, matching the roadmap's "raise, degrade, or emulate":

- **Unsupported operation → raise, before I/O.** `UnsupportedError`
  carries the `Feature`. Checked at the boundary, as Home Assistant does
  with its service-call validation, so nothing is half done.
- **Lossy write → depends on the `LossPolicy`, default `RAISE`.** The
  backend's mapper runs before the write is sent and returns a list of what
  it could not store (a priority of 3 on a backend with three levels, a
  fourth status, a `DEPENDS-ON` to Gitea's API version without
  dependencies). The policy is then called with that list. It is an object,
  not an enum, so that each behaviour lives in one place and a caller can
  supply its own (the strategy pattern, suggested in the
  [PR 5 review](https://github.com/pycalendar/calendaring/pull/5#discussion_r4237585911)):

  ```python
  @dataclass(frozen=True)
  class Loss:
      property: str                     # e.g. "PRIORITY", "STATUS", "RELATED-TO"
      sent: object                      # the value in the item
      stored: object | None             # what the backend will store instead; None if dropped

  class LossPolicy(Protocol):
      def __call__(self, losses: Sequence[Loss], item: Item) -> None: ...
          # return: the write goes ahead; raise: nothing is sent

  RAISE: LossPolicy                     # raises LossyWriteError(losses)
  WARN: LossPolicy                      # emits LossyWriteWarning, then returns
  ALLOW: LossPolicy                     # returns
  ```

  The three stock policies live in `calendaring.loss`. A caller's own policy
  can log the losses, collect them for a report, or allow some properties
  and raise on others. The policy is set per workspace or backend and can
  be overridden per call (`loss=`). A configuration file names one of the
  stock three (`raise`, `warn`, `allow`). Default `RAISE`, because the alternative is the Home Assistant failure ([prior art §1.4, Home Assistant](PRIOR_ART_AND_DECISIONS.md#14-home-assistant)): `IN-PROCESS` silently becoming `needs_action`. A warning is also
  what [roadmap 1.3](ROADMAP.md#13-syncasync-scaffolding)'s `-W error` test runs turn back into a failure.
- **Emulation → automatic, only when indistinguishable.** Client-side
  filtering, change listing and cross-backend move are emulated without
  asking, because the caller gets the same answer ([§4 Search](#4-search)) or a documented
  weaker guarantee (`write.conditional` emulated has a race window). No
  emulation that changes a result is ever automatic.

The `RAISE` default for lossy writes is confirmed by the author (2026-10-10).

What the library cannot catch: a server that declares `UNKNOWN` and then
silently drops a property. caldav's hints call that `unsupported`; only a
probe (caldav-server-tester) or a read-back finds it. So every write takes
`verify=True`, from the first release: after the write the item is
reloaded and compared with what was sent. The comparison covers every
property that was sent, after normalisation, except those the mapper had
already reported as not storable (a lossy write, above), and skips what
servers legitimately change: `DTSTAMP`, `SEQUENCE`, property
order, and the identity a backend assigns (`uid`, `native_id`, `etag`;
[§3.2 Identity](#32-identity)). A difference raises `VerificationError`
listing what the server dropped or changed. It is not a `LossyWriteError`:
that one means nothing was sent, while a `VerificationError` means the
write happened and the stored result differs. It costs one extra
round trip, so it is off by default, and a caller can turn it on per call
or per workspace or backend, like `loss=`.

---

## 6. Errors

```
CalendaringError
├── ConfigurationError            # roadmap 1.4; also a session the backend cannot use
├── AuthenticationError           # 401, bad token
│   └── AuthorizationError        # 403
├── NotFoundError                 # also a LookupError
├── AmbiguousError                # a name matched several collections
├── ConflictError                 # a precondition failed; itself: changed since read
│   ├── AlreadyExistsError        # add(): .taken is "uid" or "native_id"
│   └── SyncTokenError            # changes(token): the backend can no longer honour the token
├── UnsupportedError              # carries .feature
│   └── LossyWriteError           # carries .losses; raised before anything is sent
├── InvalidDataError              # the backend rejected the data, or it does not parse; also a ValueError
├── RateLimitError                # carries .retry_after: float | None
├── VerificationError             # verify=True: written, but stored differently; carries .differences
└── BackendError                  # anything else from the backend or transport
    └── TransportError            # network, TLS, timeout

CalendaringWarning
└── LossyWriteWarning
```

- Every error carries `.backend` and, where known, `.collection_id`.
- **The native exception is always `__cause__`** (`raise … from exc`), so
  a caller can reach `caldav.lib.error.DAVError` or an HTTP response
  without the hierarchy pretending it does not exist.
- `NotFoundError` is also a `LookupError` and `InvalidDataError` a
  `ValueError`, so generic code that catches the built-ins still works.
- `RateLimitError` is raised, not retried. Retrying belongs to the
  hand-written per-mode layer ([roadmap 0.2](ROADMAP.md#02-syncasync-architecture): `asyncio.sleep` must not be in
  `_async/`), and whether it retries is a workspace option ([roadmap 1.4](ROADMAP.md#14-configuration-and-credentials)).
- A `TransportError` during a write means the outcome is unknown: the
  write may have been stored. It is never retried, whatever the retry
  option says. The caller finds out with `reload()` after a `save`, with
  `get_by_native_id()` after `add(native_id=...)`, and otherwise with
  `get(uid)` where UIDs round-trip ([§3.2](#32-identity)).
- `SyncTokenError` is raised whatever status the server used for an
  expired or never-issued token: 403 with `DAV:valid-sync-token`, 412
  (Xandikos), or a 500 from a server known to answer a bad token that way
  (Xandikos and CCS, flagged by caldav's `sync-token.invalid-token`
  feature). Only `changes(token)` raises it. Kept apart from a failed
  `If-Match`, because the remedy differs: start again with `changes()`, not
  reload the item. It is still a `ConflictError`, as `AlreadyExistsError`
  is, because all three are a failed precondition; a handler that reloads
  on `ConflictError` should catch the subclasses first.
- Fan-out ([§2.1 Workspace](#21-workspace)) does not raise; `MultiResult.raise_for_errors()` raises an `ExceptionGroup` of these.

caldav's errors map one to one where they overlap:
`NotFoundError` → `NotFoundError`, `ETagMismatchError` and
`ScheduleTagMismatchError` → `ConflictError`; in `changes(token)`, a
`DAVError` carrying the `DAV:valid-sync-token` precondition, any 412, and a
500 from a server flagged as above → `SyncTokenError`;
`AuthorizationError` → `AuthenticationError` or `AuthorizationError` by
status code, `RateLimitError` → `RateLimitError`, other `DAVError` →
`BackendError`.

---

## 7. Change detection

Adopts vdirsyncer's contract ([prior art §1.2, vdirsyncer](PRIOR_ART_AND_DECISIONS.md#12-vdirsyncer-khal-and-todoman)), extended to collections.

**Every stored item has an `etag`**, real or synthetic:

| Backend | `etag` | `write.conditional` |
|---|---|---|
| CalDAV | the server's ETag | `FULL` (`If-Match`) |
| files, vdir | `f"{st_mtime_ns};{st_ino}"` | `EMULATED`: compare, then atomic rename |
| files, single `.ics` | hash of the item's serialisation | `EMULATED` |
| feed | hash of the item's serialisation | n/a (read-only) |
| JMAP | hash of the item's JSCalendar object | `EMULATED` |
| Gitea | `content_version`, plus `updated_at` | `FULL` for the body, `EMULATED` for the rest ([§10 Open questions](#10-open-questions), Q6) |

JMAP has no per-object version. Its `state` covers every object of one
data type in the account, so used as an etag it would mark every item
changed after any write, and `ifInState` on `/set` is account-wide in the
same way (RFC 8620 §5.1, §5.3; pointed out in the
[PR 5 review](https://github.com/pycalendar/calendaring/pull/5#discussion_r4242767102)).
Hence a hash, and a read-compare-write that can race.

**Every collection answers `changes(token)`:**

```python
@dataclass(frozen=True)
class ChangeSet(Generic[T]):            # outside _async/; T is the bound item class of the mode
    changed: list[T]                    # new or modified since token (or known), bound to the collection
    deleted: list[str]                  # native ids (CalDAV: hrefs) removed since token (or from known); a removal report names no UID
    token: SyncToken                    # SyncToken = NewType("SyncToken", str); opaque, persist it and pass it back

cs = await cal.changes()                # everything, plus a token
cs = await cal.changes(cs.token)        # what changed since
```

- CalDAV: RFC 6578 sync-token where the server supports it (`FULL`),
  otherwise emulated by caldav from an ETag listing. `changes()` without a
  token lists the members with `PROPFIND` and takes the token from the
  collection's `DAV:sync-token` property (RFC 6578 §4), read before the
  member listing or in the same `Depth: 1` request, so a change made in
  between is reported again by the next `changes(token)` instead of lost;
  not from a
  `sync-collection` report with an empty token: Stalwart truncates that
  report with a 507 and hands back its newest token, so following it loses
  the rest of the calendar. The `PROPFIND` way worked on all five servers
  calendaring-sync tests with
  ([PR 5 review](https://github.com/pycalendar/calendaring/pull/5#discussion_r4242773257)).
- JMAP: `CalendarEvent/changes` and `Task/changes` (RFC 8620 §5.2). Their
  state covers the whole account and data type, not one calendar. A
  collection keeps its own list of ids, carried in the token as for files.
  It reports as changed the updated ids whose `calendarIds` include it, and
  as deleted the ids on its list that were destroyed or no longer include
  it: an event moved to another calendar comes back as updated, not
  destroyed.
- Files: the token is a compact encoding of `{native_id: etag}`; changes are a
  re-scan diffed against it (`EMULATED`).
- Feed: HTTP `ETag`/`Last-Modified` short-circuits "nothing changed";
  otherwise a diff of item hashes, as for files.
- Gitea: `since=` on the issues API finds changed items; finding *deleted*
  ones needs a listing of all ids, so `EMULATED`.

Removals are reported by `native_id`, not `uid`: an RFC 6578 report names
only the href of a removed member, and a server can hold several objects
with one UID (calendaring-sync's design reached the same conclusion; see
[§10 Open questions](#10-open-questions), Q11).

A token is valid only for the collection that issued it. A token the
backend can no longer honour (CalDAV `valid-sync-token` precondition, a
files token from a different directory) raises `SyncTokenError`, a
`ConflictError` ([§6 Errors](#6-errors)), and the caller starts again with
`changes()`.

**A full listing can skip what the caller already has.** `changes(known=
{native_id: etag})` is a full listing compared with what the caller holds:
`changed` has the items that are new or whose etag differs, and only those
are fetched; `deleted` has the ids in `known` that are gone; every other id
in `known` is unchanged. It returns a token like any other call. Without
`known`, `changes()` reports every item as changed, as above. On CalDAV the
listing is the `PROPFIND` above, with `DAV:getetag`, and only the changed
members are fetched, in one `calendar-multiget` (RFC 4791 §7.9), so a token
reset downloads what changed rather than the whole calendar. `known` with
a token raises `ValueError`: an incremental report has its own baseline.
Etags the caller got back from its own writes may belong to content the
server rewrote (see "The content after a write" in [§2.3](#23-collection)), so
`known=` built from them will not report the rewrite.
*Asked for by calendaring-sync, whose full listing works this way
([PR 19](https://github.com/pycalendar/calendaring-sync/pull/19#issuecomment-6114023463));
proposal, open to review.*

**A change set is always complete.** A truncated page (CalDAV 507, RFC 6578
§3.6; JMAP `hasMoreChanges`) is followed inside `changes()`, which returns
only when it has everything. So `ChangeSet` needs no "complete or
truncated" flag, and no "full listing or incremental" flag either:
`changes()` without a token is always a full listing, `changes(token)`
always incremental, and an expired token raises rather than falling back to
a listing. A listing with `known` is complete too, but as a difference: an
adapter that treats a full listing as "anything not mentioned is gone" must
read `known` minus `deleted` as still there. The cost is memory on a very large calendar. calendaring-sync
keeps both flags in its own change set; an adapter built on this one sets
them from the call it made.

For Gitea, `updated_at` is the weakest change signal in the survey. The
synthetic etag adds `content_version`, so "changed twice within the clock
resolution" is seen at least for the issue body; whether it covers other
fields is checked in roadmap 2.5 ([§10 Open questions](#10-open-questions), Q6).

---

## 8. The escape hatch

Every `Backend`, `Collection` and item has `.native`:

| Backend | `Backend.native` | `Collection.native` | item `.native` |
|---|---|---|---|
| CalDAV | `caldav.DAVClient` / `AsyncDAVClient` | `caldav.Calendar` | `caldav.CalendarObjectResource` |
| files | the root `Path` | the directory or file `Path` | the file `Path` |
| JMAP | `calendaring_jmap.JMAPClient` / `AsyncJMAPClient` | the calendar id | the JMAP object dict |
| Gitea | the base URL and an HTTP session | the repository dict from the API | the issue dict from the API |

The base classes type it `object`. Each backend's subclass narrows it
(`CalDAVCollection.native: caldav.Calendar`), so a caller who has checked
`isinstance(cal, CalDAVCollection)` gets a typed native object, and
`--verifytypes` ([D5](PRIOR_ART_AND_DECISIONS.md#d5-typing-strictness)) stays at 100 % without an `Any` in the public API.

Rules: the native object is the backend's own, and reading from it is
always safe. Writing through it bypasses this library's guarantees: no
capability check, no loss check, and the item's `etag` is stale afterwards,
so `reload()` it. The item's `native` is a snapshot from when it was read,
not a live handle.

The other direction also works: `collection.wrap(native_item)` turns a
backend object (a `caldav.Todo`, say) into this library's item, bound to
that collection, without I/O; `wire` stays `None` for a `caldav` object,
whose `wire_data` is its normalised copy rather than the server's bytes
(see "The stored form" in [§3.1](#31-the-base-a-typed-view-over-icalendar)). Together with `.native` that lets code use both libraries side by side,
which is what a migration needs
([§9 Migrating from caldav](#9-migrating-from-caldav)).

---

## 9. Migrating from caldav

What a caldav user keeps, what changes, and what is only reachable through
the escape hatch ([§8 The escape hatch](#8-the-escape-hatch)). Taken from
caldav's public API as of 2026-10-08 (caldav 3.4.0).

| caldav | calendaring | |
|---|---|---|
| `get_davclient()`, `get_calendar(s)()`, config file | `Backend.connect()`, `Workspace.from_config()` | changed; same config file ([config proposal](CONFIGURATION_PROPOSAL.md)) |
| `principal()`, `calendars()`, `make_calendar(supported_calendar_component_set=…)` | `backend.collections()`, `create_collection(components=…)` | same |
| `get_supported_components()` | `collection.components` | same |
| `search(…)`, `CalDAVSearcher` | `collection.search(…)` with a `Searcher` | same; caldav already post-filters client-side |
| `search(…, server_expand=True)` | not a caller choice; the backend decides, the result is the same | escape hatch |
| `get_object_by_uid()`, `event_by_uid()`, `todo_by_uid()` | `get(uid)` | same |
| `event_by_url()` | `get_by_native_id(href)` | same |
| `add_todo(summary=…)`, `save_todo(…)` | `add_task(summary=…)` | same for properties; `ical=` → `add(Task.from_ical(…))` |
| `obj.save()`, `no_overwrite`, `no_create` | `obj.save()` on a bound item, `collection.add(obj)` for a new one | same for updates; create and update split |
| ETag / Schedule-Tag preconditions on save | `etag` precondition, `ConflictError` | same; Schedule-Tag only through the escape hatch |
| `obj.load()`, `obj.delete()` | `obj.reload()`, `obj.delete()` | same (in place on a bound item) |
| `multiget()`, `load_by_multiget()` | used inside the backend | not a public call |
| `icalendar_instance`, `edit_icalendar_component()` (borrowing) | `item.icalendar`, `item.component` | simpler: the item is the iCalendar data, nothing to borrow |
| `vobject_instance` | — | escape hatch (`item.native.vobject_instance`) |
| `data`, `wire_data` | `item.wire` (as read), `item.icalendar.to_ical()` (as it is now) | changed: `wire` is the server's bytes as read, not caldav's normalised copy, and set to `None` by a write |
| `search(expand=True)` | `search(expand=True)` | same |
| `search(expand=True, split_expanded=False)` | — | dropped: an item is never several occurrences without their master ([§3.1](#what-an-item-contains)) |
| `save(only_this_recurrence=True)` (default) | `save(occurrence)` | same ([§3.7 Recurrence](#37-recurrence)) |
| `save(all_recurrences=True)` | `save(occurrence, scope=Scope.ALL)` | same |
| `save(only_this_recurrence=None / False)` | — | escape hatch |
| `expand_rrule(start, end)` on an object | `search(expand=True)`, or `recurring_ical_events` directly | changed |
| "this and future" for events | — | missing in both |
| `complete(handle_rrule=True, rrule_mode=…)` | `task.complete(mode=…)` | same modes; caldav's "interval from completion" guess is dropped |
| `complete()` on a recurring task, default `handle_rrule=False` | `task.complete()` handles the `RRULE` (`mode="safe"`) | **behaviour change**: caldav completes the whole series by default |
| `uncomplete()` | `task.uncomplete()` | same |
| `is_pending()` | `task.component.status` | changed: no helper |
| `get_due()`, `get_duration()`, `set_duration(movable_attr=…)`, `get_dtend()`, `set_end()` | `task.component.end`, `task.component.duration`, `task.set_duration(keep=…)`, `event.component.end` | same, through `icalendar`'s properties |
| `set_due(due, move_dtstart=…, check_dependent=…)` | `task.component.DUE = …` | **not yet**: `move_dtstart` and `check_dependent` |
| `set_relation()`, `get_relatives()` | `item.component.related_to`, `item.relatives()` | same |
| `check_reverse_relations()`, `fix_reverse_relations()` | — | **not yet** |
| `objects_by_sync_token()` | `collection.changes(token)` | same |
| `save_with_invites()`, `accept_invite()`, `decline_invite()`, `change_attendee_status()`, `schedule_inbox()`, `freebusy_request()` | `ATTENDEE`, `ORGANIZER` as data only | escape hatch; scheduling (iTIP) is outside the funded scope |
| `add_attendee()`, `add_organizer()` | `item.component.attendees`, `item.component.organizer` | same, as data |
| `propfind()`, `proppatch()`, `report()`, `mkcol()`, `request()` | — | escape hatch (`backend.native`) |
| compatibility hints, `features:` profile | derived capabilities ([§5.1 The table](#51-the-table)); the profile stays in the config | same source |

The two **not yet** rows are pure logic plus a relatives lookup, and fit
in Phase 1 if plann needs them before roadmap 3.3. The behaviour change in
`complete()` is deliberate: completing a whole series because the caller
forgot a flag is the wrong default. caldav's own docstring says it may
make the flag mandatory.

### 9.1 A migration path for plann

plann is the first program that has to move (roadmap 3.3). In its
`plann/*.py` (`git grep -F` at plann 95fdab5, 2026-10-09) it calls
`get_relatives(` 16 times, `.save(` 13, `get_duration(` 9, `get_due(` 7,
`.complete(` 4 and `set_duration(` 4. A big-bang switch is not needed, because the two libraries
share their data model (`icalendar` objects) and the escape hatch goes
both ways:

1. **Connect through calendaring, keep calling caldav.** plann gets its
   collections from `Workspace.from_config()` (roadmap 1.4, which plann's
   credential work already waits for) and uses `collection.native`, a
   `caldav.Calendar`, everywhere else. Nothing else changes.
2. **Move the reads:** search, `get`, `relatives`. Where plann still holds
   caldav objects, `collection.wrap()` converts them.
3. **Move the writes:** `add`, `save`, `complete`, with the `complete()`
   default checked at every call site.
4. **What is left is the gap list.** Every remaining `.native` call is a
   row in the table above that plann needs, and either gets added here or
   stays as a deliberate CalDAV-only feature.

Progress is measurable: the number of `caldav` names plann uses directly.
For the duration, plann depends on both libraries, which it does anyway,
since calendaring depends on caldav.

---

## 10. Open questions

Settled after peer review on 2026-10-13: each question is decided, deferred, or
marked as not blocking; none blocks
[roadmap 1.2](ROADMAP.md#12-abstract-base-classes-and-the-backend-conformance-suite) from starting.

1. *Decided, 2026-10-10:* **Items and I/O (A1).** Mode-free data classes
   plus bound subclasses per mode, which keeps caldav's `task.save()`
   ([§1.1 Sync and async](#11-sync-and-async)).
2. *Decided, 2026-10-09: `Workspace`.* **The name `Client`.** The author disliked it ("could mean anything")
   and suggested `CalendaringConfig`, `CalendaringCollection` and
   `Calendaring`. What the object is: a set of backends, usually loaded from
   configuration, with fan-out operations over them. The criteria: not an
   HTTP word (client, session, connection), says "several sources", and
   survives a package rename.

   | Name | Verdict |
   |---|---|
   | `Client` | an HTTP word, and says nothing about "several" |
   | `CalendaringConfig` | it does I/O, so it is more than a config |
   | `CalendaringCollection` | clashes with `Collection`, which means a calendar here |
   | `Calendaring` | reads well (`Calendaring.from_config()`), but breaks if the package is renamed |
   | `Session` | requests/SQLAlchemy usage, but clashes with the HTTP session a backend owns |
   | **`Workspace`** | "everything you have configured"; no clash; survives a rename |

   **Proposal: `Workspace`**, accepted by the author and applied
   throughout.
3. *Decided, 2026-10-10:* **Where recurring-task completion lives.** caldav's `complete(handle_rrule=…)`
   logic, and the occurrence merge behind `save(only_this_recurrence=…)`,
   are not CalDAV protocol logic, and by [D1](PRIOR_ART_AND_DECISIONS.md#d1-packaging-principle)
   do not belong in `caldav`. The files backend needs them too. The author
   does not want another package for a few hundred lines, and suggested
   `recurring_ical_events`, which already documents editing one occurrence;
   its maintainer asked for an issue with a proposed API. Where the code
   lives is decided (below); the API itself is still open, in
   [recurring-ical-events issue 292](https://github.com/niccokunzmann/python-recurring-ical-events/issues/292). Until it is settled, roadmap 2.1
   calls caldav's code, and roadmap 2.2 waits for the outcome rather than
   copying it. That means the CalDAV backend keeps caldav's "interval after
   completion" guess for an `RRULE` without `BY*` parts until then: a
   known deviation from [§2.3 Collection](#23-collection), listed in the
   capability matrix.
   *Agreed with recurring-ical-events' maintainer in the
   [PR 5 review](https://github.com/pycalendar/calendaring/pull/5#discussion_r4237580140),
   2026-10-10*, with this split: the merge itself (inserting or replacing
   an override in a recurrence set, completing one occurrence) goes to
   `recurring_ical_events` as `icalendar` manipulation with no I/O. Working
   out that `save(occurrence)` needs a merge, and fetching the stored
   object to merge into, stays here, because it is I/O. Splitting a series
   ("this and future" for events) is not part of that agreement; it is
   assumed to go to [edit-icalendar](https://github.com/pycalendar/edit-icalendar),
   a draft as of 2026-10-12 ([§3.7](#37-recurrence)). Its README also
   covers editing and deleting single occurrences, so the merge above may
   move there too, despite "no extra package" earlier in this question.
4. *Decided, 2026-10-10:* **`verify=True`** on writes is in from the first
   release ([§5.3 What happens when the caller asks for something unsupported](#53-what-happens-when-the-caller-asks-for-something-unsupported)).
5. *Resolved, 2026-10-09:* assignees are `ATTENDEE`s with the tracker's
   profile URL as the calendar address; no `X-` property
   ([§3.4 Task](#34-task)).
6. *Deferred to roadmap 2.5, 2026-10-12:* answered once a Gitea test
   server is up. **Gitea's `content_version`** covers the issue body; whether it also
   changes on label, state or due-date edits is checked in roadmap 2.5,
   against a Gitea run as a CI service container (the official image with
   SQLite needs a few hundred MB of RAM and no persistent host). This is a
   task for 2.5, not a question for the author. A permanent instance is only
   needed for dogfooding, and is optional. *Author, 2026-10-10:* the
   integration tests run against a CI service container, as caldav's do.
7. *Decided, 2026-10-10:* **Feature granularity.** Below 1.0 the list in
   [§5.1 The table](#51-the-table) is flexible: it starts with what the
   funded backends need and grows when needed. Whether it is closed per
   release after 1.0 is a question for then.
8. *Deferred to [roadmap 1.4](ROADMAP.md#14-configuration-and-credentials), 2026-10-13:*
   **The configuration file is shared by caldav, calendaring-jmap and this
   library.** Where should its parser live? A proposal for the team is in
   [CONFIGURATION_PROPOSAL.md](CONFIGURATION_PROPOSAL.md).
9. *Decided, 2026-10-12:* **One item, several backends** (the author's suggestion): an
   item that references a list of backends, so that `save()` or
   `complete()` pushes the change to all of them. It is a good feature, but
   it is a sync engine, not a reference:
   - The same task has a different `uid`, `native_id` and `etag` on each
     backend (Gitea synthesises the `uid`, [§3.2 Identity](#32-identity)),
     so the item needs one identity record per backend.
   - Capabilities differ, so one save can be lossless on CalDAV and lossy on
     Gitea, which needs a loss decision per target.
   - A save can succeed on one backend and fail on another. That is a
     `MultiResult`, not a return value.
   - When the backends have diverged, which copy wins?

   *Decided by the author, 2026-10-12:* no mirroring in 1.x. The conflict half
   belongs in [calendaring-sync](https://github.com/pycalendar/calendaring-sync),
   which is being designed now (Q11).
   *Decided by the author:* bound items hold their collections as a tuple
   (`item.collections`) from the start, with only one supported for now:
   `bind()` and `wrap()` produce exactly one, and building a bound item
   with more raises `UnsupportedError`. The attribute stays when mirroring
   comes, but `etag`, `native_id`, `collection_id` and (on Gitea) `uid` are
   singular today and per backend then (first bullet above), so mirroring
   will change those parts of the API.
10. *Decided, 2026-10-13:* **Item class names** ([§1.1 Sync and async](#11-sync-and-async)):
    `Task` (data) / `SyncTask` / `AsyncTask` as proposed, or `TaskData` /
    `Task` / `AsyncTask` to match the I/O classes' httpx pattern? The
    proposal keeps the short name for the class most code touches. The
    base class follows suit: `Item` / `SyncItem` / `AsyncItem`, or
    `ItemData` / `Item` / `AsyncItem`.
    *Decided by the author after peer review:* `Item` / `SyncItem` /
    `AsyncItem`, as proposed; the short name stays with the data class.
11. *Not blocking, 2026-10-13:* **Cooperation with calendaring-sync.**
    Assumed to be easy: the interfaces below are kept compatible, and
    nothing in 1.x waits for it. Still open: which of the two depends on
    the other, a calendaring adapter in calendaring-sync or calendaring
    depending on calendaring-sync (for a `Mirror`, which Q9 leaves out of
    1.x), and with it the licence question at the end.

    calendaring-sync (Sashank, same 2026-11-01 deadline) keeps sync state
    and detects conflicts; protocol adapters feed it. Its design
    ([PR 19](https://github.com/pycalendar/calendaring-sync/pull/19))
    overlaps with [§7 Change detection](#7-change-detection) and with
    roadmap 2.1. Neither project can wait for the other, so the proposal is
    to share findings and keep the interfaces compatible:
    - **caldav fixes both need.** The sync adapter worked around caldav
      dropping a truncated `sync-collection` page (RFC 6578 §3.6), losing a
      403's body (so `valid-sync-token` cannot be told from a permission
      error), `save()` bumping `SEQUENCE` unasked, and `delete()` sending no
      precondition. Our CalDAV backend hits the same four. Filed as caldav
      issues [737](https://github.com/python-caldav/caldav/issues/737),
      [738](https://github.com/python-caldav/caldav/issues/738),
      [739](https://github.com/python-caldav/caldav/issues/739) and
      [740](https://github.com/python-caldav/caldav/issues/740), fixed on
      caldav master for 3.5.0; calendaring-sync has dropped its workarounds.
      Still open in caldav, and needed here too: the precondition is read
      only from 401/403 errors, so a 412 for an expired token arrives as a
      plain `ReportError`, and a removed member in a sync result is an
      object with no etag rather than marked as removed. Server side:
      [xandikos#743](https://github.com/jelmer/xandikos/issues/743) (a 500
      for a malformed token) and
      [xandikos#730](https://github.com/jelmer/xandikos/issues/730) (the 412).
    - **One "calendaring" adapter later.** Our `Collection.changes()`,
      etags and conditional writes cover CalDAV, JMAP, files, feeds and
      Gitea. An adapter in calendaring-sync built on them would give it
      every calendaring backend at once; its adapter base class is public,
      and its docs now say adapters may live outside the package. JMAP
      would probably stay a native adapter there: it keeps JSCalendar
      rather than iCalendar, and its scope is one account and data type, not
      one calendar. The JSCalendar original stays reachable here as the
      JMAP item's `.native` ([§8](#8-the-escape-hatch)).
    - **Adopted from their findings:** removal by native id, `PROPFIND`
      plus `DAV:sync-token` for a full CalDAV listing, a complete change set
      with no flags, the untrusted etag after a write, `SyncTokenError`,
      `AlreadyExistsError.taken`, `add(native_id=...)` and no retry of a
      write ([§2.3](#23-collection), [§6](#6-errors),
      [§7](#7-change-detection)).
    - **Proposed for a cheap calendaring adapter, open to review:** the
      stored bytes of an item that was read (`Item.wire`) and writes of
      given bytes (`wire=`), since their core stores what the server sent
      and pushes what the application recorded
      ([§3.1](#the-stored-form)); and `changes(known=...)`, a full listing
      that fetches only what changed, as theirs does after a token reset
      ([§7](#7-change-detection)).
    - **Licence.** calendaring-sync is AGPL. An adapter living there may
      depend on calendaring; calendaring depending on calendaring-sync
      (for a `Mirror`) would have to be an optional extra, like JMAP
      ([D3](PRIOR_ART_AND_DECISIONS.md#d3-licence)). Sashank has offered to
      move calendaring-sync to this project's licence and unasync layout;
      undecided.

    Discussion: [the author's first comment](https://github.com/pycalendar/calendaring-sync/pull/19#issuecomment-6096220788),
    [Sashank's answers](https://github.com/pycalendar/calendaring-sync/pull/19#issuecomment-6112378889),
    [what is in the way of a calendaring adapter](https://github.com/pycalendar/calendaring-sync/pull/19#issuecomment-6114023463);
    server evidence on calendaring-sync's
    [`evidence` branch](https://github.com/pycalendar/calendaring-sync/tree/evidence).
12. *Deferred, 2026-10-13:* **Occurrence items: `delete()` and the shared etag**
    ([What an item contains](#what-an-item-contains)). Occurrences of one
    stored series share its `native_id` and `etag`, so the rules for a whole
    object do not carry over. The proposals below are drafts. The author
    recalls reasons for preferring `STATUS:CANCELLED` over `EXDATE`, to be
    written down when this is settled.
    [#398](https://github.com/python-caldav/caldav/issues/398) prefers
    `CANCELLED` to raising an error from `delete()`, since a traceback will
    reach some end user sooner or later. To be settled before
    [roadmap 2.1](ROADMAP.md#21-caldav-backend) implements `delete()` on an
    occurrence.
    - **`delete(occurrence)`** must not delete the stored series, which
      caldav may do today: caldav
      [#398](https://github.com/python-caldav/caldav/issues/398) suspects
      it, and nobody has tested it.
      Two options. **`EXDATE`:** remove that occurrence's override, if
      there is one, and add an `EXDATE` to the master. When there is no
      master, remove the override, and delete the stored object only when
      it was the last one. **`STATUS:CANCELLED`**, which #398 suggests and
      the author leans towards: set it on the occurrence's override,
      creating the override if needed. The occurrence stays visible as
      cancelled, so this is an edit rather than a deletion.
    - **Conflicts between siblings.** Saving one occurrence changes the
      stored object's etag under its siblings. If a sibling's save then
      required the etag it was read with, it would raise `ConflictError`
      for an edit nobody made to it. **Proposal:** `save(occurrence)`
      already fetches the stored object to merge into, so it raises only if
      *that occurrence* changed since it was read, and writes with the
      fresh etag. For an overridden occurrence the check is the override's
      `SEQUENCE` or `LAST-MODIFIED`. A generated occurrence has no override,
      and a concurrent edit of the master (its `RRULE`, its `DTSTART`)
      changes it too, so there the check is the master's.
13. *Decided, 2026-10-12:* **Change tracking and write strategies** (the reviewer's "borrowing",
    [PR 5 review](https://github.com/pycalendar/calendaring/pull/5#discussion_r4237509309)).

    **Writes are explicit.** The funded API has explicit `save()` and
    `reload()`, and `save()` is the primitive any later layer is built on
    ([PR 5 video meeting](https://github.com/pycalendar/calendaring/pull/5#issuecomment-6122366863)).
    Implicit synchronization, where an edit marks the item dirty and a
    strategy writes at once, gathers and flushes on request, or syncs in
    the background, is wanted but not a priority, and is
    [beyond the funded scope](ROADMAP.md#beyond-the-funded-scope). The
    [p5 prototype](SYNC_ASYNC_ARCHITECTURE.md#12-revisited-the-adapter-pattern-2026-10-10)
    shows gather-and-flush working in both modes without duplicated I/O.

    **Writes are tracked through `component`.** The item has no properties
    of its own ([§3.1](#31-the-base-a-typed-view-over-icalendar)), so a
    write through `item.component` has to be seen there. `component` is
    this library's subclass of `icalendar.Todo` / `Event` / `Journal`,
    which overrides `__setitem__`, `__delitem__`, `pop` and `update` to
    mark the item dirty. The typed path, `task.component.summary = x`, is
    then also the tracked path, with no property list repeated here and no
    loss of static types. Against `icalendar` 7.3.1.dev188, every
    typed-property write tried (`summary`, `status`, `DUE`, `DURATION`,
    `DTSTART`, `categories`, `add()`, `del`) goes through those methods.

    - **Needed from `icalendar`:** parsing into the subclass, proposed in
      [icalendar issue 1914](https://github.com/collective/icalendar/issues/1914)
      as a `ComponentFactory` and `Component.convert_to(factory)`, which
      `Task.from_icalendar()` would use on a copy of the caller's
      `Calendar`; and a tested, documented promise that all writes go
      through those methods.
    - **Known gaps:** an in-place change
      (`task.component.categories.append(...)`) calls no setter, so
      `save()` still compares a `to_ical()` snapshot as the backstop; and a
      typo (`component.sumary = x`) is accepted silently.
    - **Rejected:** delegating unknown item attributes to `component`
      (`task.summary` would be `Any` to type checkers), with or without
      renaming it `_component`, and a generated typing stub (code
      generation). The comparison is in git history, as
      `docs/design/WIP_ITEM_DELEGATION.md`.
    - **Transactions, deferred with implicit synchronization.** The
      reviewer asked for edits spanning several calls, or several items, to
      behave
      [like a transaction](https://github.com/pycalendar/calendaring/pull/5#issuecomment-6113054014).
      Gather-and-flush would hold writes until the flush, but no backend
      here has a multi-object transaction (CalDAV writes one resource per
      request, and JMAP `/set` applies and reports each object on its own,
      RFC 8620 §5.3). A flush that fails halfway would have to report which
      writes were stored, as fan-out's `MultiResult` does
      ([§2.1](#21-workspace)), rather than promise atomicity.
14. *Deferred to edit-icalendar, 2026-10-13:* **Writing through `component` on a recurrence set with overrides.**
    The trap is documented in
    [What an item contains](#what-an-item-contains), and the fix is left to
    [edit-icalendar](https://github.com/pycalendar/edit-icalendar). Q3
    sends splitting a series and editing single occurrences there; this
    adds whole-series edits on a set with overrides. For reference when that is
    designed: [`ical`](PRIOR_ART_AND_DECISIONS.md#13-ical-allen-porter)
    avoids the trap by not editing in place: `store.edit(uid, patch,
    recurrence_id, recurrence_range)` takes a partial item holding only the
    changed fields. Without a `recurrence_id`, the patch goes to every
    component with that `UID`, the master and all overrides. With one, it
    forks an override (`Range.NONE`) or ends the series with `UNTIL` and
    starts a new one (`THIS_AND_FUTURE`). Another option for then, or a
    stopgap before it, is for `save()` to warn when the master changed and
    the stored object has overrides.
    The tracked `component` (Q13) lets `save()` see *which* properties
    changed, which a patch-style merge needs.

---

## 11. Peer review

2026-10-10 to 2026-10-13, considered complete on 2026-10-13: an
`icalendar` and `recurring-ical-events` maintainer (@niccokunzmann)
reviewed in [PR 5](https://github.com/pycalendar/calendaring/pull/5) and in
video calls. What it changed:
the item base class is `Item` and carries no iCalendar properties of
its own ([§3.1](#31-the-base-a-typed-view-over-icalendar)); what an item may
hold is spelled out ([What an item contains](#what-an-item-contains)); the
occurrence merge goes to `recurring_ical_events` (Q3); and change tracking is
Q13, which has decided how writes are tracked (a tracked subclass of the
`icalendar` components, pending icalendar issue 1914) and defers his
question about transactions along with implicit synchronization. The video meeting of
2026-10-12 kept `save()` and `reload()` explicit and moved implicit
synchronization beyond the funded scope. His proposal of per-mode adapters (`item.sync.save()`) was
prototyped and not adopted
([sync/async §12](SYNC_ASYNC_ARCHITECTURE.md#12-revisited-the-adapter-pattern-2026-10-10)).

The table lists the reviewers considered when the review was planned.
The review was done by @niccokunzmann and Sashank (below); the others were
not approached for roadmap 1.1.

| Reviewer (role) | Why | What to ask |
|---|---|---|
| a maintainer of `icalendar` | [§3 Items](#3-items) builds on its typed properties; the [roadmap 4.5](ROADMAP.md#45-documentation-review-and-improvements) documentation contributor comes from there | [§3 Items](#3-items), [§3.6 Properties with no standard home](#36-properties-with-no-standard-home) |
| the author of `ical` and Home Assistant's calendar integrations (@allenporter) | the nearest existing multi-backend model, and the recurrence reference | [§1.1 Sync and async](#11-sync-and-async), [§5 Capabilities](#5-capabilities), [§3.7 Recurrence](#37-recurrence) |
| a `caldav` user with a large codebase on it | A1: two item classes per mode and two ways to save, against caldav's one | [§1.1 Sync and async](#11-sync-and-async), [§2.3 Collection](#23-collection) |
| `plann` (the author, as its maintainer) | the committed downstream consumer; [roadmap 1.6](ROADMAP.md#16-time-tracking-model-and-api) depends on [§3.4 Task](#34-task) | [§3.4 Task](#34-task), [§4 Search](#4-search) |
| a vdirsyncer/pimsync maintainer | [§7 Change detection](#7-change-detection) adopts their contract | [§7 Change detection](#7-change-detection) |

The author has reviewed it. Q11's licence offer and dependency direction
are still open, but do not block 1.x.

Sashank (calendaring-sync) reviewed [§7 Change detection](#7-change-detection)
from a sync tool's side on 2026-10-11, in PR 5 and in
[calendaring-sync PR 19](https://github.com/pycalendar/calendaring-sync/pull/19).
What it changed: the JMAP etag, the full CalDAV listing, complete change
sets, `SyncTokenError`, the create clashes, no retried writes and the etag
after a write ([§10](#10-open-questions), Q11).

---

*Drafted with AI assistance (Claude Opus 5.5 via Claude Code) from the [roadmap 0.1](ROADMAP.md#01-task-and-issue-tracker-data-model)–[roadmap 0.3](ROADMAP.md#03-prior-art-standards-and-project-decisions)
documents and the source of `caldav`, `icalendar` and `icalendar-searcher` as
checked out on 2026-10-08 and 09. Reviewed by the author (see Status).*
