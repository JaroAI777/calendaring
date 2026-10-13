"""One conformance suite, driving all six prototypes in both modes.

This file *is* one of the measurements.  The roadmap asks whether a single test
suite can drive both modes; the answer for every candidate is yes, and this
is the evidence.  What differs between the candidates is not the suite but what
the suite is able to *catch* - see ``test_composed_write_actually_writes`` and
``test_misuse_is_caught_in_both_modes``.

Run:  python -m pytest prototypes/sync_async/ -q
"""

from __future__ import annotations

import inspect
import warnings
from pathlib import Path
from typing import Any

import pytest

import common
import p1_dual_mode
import p2_sansio
import p2_sansio_bug
import p2_typed
import p3_greenlet
from p4_codegen._async import buggy as p4_async_buggy
from p4_codegen._async import tasks as p4_async
from p4_codegen._sync import buggy as p4_sync_buggy
from p4_codegen._sync import tasks as p4_sync
from p5_adapter import adapter as p5
from p5_adapter._async import ops as p5_async
from p5_adapter._sync import ops as p5_sync
from common import AsyncFileTransport, AsyncTransport, FileStore, Store, SyncTransport

PROTOCOLS = ["p1_dual_mode", "p2_sansio", "p2_typed", "p3_greenlet", "p4_codegen", "p5_adapter"]
MODES = ["sync", "async"]


async def maybe_await(value: Any) -> Any:
    """The one concession the suite makes to running in two modes."""
    if inspect.isawaitable(value):
        return await value
    return value


def make_collection(proto: str, mode: str, store: Store, transport: Any = None) -> Any:
    if transport is None:
        transport = AsyncTransport(store) if mode == "async" else SyncTransport(store)
    if proto == "p1_dual_mode":
        return p1_dual_mode.Collection(transport, is_async=(mode == "async"))
    if proto == "p2_sansio":
        return p2_sansio.Collection(transport)
    if proto == "p2_typed":
        return (
            p2_typed.AsyncCollection(transport)
            if mode == "async"
            else p2_typed.SyncCollection(transport)
        )
    if proto == "p4_codegen":
        return (
            p4_async.AsyncCollection(transport)
            if mode == "async"
            else p4_sync.SyncCollection(transport)
        )
    if proto == "p5_adapter":
        return (
            p5_async.AsyncCollection(transport)
            if mode == "async"
            else p5_sync.SyncCollection(transport)
        )
    return p3_greenlet.Collection(transport)


def io(proto: str, mode: str, task: Any) -> Any:
    """Where an item's I/O methods are: on the item, or on p5's facade."""
    if proto != "p5_adapter":
        return task
    return task.aio if mode == "async" else task.sync


@pytest.fixture(params=PROTOCOLS)
def proto(request: Any) -> str:
    return request.param


@pytest.fixture(params=MODES)
def mode(request: Any) -> str:
    return request.param


@pytest.fixture
def store() -> Store:
    return Store().seed(5)


@pytest.mark.asyncio
async def test_get_task(proto: str, mode: str, store: Store) -> None:
    coll = make_collection(proto, mode, store)
    task = await maybe_await(coll.get_task("task-1"))
    assert task.uid == "task-1"
    assert task.summary == "Task 1"


@pytest.mark.asyncio
async def test_get_missing_task_raises(proto: str, mode: str, store: Store) -> None:
    coll = make_collection(proto, mode, store)
    with pytest.raises(KeyError):
        await maybe_await(coll.get_task("nope"))


@pytest.mark.asyncio
async def test_save(proto: str, mode: str, store: Store) -> None:
    coll = make_collection(proto, mode, store)
    task = await maybe_await(coll.get_task("task-1"))
    task.summary = "changed"
    await maybe_await(io(proto, mode, task).save())
    assert store.tasks["task-1"]["summary"] == "changed"


@pytest.mark.asyncio
async def test_search_paginates(proto: str, mode: str, store: Store) -> None:
    """The loop case: 5 tasks at PAGE_SIZE 2 must take 3 requests."""
    coll = make_collection(proto, mode, store)
    tasks = await maybe_await(coll.search())
    assert len(tasks) == 5
    search_requests = [r for r in store.request_log if r.path.startswith("/search")]
    assert len(search_requests) == 3


