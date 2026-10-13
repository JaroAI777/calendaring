"""What only p5 has: strategies, borrowing, and the mode as a type parameter.

The shared behaviour (get, save, paginated search, composition, filesystem,
errors) is in ``test_conformance.py``, which drives p5 like the others.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from common import AsyncTransport, Store, SyncTransport
from p5_adapter import adapter as p5
from p5_adapter._async import ops as p5_async
from p5_adapter._sync import ops as p5_sync
from test_conformance import _require

HERE = Path(__file__).parent


@pytest.fixture
def store() -> Store:
    return Store().seed(5)


def puts(store: Store) -> int:
    return sum(r.method == "PUT" for r in store.request_log)


async def test_bare_call_in_async_mode_raises_before_io(store: Store) -> None:
    """The caldav slip through the bare method: an exception, not a lost write.

    ``item.save()`` is a plain ``def``, so the check runs at the call site and
    there is no coroutine to drop.
    """
    coll = p5_async.AsyncCollection(AsyncTransport(store))
    task = await coll.get_task("task-1")
    task.summary = "changed"
    with pytest.raises(p5.ModeError, match="await item.aio.save"):
        task.save()
    assert puts(store) == 0


def test_bare_call_in_sync_mode_is_caldavs_task_save(store: Store) -> None:
    coll = p5_sync.SyncCollection(SyncTransport(store))
    task = coll.get_task("task-1")
    task.summary = "changed"
    task.save()
    task.complete()
    assert store.tasks["task-1"] == {"uid": "task-1", "summary": "changed", "status": "COMPLETED"}


async def test_wrong_facade_raises(store: Store) -> None:
    atask = await p5_async.AsyncCollection(AsyncTransport(store)).get_task("task-1")
    stask = p5_sync.SyncCollection(SyncTransport(store)).get_task("task-1")
    with pytest.raises(p5.ModeError):
        atask.sync  # type: ignore[misc]
    with pytest.raises(p5.ModeError):
        stask.aio  # type: ignore[misc]


async def test_composition_slip_through_the_bare_name_raises_in_async(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inside an async op, calling the item's bare ``save()`` instead of
    ``await self.save()`` raises rather than dropping the write.  The other
    shape, ``self.save()`` on the facade without ``await``, is p4's slip
    unchanged, and is what the type checker is for (CASE 7 in the probe).
    """

    async def uncomplete(self: Any) -> Any:
        self._item._data.status = "NEEDS-ACTION"
        self._item.save()  # the slip
        return self._item

    monkeypatch.setattr(p5_async.AsyncFacade, "uncomplete", uncomplete)
    task = await p5_async.AsyncCollection(AsyncTransport(store)).get_task("task-1")
    with pytest.raises(p5.ModeError):
        await task.aio.uncomplete()


def test_borrowing_marks_dirty_and_save_clears_it(store: Store) -> None:
    task = p5_sync.SyncCollection(SyncTransport(store)).get_task("task-1")
    assert not task.dirty
    task.summary = "changed"
    assert task.dirty
    task.sync.save()
    assert not task.dirty


def test_nested_borrow_notifies_once(store: Store) -> None:
    coll = p5_sync.SyncCollection(SyncTransport(store))
    task = coll.get_task("task-1")
    task._strategy = p5.Autosync(coll)
    with task._borrowed() as data:
        data.summary = "a"
        task.status = "COMPLETED"  # a borrow inside a borrow
    assert puts(store) == 1


def test_a_borrow_that_raises_writes_nothing(store: Store) -> None:
    """A failed modification must not be written half-done under Autosync."""
    coll = p5_sync.SyncCollection(SyncTransport(store))
    task = coll.get_task("task-1")
    task._strategy = p5.Autosync(coll)
    with pytest.raises(ValueError):
        with task._borrowed() as data:
            data.summary = "half"
            raise ValueError("the rest of the edit failed")
    assert puts(store) == 0


def test_autosync_writes_once_per_modification(store: Store) -> None:
    """Mode 1 of the review.  Three setters, three round trips."""
    coll = p5_sync.SyncCollection(SyncTransport(store))
    task = coll.get_task("task-1")
    task._strategy = p5.Autosync(coll)
    task.summary = "a"
    task.summary = "b"
    task.status = "COMPLETED"
    assert puts(store) == 3
    assert store.tasks["task-1"]["status"] == "COMPLETED"


