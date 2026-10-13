"""p5: one adapter class, the mode as a type parameter.  Cases 1-3 as for the
other probes, plus the ones only p5 has: the wrong facade, a bare call in
async mode, and a mode-free helper used from both."""
from typing import Any

from p5_adapter.adapter import GatherMode, TaskAdapter
from p5_adapter._async.ops import AsyncCollection
from p5_adapter._sync.ops import SyncCollection

def rename(item: TaskAdapter[object], name: str) -> None:
    item.summary = name               # mode-free helper - must NOT error

def sync_ok(coll: SyncCollection) -> None:
    task = coll.get_task("x")
    rename(task, "y")
    task.sync.save()
    task.save()                       # CASE 1: correct - must NOT error

async def async_ok(coll: AsyncCollection) -> None:
    task = await coll.get_task("x")
    rename(task, "y")
    await task.aio.save()             # CASE 1b: correct - must NOT error

async def async_misuse(coll: AsyncCollection) -> None:
    task = coll.get_task("x")
    print(task.summary)               # CASE 2: WRONG - should error

async def sync_misuse(coll: SyncCollection) -> None:
    task = await coll.get_task("x")   # CASE 3: WRONG - should error
    print(task.summary)

async def wrong_facade(coll: AsyncCollection) -> None:
    task = await coll.get_task("x")
    task.sync.save()                  # CASE 4: WRONG - sync facade on an async item

def wrong_facade_2(coll: SyncCollection) -> None:
    task = coll.get_task("x")
    task.aio.save()                   # CASE 4b: WRONG - async facade on a sync item

async def bare_in_async(coll: AsyncCollection) -> None:
    task = await coll.get_task("x")
    task.save()                       # CASE 5: WRONG - bare call in async mode

def gathered(item: TaskAdapter[GatherMode]) -> None:
    item.save()                       # CASE 6: correct - queueing is allowed anywhere

async def forgot_await(coll: AsyncCollection) -> None:
    task = await coll.get_task("x")
    task.aio.save()                   # CASE 7: WRONG - missing await on the facade

def helper_any(item: TaskAdapter[Any]) -> None:
    item.sync.save()                  # CASE 8: WRONG for async items - Any hides it

def helper_object(item: TaskAdapter[object]) -> None:
    item.sync.save()                  # CASE 9: WRONG for async items - object does not