@pytest.mark.asyncio
async def test_composed_write_actually_writes(proto: str, mode: str, store: Store) -> None:
    """I/O, then logic, then I/O.  This is the caldav bug shape."""
    coll = make_collection(proto, mode, store)
    task = await maybe_await(coll.get_task("task-1"))
    await maybe_await(io(proto, mode, task).complete())
    assert store.tasks["task-1"]["status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_uncomplete_actually_writes(
    proto: str, mode: str, store: Store, request: Any
) -> None:
    """p1 fails this in async mode only.  That asymmetry is the whole finding.

    The xfail is applied as a *strict* marker rather than by calling
    ``pytest.xfail()``, which raises immediately and would leave the assertion
    below unreached.  Strict means that if p1's bug were ever fixed this test
    would XPASS and the suite would fail, instead of quietly staying green.
    """
    if proto == "p1_dual_mode" and mode == "async":
        request.node.add_marker(
            pytest.mark.xfail(
                strict=True,
                reason="dual-mode uncomplete() drops the coroutine: the object "
                "looks correct and the server was never written to",
            )
        )
    coll = make_collection(proto, mode, store)
    task = await maybe_await(coll.get_task("task-1"))
    await maybe_await(io(proto, mode, task).complete())
    result = await maybe_await(io(proto, mode, task).uncomplete())
    assert result.status == "NEEDS-ACTION"
    assert store.tasks["task-1"]["status"] == "NEEDS-ACTION"


@pytest.mark.asyncio
async def test_dual_mode_silently_drops_the_write(store: Store) -> None:
    """The bug itself, asserted positively rather than as an absence.

    This is the measurement the document's section 2 quotes: the object comes
    back looking right, and the server still holds the old value.
    """
    coll = make_collection("p1_dual_mode", "async", store)
    task = await coll.get_task("task-1")
    await task.complete()
    assert store.tasks["task-1"]["status"] == "COMPLETED"

    result = task.uncomplete()  # no await needed: it is not a coroutine
    assert result.status == "NEEDS-ACTION"  # the object looks correct...
    assert store.tasks["task-1"]["status"] == "COMPLETED"  # ...the server is stale


@pytest.mark.asyncio
async def test_backend_error_propagates(proto: str, mode: str, store: Store) -> None:
    store.fail_on.add("/tasks/task-1")
    coll = make_collection(proto, mode, store)
    with pytest.raises(common.BackendError):
        await maybe_await(coll.get_task("task-1"))


@pytest.mark.asyncio
async def test_backend_error_mid_pagination_propagates(
    proto: str, mode: str, store: Store
) -> None:
    """Failing on page 2 is harder than failing on the only request."""
    store.fail_on.add("/search?cursor=2")
    coll = make_collection(proto, mode, store)
    with pytest.raises(common.BackendError):
        await maybe_await(coll.search())


@pytest.mark.asyncio
async def test_filesystem_backend(proto: str, mode: str, tmp_path: Path) -> None:
    """The roadmap's 'not only HTTP' case: a naturally blocking transport."""
    fstore = FileStore(tmp_path)
    for i in range(5):
        (tmp_path / f"task-{i}.json").write_text(
            f'{{"uid": "task-{i}", "summary": "Task {i}", "status": "NEEDS-ACTION"}}'
        )
    transport = AsyncFileTransport(fstore) if mode == "async" else SyncTransport(fstore)
    coll = make_collection(proto, mode, fstore, transport)

    tasks = await maybe_await(coll.search())
    assert len(tasks) == 5
    task = await maybe_await(coll.get_task("task-2"))
    task.summary = "written to disk"
    await maybe_await(io(proto, mode, task).save())
    assert "written to disk" in (tmp_path / "task-2.json").read_text()


@pytest.mark.asyncio
async def test_misuse_is_caught_in_both_modes(mode: str, store: Store) -> None:
    """Sans-I/O: calling a public I/O method from inside another one.

    The point is that this raises in **sync** mode as well, so the mistake is
    caught by the ordinary test suite rather than only by an async-specific
    test that someone has to remember to write.  The broken body lives in
    ``p2_sansio_bug.py`` so that ``check_ast.py`` has a committed specimen too.
    """
    coll = make_collection("p2_sansio", mode, store)
    task = await maybe_await(coll.get_task("task-1"))
    buggy = p2_sansio_bug.BuggyTask(uid=task.uid)
    buggy._transport = coll._transport
    with pytest.raises(p2_sansio.SansIOMisuse):
        await maybe_await(buggy.uncomplete())


