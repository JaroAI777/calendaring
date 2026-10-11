# Working document: delegating item attributes to the component

**Status:** working document, written by Claude Opus 5.5 on 2026-10-11 at the
author's request. **Delete it unless delegation is adopted.** If it is
adopted, fold the outcome into [API_DESIGN §3.1](API_DESIGN.md#31-the-base-a-typed-view-over-icalendar)
and [§10 item 13](API_DESIGN.md#10-open-questions), then delete this file.

**Question:** should `item.summary = "x"` work, by passing unknown attribute
reads and writes on to `item.component`, without the library repeating
icalendar's property list?

**Background:** in the [PR 5 review](https://github.com/pycalendar/calendaring/pull/5#discussion_r4237509309)
the reviewer proposed one getter and setter per property. Each setter would
"borrow" the component, mark the item dirty and notify synchronization
observers. The author rejected
[repeating the property list](https://github.com/pycalendar/calendaring/pull/5#discussion_r4238484919)
(six lines per property, per item type, kept in step with icalendar by hand),
but not the pattern itself. Delegation is a way to keep the pattern without
the list.

---

## 1. The sketch

```python
class Item:
    component: icalendar.Component
    _dirty: bool
    ...                                     # the other fields of API_DESIGN §3.1

    def __getattr__(self, name):            # only called when normal lookup fails
        comp = self.__dict__.get("component")
        if comp is None or not _is_property(comp, name):
            raise AttributeError(name)
        return getattr(comp, name)

    def __setattr__(self, name, value):
        if _is_own(type(self), name):
            return object.__setattr__(self, name, value)
        if not _is_property(self.component, name):
            raise AttributeError(name)
        setattr(self.component, name, value)
        object.__setattr__(self, "_dirty", True)   # and notify strategies (§10 item 13)

def _is_own(cls, name):                     # a field declared anywhere in the class tree
    return any(name in inspect.get_annotations(k) or name in vars(k)
               for k in cls.__mro__ if k is not object)

def _is_property(comp, name):
    return isinstance(getattr(type(comp), name, None), property)
```

The item's own fields are whatever the item's class and its bases declare, so
the fields of subclasses (`Task.native_status`, `Task.planned_start`, the bound
classes' `collections`) are set on the item and not delegated. A fixed list on
`Item` would miss them and make `bind()` and the backends' native passthrough
raise. `inspect.get_annotations` is needed rather than
`vars(k)["__annotations__"]`, because annotations are evaluated lazily on
Python 3.14. A real implementation would cache `_is_own` per class and name.

The list of what can be delegated is whatever the component's own class
defines as a `property`, read at run time. Nothing is copied.

I checked this against icalendar 7.3.1.dev188, using a scratch copy of the
sketch:

- `icalendar.Todo` has 43 properties, among them `summary`, `DUE`, `end`,
  `categories`, `attendees`, `RECURRENCE_ID`, `LAST_MODIFIED`. They are not
  hard-coded anywhere here.
- `item.summary = "x"` writes `SUMMARY:x` into the component and sets the
  dirty flag; `native_status` and `collections`, declared on subclasses, are
  set on the item and leave the flag alone (Python 3.14.7).
- `item.sumary = "x"` (a typo) raises `AttributeError`.
- On an `Event`, `item.DUE = …` raises: the list depends on the component type.
- `copy.copy` and `copy.deepcopy` work, because of the
  `self.__dict__.get("component")` guard. Without the guard, `copy` calls
  `__getattr__` before `component` exists and recurses.
- mypy reports `item.summary` as `Any`.

## 2. What it buys

- **No duplicated property list.** If icalendar adds a property, the item
  gets it too. Nothing can drift, because there is only one implementation.
  The objection in A2 was to a second typed API, and delegation is not one.
- **A place to track writes.** Every write through the item goes through
  `Item.__setattr__`. That is where the reviewer's `_borrowed()` / dirty /
  notify pattern goes, without waiting for icalendar to add change
  notifications. It also feeds the write strategies of §10 item 13
  (immediate, gather-and-flush, background).
- **Typos fail loudly.** Without delegation, `item.sumary = "x"` and even
  `item.summary = "x"` quietly create a plain Python attribute on the item,
  and the value is lost on save. Writing through the component directly has
  the same problem: `item.component.sumary = "x"` is accepted and never
  serialised (checked). Delegation closes this for the item.
- **Familiar to caldav users.** `task.summary` reads like the caldav and
  plann code it replaces. It is shorter than `task.component.summary`.
- **Mode-free.** It lives on the plain `Item`, so A1 and the unasync-generated
  sync copy are unaffected.

## 3. Drawbacks

- **Static typing is lost.** `task.summary` is `Any` for mypy and pyright,
  and IDE completion doesn't know it. Getting the types back needs a
  `TYPE_CHECKING`-only declaration per item type, which is the property list
  again (names and types only, no code). It could be generated or
  checked against icalendar by a test, but it is still a list. The typed
  path, `task.component.summary`, stays fully typed. **This is the main cost.**
- **In-place changes are not seen.** `task.categories.append("b")` changes the
  stored data without calling any setter: icalendar returns the live list
  (checked, and the same holds for `attendees`). Writes through
  `task.component` bypass the hook as well. Delegation therefore cannot be the
  only source of the dirty flag. A snapshot comparison is still needed as a
  backstop: `component.to_ical()` when the item is read or saved, compared at
  `save()` / when a strategy flushes. Returning copies from the getter would
  make `append` lose data silently, which is worse.
- **Two ways to write the same thing.** `task.summary` and
  `task.component.summary` would both work, but only the first is tracked
  straight away. The documentation has to choose one as the normal way and
  call the other the escape hatch. Renaming it `item._component` would make
  the choice for the user, but it costs more than the name suggests; see
  [§4, alternative D](#4-alternatives).
- **Name collisions.** The item's own fields win. Today the only overlap is
  `uid` (read-only on the item, writable on the component), and there the
  item's behaviour is the right one. Any field added to `Item` or a subclass
  later silently hides a component property with the same name. That can be
  guarded with a test that the item's own fields and icalendar's properties
  overlap only in an approved list.
- **Mixed-case names.** icalendar uses both `DUE` and `end`, `LAST_MODIFIED`
  and `last_modified`. Delegation passes that through as it is, so
  `task.due` raises while `task.DUE` works. The vocabulary is icalendar's,
  quirks included.
- **Harder to discover.** `dir(task)` and `help(Task)` don't show the
  delegated names unless `__dir__` is overridden too (which is easy).
- **It is magic.** `__getattr__` and `__setattr__` are run on every attribute
  write to the item and surprise contributors. The sketch is about 20 lines,
  but those lines have to stay correct.

## 4. Alternatives

- **A. No delegation (the current text of A2).** Users write through
  `item.component`. Typing is complete, but there is no tracking hook and the
  typo trap stays open. Tracking waits for icalendar.
- **B. Closed item, without delegation.** `__slots__`, or a `__setattr__`
  that raises on anything that is not the item's own field. This closes the
  typo trap at almost no cost, but gives no tracking hook.
- **C. Delegation (this document).**
- **D. Delegation with a private `_component`.** One spelling, and every
  setter call is tracked. But:
  - The only fully typed path goes away, so the typing drawback of §3 applies
    to everything the user writes, not just to the short spelling.
  - Delegation only reaches typed properties. Properties icalendar has no
    accessor for (`PERCENT-COMPLETE`, `COMPLETED`, `ESTIMATED-DURATION`, all
    read today with `component.get(...)`) and subcomponents would need another
    public path, for instance `item["PERCENT-COMPLETE"]` delegated to the
    component's `__getitem__`/`__setitem__`, which can also be tracked.
  - `item.icalendar` (the whole `VCALENDAR`) is public too, and contains the
    component. Hiding one while the other stays public only hides it halfway.
    It would have to go read-only or private as well, which departs from
    [D6](PRIOR_ART_AND_DECISIONS.md#d6-canonical-in-memory-model) and from the
    escape hatch of [API_DESIGN §8](API_DESIGN.md#8-the-escape-hatch).
  - In-place changes (`item.categories.append(...)`) still bypass the hook, so
    the snapshot comparison is still needed.

  If chosen, the leading underscore is the Python convention, and nothing
  enforces it: `item._component.summary = x` still works.
  Keeping `component` public and documenting it as the untracked escape hatch,
  as `native` is, gets the same clarity without these costs.
- **E. Delegation plus a generated typing stub.** C, with types restored by a
  `.pyi` / Protocol generated from icalendar. This brings back code
  generation, which the author already dislikes in unasync.

Whatever is chosen, a `save()` that compares snapshots is the only thing that
catches every change, including those A–E miss. So it belongs in the design
in all five cases.

## 5. Conclusion

Delegation does what was asked: the reviewer's tracking pattern works and no
property list is repeated. The evidence above shows two limits. It does not
catch in-place changes, so the snapshot backstop stays. And it gives up static
types on the short spelling. If untyped `task.summary` next to typed
`task.component.summary` is acceptable, C plus the snapshot backstop is better
than A: it closes the typo trap and gives §10 item 13 a hook that doesn't
wait for icalendar. If typing on the main path is not up for negotiation, B
plus the snapshot backstop gets most of the safety for almost no code, and
tracking goes back to waiting for icalendar.
