"""Produce the numbers quoted in docs/design/SYNC_ASYNC_ARCHITECTURE.md.

Run:  python prototypes/sync_async/measure.py
"""

from __future__ import annotations

import ast
import asyncio
import io
import os
import sys
import traceback
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

import common  # noqa: E402
import p1_dual_mode  # noqa: E402
import p2_sansio  # noqa: E402
import p3_greenlet  # noqa: E402
from p4_codegen._async import tasks as p4_async  # noqa: E402
from p4_codegen._sync import tasks as p4_sync  # noqa: E402
from common import AsyncTransport, Store, SyncTransport  # noqa: E402

HERE = Path(__file__).parent


def code_lines(path: Path) -> int:
    """Lines of actual code: no blanks, no comments, no docstrings."""
    src = path.read_text()
    tree = ast.parse(src)
    doc_lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                if isinstance(body[0].value.value, str):
                    doc_lines.update(range(body[0].lineno, (body[0].end_lineno or body[0].lineno) + 1))
    n = 0
    for i, line in enumerate(src.splitlines(), start=1):
        s = line.strip()
        if not s or s.startswith("#") or i in doc_lines:
            continue
        n += 1
    return n


def make(proto: str, mode: str, store: Store) -> Any:
    transport = AsyncTransport(store) if mode == "async" else SyncTransport(store)
    if proto == "p1_dual_mode":
        return p1_dual_mode.Collection(transport, is_async=(mode == "async"))
    if proto == "p2_sansio":
        return p2_sansio.Collection(transport)
    if proto == "p4_codegen":
        return p4_async.AsyncCollection(transport) if mode == "async" else p4_sync.SyncCollection(transport)
    return p3_greenlet.Collection(transport)


def _short(filename: str) -> str:
    """A frame's file relative to this directory, else its basename.

    p4 has a ``tasks.py`` in both ``_async/`` and ``_sync/``; a bare basename
    would not say which copy failed.
    """
    p = Path(filename)
    try:
        return str(p.relative_to(HERE))
    except ValueError:
        return p.name


def capture_traceback(proto: str, mode: str) -> tuple[int, list[str], str]:
    """Fail on page 2 of a paginated search; return (frames, files, text)."""
    store = Store().seed(5)
    store.fail_on.add("/search?cursor=2")
    coll = make(proto, mode, store)

    async def run_async() -> None:
        await coll.search()

    try:
        if mode == "async":
            asyncio.run(run_async())
        else:
            coll.search()
    except common.BackendError:
        buf = io.StringIO()
        traceback.print_exc(file=buf)
        text = buf.getvalue()
        tb = sys.exc_info()[2]
        frames = traceback.extract_tb(tb)
        files = [_short(f.filename) for f in frames]
        return len(frames), files, text
    raise AssertionError("expected BackendError")


OPS = {"get_task", "search", "save", "complete", "uncomplete"}

MACHINERY = {
    "p1_dual_mode.py": [],  # no shared machinery; the pattern *is* the duplication
    "p2_sansio.py": ["SansIOMisuse", "guard", "_drive_sync", "_drive_async", "public"],
    "p3_greenlet.py": ["_Missing", "await_", "greenlet_spawn", "_AsyncTransportAdapter", "public"],
}


def count_ops(path: Path) -> int:
    """Public I/O operations a prototype exposes.

    Counted, not assumed: an earlier version hard-coded 5 for every file, which
    silently charged p2 for a sixth body it happened to contain (review
    finding 14).  Both `def save(...)` and `save = public(_io_save)` count.
    """
    tree = ast.parse(path.read_text())
    found: set[str] = set()
    for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
        for node in cls.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in OPS:
                found.add(node.name)
            elif isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name) and tgt.id in OPS:
                        found.add(tgt.id)
    return len(found)


def machinery_lines(path: Path, names: list[str]) -> int:
    """Code lines of the fixed overhead: named definitions plus module-level setup.

    Module-level statements (imports, type aliases, the ContextVar) are fixed
    cost too, and charging them per method was the second half of finding 14.
    """
    src = path.read_text()
    tree = ast.parse(src)
    total = 0
    for node in tree.body:
        is_named = getattr(node, "name", None) in names
        is_module_level_setup = not isinstance(
            node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        )
        if not (is_named or is_module_level_setup):
            continue
        seg = ast.get_source_segment(src, node) or ""
        sub = ast.parse(seg)
        docs: set[int] = set()
        for n2 in ast.walk(sub):
            if isinstance(n2, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                b = n2.body
                if b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant):
                    if isinstance(b[0].value.value, str):
                        docs.update(range(b[0].lineno, (b[0].end_lineno or b[0].lineno) + 1))
        for i, line in enumerate(seg.splitlines(), start=1):
            t = line.strip()
            if t and not t.startswith("#") and i not in docs:
                total += 1
    return total