@pytest.mark.asyncio
async def test_facade_misuse_is_caught_in_both_modes(
    mode: str, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same guard, reached through a typed facade rather than public().

    Review finding 1: the facades bypass ``public()``, so this is the test that
    would have caught p2b having no guard at all.  The slip
    is planted in the shared core body and reached through the public facade
    method, as a caller would.  What catches it is the ``guard()`` in the
    facade the body wrongly calls (``save``).  In async mode that guard is the
    only thing that can, since the inner coroutine is discarded before any
    driver runs; removing it fails the async case.  In sync mode the inner
    driver would raise as well.
    """
    coll = make_collection("p2_typed", mode, store)
    task = await maybe_await(coll.get_task("task-1"))

    def _io_uncomplete(self: Any) -> Any:
        self.status = "NEEDS-ACTION"
        self.save()  # the caldav slip, inside a shared core body
        return self
        yield

    monkeypatch.setattr(p2_typed._TaskCore, "_io_uncomplete", _io_uncomplete)
    with pytest.raises(p2_sansio.SansIOMisuse):
        await maybe_await(task.uncomplete())


def test_every_io_body_has_both_facades() -> None:
    """Section 10 of the design document promises this check; here it is.

    A core I/O body with only one facade is the failure mode of the two-facade
    design, and it is mechanical to detect.
    """
    for core, sync_cls, async_cls in [
        (p2_typed._TaskCore, p2_typed.SyncTask, p2_typed.AsyncTask),
        (p2_typed._CollectionCore, p2_typed.SyncCollection, p2_typed.AsyncCollection),
    ]:
        bodies = {n.removeprefix("_io_") for n in vars(core) if n.startswith("_io_")}
        for name in bodies:
            assert callable(getattr(sync_cls, name, None)), f"{sync_cls.__name__}.{name}"
            assert callable(getattr(async_cls, name, None)), f"{async_cls.__name__}.{name}"


def test_static_check_passes_the_real_modules_and_catches_the_specimen() -> None:
    """Section 10 calls ``check_ast.py`` a prototype, not a CI gate; the suite runs it.

    Known limits, recorded in section 10 as requirements for item 1.3: it only
    resolves names defined in the same file, flags any same-named attribute,
    and exempts ``g = self.save(); yield from g`` as delegation.
    """
    import check_ast

    here = Path(__file__).parent
    assert check_ast.check(here / "p2_sansio.py") == []
    assert check_ast.check(here / "p2_typed.py") == []
    flagged = {line for line, _ in check_ast.check(here / "p2_sansio_bug.py")}
    src = (here / "p2_sansio_bug.py").read_text().splitlines()
    planted = {n for n, text in enumerate(src, 1) if "# BUG" in text}
    assert len(planted) == 2
    assert flagged == planted


@pytest.mark.asyncio
async def test_dropped_io_body_is_silent_at_runtime(
    mode: str, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other shape, in p2b: ``self._io_save()`` without ``yield from``.

    Planted in p2b's shared core body and reached through the public facade,
    as ``test_facade_misuse_is_caught_in_both_modes`` does for the public-name
    shape.  The guard never runs, because no public name is called; the
    generator is built and dropped, and Python does not warn about a generator
    that was never started.  The write is lost in both modes with no exception
    and no warning, so only a static check can catch this shape.  The test
    pins that hole; ``p2_sansio_bug._io_reopen`` is the same shape for
    ``check_ast.py``.
    """
    store.tasks["task-1"]["status"] = "COMPLETED"
    coll = make_collection("p2_typed", mode, store)
    task = await maybe_await(coll.get_task("task-1"))

    def _io_uncomplete(self: Any) -> Any:
        self.status = "NEEDS-ACTION"
        self._io_save()  # the dropped generator, inside a shared core body
        return self
        yield

    monkeypatch.setattr(p2_typed._TaskCore, "_io_uncomplete", _io_uncomplete)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        result = await maybe_await(task.uncomplete())
    assert result.status == "NEEDS-ACTION"
    assert store.tasks["task-1"]["status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_codegen_bug_is_async_only(store: Store) -> None:
    """p4: the planted missing ``await`` is correct code once unasync'ed.

    The same asymmetry as p1: the sync suite is green, async users lose the
    write.  Only a type checker or ``-W error`` sees it (section 5).
    """
    sync_coll = p4_sync.SyncCollection(SyncTransport(store))
    task = p4_sync_buggy.SyncBuggyTask.from_dict(store.tasks["task-1"])
    task._collection = sync_coll
    task.status = "COMPLETED"
    task.save()
    task.uncomplete()
    assert store.tasks["task-1"]["status"] == "NEEDS-ACTION"  # sync: correct

    async_coll = p4_async.AsyncCollection(AsyncTransport(store))
    atask = p4_async_buggy.AsyncBuggyTask.from_dict(store.tasks["task-1"])
    atask._collection = async_coll
    atask.status = "COMPLETED"
    await atask.save()
    with pytest.warns(RuntimeWarning, match="never awaited"):
        result = await atask.uncomplete()
        del result
        import gc

        gc.collect()
    assert atask.status == "NEEDS-ACTION"  # the object looks right...
    assert store.tasks["task-1"]["status"] == "COMPLETED"  # ...the server is stale


def _require(module: str) -> Any:
    """Import a dev-only tool; skip without it locally, fail without it in CI.

    A plain ``importorskip`` would let a CI job that lacks the tool go green
    without ever running the check (review finding M2).
    """
    import importlib
    import os

    try:
        return importlib.import_module(module)
    except ImportError:
        if os.environ.get("CI"):
            pytest.fail(f"{module} is required when CI is set")
        pytest.skip(f"needs {module}: uv run --with {module}")


def test_generated_sync_code_is_fresh(tmp_path: Path) -> None:
    """The committed ``_sync/`` must be exactly what unasync makes of ``_async/``.

    This is what replaces a build step.  It compares the *set* of files as well
    as their contents, recursively, so an edit to the generated copy, a
    forgotten regeneration, a new subpackage and an orphan left behind by a
    deleted source all fail here.
    """
    _require("unasync")
    from p4_codegen import generate

    committed_root = Path(generate.__file__).parent / "_sync"
    fresh = generate.generate(tmp_path)
    committed = {p.relative_to(committed_root) for p in committed_root.rglob("*.py")}
    assert committed == fresh, "file sets differ: run p4_codegen/generate.py"
    for rel in sorted(fresh):
        assert (committed_root / rel).read_text() == (tmp_path / "_sync" / rel).read_text(), (
            f"_sync/{rel} is stale"
        )


def _slip_lines(path: Path) -> dict[str, int]:
    """Line numbers of the ``# SLIP <shape>`` markers in a specimen."""
    out: dict[str, int] = {}
    for i, line in enumerate(path.read_text().splitlines(), start=1):
        if "# SLIP " in line:
            out[line.split("# SLIP ")[1].split(":")[0]] = i
    return out


def test_type_checker_flags_the_codegen_slip() -> None:
    """p4's enforcement *is* the type checker, so the suite must assert it fires.

    Pins all three shapes in ``_async/buggy.py``: the bare call is caught by
    default mypy; the unannotated helper only with ``--check-untyped-defs``;
    the comprehension by neither.  The last two are limits, asserted so that
    a change in mypy's behaviour shows up here rather than going unnoticed.
    """
    import subprocess
    import sys

    _require("mypy")
    here = Path(__file__).parent
    specimen = here / "p4_codegen" / "_async" / "buggy.py"
    lines = _slip_lines(specimen)

    def flagged(*flags: str) -> set[int]:
        out = subprocess.run(
            [sys.executable, "-m", "mypy", "--no-incremental", "--cache-dir=/dev/null", *flags, str(specimen)],
            capture_output=True, text=True, cwd=here, env={**__import__("os").environ, "MYPYPATH": str(here)},
        )
        return {
            int(ln.split(":")[1]) for ln in out.stdout.splitlines() if "[unused-coroutine]" in ln
        }

    assert flagged() == {lines["bare"]}
    assert flagged("--check-untyped-defs") == {lines["bare"], lines["untyped"]}


@pytest.mark.parametrize(
    ("async_src", "naive_sync"),
    [
        # a blocking backend pushed to a thread: the sync copy returns a coroutine
        ("data = await asyncio.to_thread(path.read_text)", "data = asyncio.to_thread(path.read_text)"),
        # a third-party async class: renamed to a class that does not exist
        ("client = httpx.AsyncClient()", "client = httpx.SyncClient()"),
        # concurrency: gather() survives, and is meaningless without a loop
        ("a, b = await asyncio.gather(f(), g())", "a, b = asyncio.gather(f(), g())"),
        # backoff: the sync copy builds a coroutine and drops it
        ("await asyncio.sleep(backoff)", "asyncio.sleep(backoff)"),
        # not in unasync's table: httpx's sync client calls it close()
        ("await self._http.aclose()", "self._http.aclose()"),
        ("item = await anext(it)", "item = anext(it)"),
        # unchanged annotations
        ("def f() -> Awaitable[int]: ...", "def f() -> Awaitable[int]: ..."),
        ('def f() -> "list[AsyncTask]": ...', 'def f() -> "list[AsyncTask]": ...'),
        # the token after `await` is skipped blindly: a syntax error
        ("await(self.save())", "self.save())"),
    ],
)
def test_unasync_is_a_token_rewrite(async_src: str, naive_sync: str) -> None:
    """What unasync cannot translate, and so what p4 must keep out of ``_async/``.

    Each of these is a correct async line whose unasync'ed form is wrong.  In
    p4 they are absent only because the transports are supplied from outside;
    a real library needs hand-written per-mode shims for them (httpcore keeps
    a ``_synchronization.py`` and a backend pair for exactly this).  mypy over
    ``_sync/`` flags some of them (the dropped ``sleep``), not all.
    """
    unasync = _require("unasync")
    import tokenize_rt

    rule = unasync.Rule("/_async/", "/_sync/")
    out = tokenize_rt.tokens_to_src(rule._unasync_tokens(tokenize_rt.src_to_tokens(async_src)))
    assert out == naive_sync
