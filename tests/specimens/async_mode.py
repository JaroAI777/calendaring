"""Type-checker specimen for async mode: type-checked by tests/test_enforcement.py, never run.

``# expect: CODE, ...`` marks a line mypy must flag with exactly those codes, and
``# expect-ruff: CODE`` one ruff must flag.  Every other line must pass, so
the correct calls double as a check that the public signatures accept them
(0.2's case 1).  The shapes are the ones SYNC_ASYNC_ARCHITECTURE.md §5
found, the misses included (unmarked, under ``# MISSED``): a change in what
the checkers catch shows up as a failing test rather than as an unnoticed
hole.
"""

from __future__ import annotations

from typing import Any

from calendaring import AsyncBackend, AsyncCollection, AsyncTask, AsyncWorkspace, Task


async def correct(backend: AsyncBackend, workspace: AsyncWorkspace, task: Task) -> str:
    backend = await AsyncBackend.connect("memory:")
    coll = await backend.collection("default")
    saved = await coll.save(task)
    await saved.save()
    done = await coll.complete(saved)
    found = await coll.tasks(summary="x")
    async for item in coll.iter_search():
        await item.reload()
    everywhere = await workspace.search(summary="x")
    configured = await AsyncWorkspace.from_config(section="x")
    assert configured.backends
    return done.uid + found[0].uid + str(len(everywhere.items))


async def bare(coll: AsyncCollection, task: Task) -> None:
    coll.save(task)  # expect: unused-coroutine


async def bound_bare(task: AsyncTask) -> None:
    task.save()  # expect: unused-coroutine


async def result_used(coll: AsyncCollection, task: Task) -> str:
    saved = coll.save(task)
    return saved.uid  # expect: attr-defined, no-any-return


async def more_results_used(backend: AsyncBackend, workspace: AsyncWorkspace) -> None:
    coll = backend.collection("default")
    coll.tasks()  # expect: attr-defined
    for _ in coll.iter_search():  # expect: attr-defined
        pass
    assert workspace.search().items  # expect: attr-defined
    assert AsyncWorkspace.from_config().backends  # expect: attr-defined


class Helper:
    def __init__(self, coll: AsyncCollection) -> None:
        self.coll = coll

    async def untyped(self, task):  # expect: no-untyped-def
        self.coll.save(task)  # expect: unused-coroutine


async def assigned(coll: AsyncCollection, task: Task) -> None:
    saved = coll.save(task)  # expect-ruff: F841


def log(*args: object) -> None: ...


# MISSED by mypy, pyright and ruff alike (0.2 §5)
async def missed(coll: AsyncCollection, task: Task, tasks: list[Task], dirty: bool, receiver: Any) -> None:
    [coll.save(t) for t in tasks]
    _ = coll.save(task)
    coll.save(task) if dirty else None
    dirty and coll.save(task)
    log("saved", coll.save(task))
    if r := coll.save(task):
        log(r)
    receiver.save(task)


async def missed_return(coll: AsyncCollection, task: Task) -> Any:
    return coll.save(task)