@pytest.mark.parametrize("mode", ["sync", "async"])
async def test_gather_coalesces_and_flushes(store: Store, mode: str) -> None:
    """Mode 2 of the review.  Four modifications of two items, two round trips."""
    if mode == "async":
        coll: Any = p5_async.AsyncCollection(AsyncTransport(store))
        gather: Any = p5_async.AsyncGather(coll)
        a, b = await coll.get_task("task-1"), await coll.get_task("task-2")
    else:
        coll = p5_sync.SyncCollection(SyncTransport(store))
        gather = p5_sync.SyncGather(coll)
        a, b = coll.get_task("task-1"), coll.get_task("task-2")
    a, b = gather.track(a), gather.track(b)
    a.summary = "a1"
    a.summary = "a2"
    a.save()  # bare call: allowed, only queues, in either mode
    b.status = "COMPLETED"
    assert puts(store) == 0
    if mode == "async":
        await gather.flush()
    else:
        gather.flush()
    assert puts(store) == 2
    assert store.tasks["task-1"]["summary"] == "a2"
    assert store.tasks["task-2"]["status"] == "COMPLETED"


def test_a_write_that_bypasses_the_setters_is_not_tracked(store: Store) -> None:
    """The leak.  A2/D6 make ``item.icalendar`` public and the storage, so a
    caller can always reach past the setters (here ``_data`` stands in for it,
    and ``categories.append()`` on a list attribute is the same shape).  The
    item is not marked dirty, and a gathered flush writes nothing.  Fixing it
    needs change notification in the data object itself (icalendar).
    """
    coll = p5_sync.SyncCollection(SyncTransport(store))
    gather = p5_sync.SyncGather(coll)
    task = gather.track(coll.get_task("task-1"))
    task._data.summary = "edited through the escape hatch"
    assert not task.dirty
    gather.flush()
    assert puts(store) == 0
    assert store.tasks["task-1"]["summary"] == "Task 1"  # the edit is lost


def test_generated_sync_code_is_fresh(tmp_path: Path) -> None:
    _require("unasync")
    from p5_adapter import generate

    committed_root = HERE / "p5_adapter" / "_sync"
    fresh = generate.generate(tmp_path)
    committed = {p.relative_to(committed_root) for p in committed_root.rglob("*.py")}
    assert committed == fresh, "file sets differ: run p5_adapter/generate.py"
    for rel in sorted(fresh):
        assert (committed_root / rel).read_text() == (tmp_path / "_sync" / rel).read_text()


def _cases(path: Path) -> dict[str, tuple[int, bool]]:
    """``# CASE n: WRONG|correct`` markers: name -> (line, should_error)."""
    out = {}
    for i, line in enumerate(path.read_text().splitlines(), start=1):
        if "# CASE " in line:
            tag = line.split("# CASE ")[1]
            out[tag.split(":")[0]] = (i, "WRONG" in tag)
    return out


def test_type_checker_sees_the_mode() -> None:
    """The finding: one class, the mode as a type parameter, and mypy rejects
    the wrong facade and the bare call in async mode.  CASE 8 is the limit:
    a helper annotated ``TaskAdapter[Any]`` turns the check off, while
    ``TaskAdapter[object]`` keeps it.
    """
    _require("mypy")
    probe = HERE / "typing_probe" / "probe_p5_adapter.py"
    out = subprocess.run(
        [sys.executable, "-m", "mypy", "--no-incremental", "--cache-dir=/dev/null", str(probe)],
        capture_output=True, text=True, cwd=HERE,
        env={**__import__("os").environ, "MYPYPATH": str(HERE)},
    )
    flagged = {
        int(ln.split(":")[1])
        for ln in out.stdout.splitlines()
        if ": error:" in ln and ln.split(":")[0].endswith("probe_p5_adapter.py")
    }
    cases = _cases(probe)
    expected_misses = {"8"}
    for name, (line, wrong) in cases.items():
        if name in expected_misses:
            assert line not in flagged, f"CASE {name} is now caught: update the document"
        elif wrong:
            assert line in flagged, f"CASE {name} not caught"
        else:
            assert line not in flagged, f"CASE {name} is a false positive"
