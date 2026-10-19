"""Type-checker specimen for sync mode: type-checked by tests/test_enforcement.py, never run.

The markers are those of ``async_mode.py``.  The generated sync API must
accept the plain calls (0.2's case 1) and reject an ``await`` on them
(case 3).
"""

from __future__ import annotations

from calendaring import Backend, Collection, SyncTask, Task, Workspace


def correct(workspace: Workspace, task: Task) -> str:
    backend = Backend.open("memory:")
    coll = backend.collection("default")
    saved = coll.save(task)
    saved.save()
    done = coll.complete(saved)
    found = coll.tasks(summary="x")
    for item in coll.iter_search():
        item.reload()
    everywhere = workspace.search(summary="x")
    assert Workspace.from_config(section="x").backends
    return done.uid + found[0].uid + str(len(everywhere.items))


async def spurious(coll: Collection, task: Task) -> None:
    await coll.save(task)  # expect: misc


async def bound_spurious(task: SyncTask) -> None:
    await task.save()  # expect: misc, func-returns-value


async def more_spurious(workspace: Workspace) -> None:
    await Backend.open("memory:")  # expect: misc
    await workspace.search()  # expect: misc
    await Workspace.from_config()  # expect: misc
