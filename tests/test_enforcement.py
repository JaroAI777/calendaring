"""The static checks the sync/async design relies on fire on committed specimens (roadmap 1.3).

A missing ``await`` in ``_async/`` is a correct call once the generator has
removed it, so the sync suite cannot see it, and the async suite only sees
it on a path a test runs.  The type checker and ruff are the protection
(SYNC_ASYNC_ARCHITECTURE.md §5), so these tests assert that both, run with
the project's own configuration, flag every planted shape in
``tests/specimens/`` and nothing else there.

The tools are dev dependencies, and a missing one fails the test rather
than skipping it: a skipped gate in CI is a gate nobody runs.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SPECIMENS = sorted((REPO / "tests" / "specimens").glob("*.py"))


def expected(marker: str) -> set[tuple[str, int, str]]:
    """``(file, line, code)`` for every ``# <marker>: CODE, ...`` comment in the specimens."""
    pattern = re.compile(rf"#\s*{marker}:\s*([\w, -]+)$")
    found = set()
    for path in SPECIMENS:
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if m := pattern.search(line):
                found |= {(path.name, number, code.strip()) for code in m.group(1).split(",")}
    return found


def run(*args: str) -> str:
    out = subprocess.run([sys.executable, "-m", *args], capture_output=True, text=True, cwd=REPO, check=False)
    assert "No module named" not in out.stderr, f"{args[0]} is not installed: pip install -e '.[dev]'"
    return out.stdout


def test_specimens_exist() -> None:
    assert {p.name for p in SPECIMENS} >= {"async_mode.py", "sync_mode.py"}
    assert expected("expect")
    assert expected("expect-ruff")


def test_mypy_flags_exactly_the_planted_misuse_in_both_modes() -> None:
    # The specimens are excluded from the plain `mypy` run; naming them checks them anyway.
    stdout = run("mypy", "--no-incremental", *map(str, SPECIMENS))
    errors = re.findall(r"^[^:\n]+:\d+: error: .*$", stdout, re.M)
    flagged = [
        (Path(m["file"]).name, int(m["line"]), m["code"])
        for m in re.finditer(r"^(?P<file>[^:\n]+):(?P<line>\d+): error: .*\[(?P<code>[\w-]+)\]$", stdout, re.M)
    ]
    # A count as well as a set: an error without a code, or the same one twice, is unexpected too.
    assert len(errors) == len(flagged) == len(set(flagged)), stdout
    assert set(flagged) == expected("expect"), stdout
    assert f"Found {len(errors)} errors in" in stdout, stdout


def test_ruff_flags_the_assigned_and_unused_coroutine() -> None:
    # The one shape mypy misses that ruff catches (F841); --no-force-exclude
    # because the specimens are excluded from the plain `ruff check .`.
    stdout = run("ruff", "check", "--no-force-exclude", "--output-format", "concise", *map(str, SPECIMENS))
    flagged = {
        (Path(m["file"]).name, int(m["line"]), m["code"])
        for m in re.finditer(r"^(?P<file>[^:\n]+):(?P<line>\d+):\d+: (?P<code>[A-Z]+\d+) ", stdout, re.M)
    }
    assert flagged == expected("expect-ruff"), stdout


def test_a_dropped_coroutine_fails_the_test_suite(tmp_path: Path) -> None:
    # The runtime backstop: under the project's filterwarnings = error, the
    # never-awaited RuntimeWarning fails the async test that dropped it,
    # through pytest's unraisable-exception hook (0.2 §5).
    test = tmp_path / "test_dropped.py"
    test.write_text("async def save() -> None: ...\n\n\nasync def test_drops() -> None:\n    save()\n")
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "-c", str(REPO / "pyproject.toml"), "-p", "no:cacheprovider", str(test)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        check=False,
    )
    assert out.returncode == 1, out.stdout
    assert "coroutine 'save' was never awaited" in out.stdout