def scaling_table() -> None:
    print()
    print("=" * 72)
    print("HOW THE COST SCALES  (fixed machinery vs per-I/O-method cost)")
    print("=" * 72)
    rows = []
    for label, fname in [
        ("p1 runtime dual-mode", "p1_dual_mode.py"),
        ("p2 generator Sans-I/O", "p2_sansio.py"),
        ("p3 greenlet", "p3_greenlet.py"),
    ]:
        total = code_lines(HERE / fname)
        fixed = machinery_lines(HERE / fname, MACHINERY[fname])
        ops = count_ops(HERE / fname)
        per = (total - fixed) / ops
        rows.append((label, total, fixed, per, ops))
        print(f"  {label:24} total={total:4}  fixed={fixed:3}  ops={ops}  per method={per:5.1f}")
    # p2b, the typed-facade variant: p2_typed.py re-implements the core and imports
    # only the drivers and guard from p2_sansio, so its fixed cost is those
    # (not p2_sansio's ``public``) plus p2_typed's own module-level setup, and
    # its per-method cost is one core body plus two facade methods.
    typed = HERE / "p2_typed.py"
    typed_setup = machinery_lines(typed, [])
    fixed = machinery_lines(HERE / "p2_sansio.py", ["SansIOMisuse", "guard", "_drive_sync", "_drive_async"])
    fixed += typed_setup
    ops = count_ops(typed)
    per = (code_lines(typed) - typed_setup) / ops
    total = int(fixed + ops * per)
    label = "p2b Sans-I/O + typed"
    rows.append((label, total, fixed, per, ops))
    print(f"  {label:24} total={total:4}  fixed={fixed:3}  ops={ops}  per method={per:5.1f}")
    # p4: only _async/ is written by hand.  Counted by p2b's rule: module-level
    # setup is fixed, the rest is per method.  Tooling is charged to neither
    # design here (review finding m3) and is printed separately below.
    src = HERE / "p4_codegen" / "_async" / "tasks.py"
    setup = machinery_lines(src, [])
    ops = count_ops(src)
    per = (code_lines(src) - setup) / ops
    total = int(setup + ops * per)
    label = "p4 unasync, written"
    rows.append((label, total, setup, per, ops))
    print(f"  {label:24} total={total:4}  fixed={setup:3}  ops={ops}  per method={per:5.1f}")
    shipped = code_lines(src) + code_lines(HERE / "p4_codegen" / "_sync" / "tasks.py")
    print(f"  {'':24} shipped, both copies: {shipped} lines")
    print()
    print("  Tooling, not shipped and not counted above:")
    print(f"    p2b check_ast.py         {code_lines(HERE / 'check_ast.py'):4} lines")
    print(f"    p4  generate.py          {code_lines(HERE / 'p4_codegen' / 'generate.py'):4} lines")
    print()
    print("  Extrapolated to caldav's 57 dual-mode methods (fixed + 57 x per):")
    for label, total, fixed, per, ops in rows:
        print(f"    {label:24} ~{int(fixed + 57 * per):5} lines")
    print()
    print("  NOTE: lines of code are NOT the argument for either design.")
    print("  p2b is the largest at scale: typed facades are paid per method.  p1")
    print("  buys its footprint by making every composite method a latent bug;")
    print("  p4 by writing in the one style a token rewrite can translate.")


def demos() -> None:
    """The blocks the design document quotes, produced from committed code."""
    import asyncio as _asyncio
    import subprocess

    import p2_sansio
    import p2_sansio_bug

    print()
    print("=" * 72)
    print("THE DUAL-MODE BUG  (p1, async: the object looks right, the server is stale)")
    print("=" * 72)
    store = Store().seed(5)
    coll = p1_dual_mode.Collection(AsyncTransport(store), is_async=True)

    async def _run() -> None:
        task = await coll.get_task("task-1")
        await task.complete()
        result = task.uncomplete()
        print(f"  uncomplete() returned: {type(result).__name__}  status={result.status}")
        print(f"  server still says:     {store.tasks['task-1']['status']}")

    _asyncio.run(_run())

    print()
    print("=" * 72)
    print("THE SANS-I/O GUARD  (fires in both modes, from p2_sansio_bug.py)")
    print("=" * 72)
    for mode in ("sync", "async"):
        st = Store().seed(5)
        transport = AsyncTransport(st) if mode == "async" else SyncTransport(st)
        buggy = p2_sansio_bug.BuggyTask(uid="task-1")
        buggy._transport = transport
        try:
            r = buggy.uncomplete()
            if mode == "async":
                _asyncio.run(r)
            print(f"  {mode:6} NOT CAUGHT - regression")
        except p2_sansio.SansIOMisuse as exc:
            print(f"  {mode:6} {type(exc).__name__}: {exc}")

    print()
    print("=" * 72)
    print("THE STATIC CHECK  (same bug, found without running anything)")
    print("=" * 72)
    for target in ("p2_sansio.py p2_typed.py", "p2_sansio_bug.py"):
        out = subprocess.run(
            [sys.executable, str(HERE / "check_ast.py"), *target.split()],
            capture_output=True, text=True,
        )
        for line in out.stdout.strip().splitlines():
            print(f"  {line}")
        print(f"  -> exit {out.returncode}")

    print()
    print("=" * 72)
    print("THE CODEGEN SLIP  (p4: which shapes of a missing await a type checker flags)")
    print("=" * 72)
    specimen = HERE / "p4_codegen" / "_async" / "buggy.py"
    shapes = {
        i: line.split("# SLIP ")[1].split(":")[0]
        for i, line in enumerate(specimen.read_text().splitlines(), start=1)
        if "# SLIP " in line
    }
    checkers = {
        "mypy": [sys.executable, "-m", "mypy", "--no-incremental", "--cache-dir=/dev/null"],
        "mypy --check-untyped-defs": [
            sys.executable, "-m", "mypy", "--no-incremental", "--cache-dir=/dev/null", "--check-untyped-defs",
        ],
        "pyright": ["pyright", "--pythonpath", sys.executable],
    }
    print(f"  {'shape':14} " + "  ".join(f"{name:>25}" for name in checkers))
    results: dict[str, set[int] | None] = {}
    for name, cmd in checkers.items():
        try:
            out = subprocess.run(
                [*cmd, str(specimen)], capture_output=True, text=True, cwd=HERE,
                env={**os.environ, "MYPYPATH": str(HERE), "PYTHONPATH": str(HERE)},
            )
        except FileNotFoundError:
            results[name] = None
            continue
        if "No module named mypy" in out.stderr:
            results[name] = None
            continue
        results[name] = {
            int(ln.split(".py:")[1].split(":")[0])
            for ln in out.stdout.splitlines()
            if "unused-coroutine" in ln or "reportUnusedCoroutine" in ln
        }
    for lineno, shape in shapes.items():
        cells = []
        for name in checkers:
            r = results[name]
            cells.append("not installed" if r is None else ("flagged" if lineno in r else "MISSED"))
        print(f"  {shape:14} " + "  ".join(f"{c:>25}" for c in cells))


def main() -> None:
    print("=" * 72)
    print("LINES OF CODE (code only: no blanks, comments or docstrings)")
    print("=" * 72)
    files = {
        "p1 runtime dual-mode": ["p1_dual_mode.py"],
        "p2 generator Sans-I/O": ["p2_sansio.py"],
        "p3 greenlet": ["p3_greenlet.py"],
        "p4 unasync, hand-written": ["p4_codegen/_async/tasks.py"],
        "p4 unasync, generated": ["p4_codegen/_sync/tasks.py"],
        "p5 adapter, hand-written": ["p5_adapter/adapter.py", "p5_adapter/_async/ops.py"],
        "p5 adapter, generated": ["p5_adapter/_sync/ops.py"],
    }
    for label, names in files.items():
        total = sum(code_lines(HERE / n) for n in names)
        detail = " + ".join(f"{n}:{code_lines(HERE / n)}" for n in names)
        print(f"  {label:32} {total:4}   ({detail})")
    # p2_typed.py re-implements the core; from p2_sansio it uses only the
    # drivers and guard.  Adding the whole of p2_sansio.py would count every
    # I/O body twice.
    drivers = machinery_lines(HERE / "p2_sansio.py", ["SansIOMisuse", "guard", "_drive_sync", "_drive_async"])
    typed = code_lines(HERE / "p2_typed.py")
    print(f"  {'p2b Sans-I/O + typed façade':32} {typed + drivers:4}   "
          f"(p2_typed.py:{typed} + drivers and guard from p2_sansio.py:{drivers})")
    print(f"\n  {'shared scaffolding (common.py)':32} {code_lines(HERE / 'common.py'):4}")
    print(f"  {'one shared test suite':32} {code_lines(HERE / 'test_conformance.py'):4}")

    scaling_table()
    demos()

    print()
    print("=" * 72)
    print("TRACEBACK QUALITY  (backend raises on page 2 of a paginated search)")
    print("=" * 72)
    for proto in ("p1_dual_mode", "p2_sansio", "p3_greenlet", "p4_codegen"):
        for mode in ("sync", "async"):
            n, names, text = capture_traceback(proto, mode)
            objlayer = "yes" if any(f.startswith(("p1_", "p2_", "p3_", "p4_")) for f in names) else "NO"
            print(f"  {proto:14} {mode:6} frames={n:2}  object-layer frame visible: {objlayer}")
            print(f"       {' -> '.join(names)}")
    print()
    print("Full async traceback for the greenlet prototype (the one at risk):")
    print("-" * 72)
    print(capture_traceback("p3_greenlet", "async")[2])


if __name__ == "__main__":
    main()
