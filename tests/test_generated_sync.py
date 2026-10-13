"""The committed ``_sync/`` copies are what the generator makes from ``_async/`` today."""

import filecmp
import importlib.util
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("generate_sync", REPO / "tools" / "generate_sync.py")
assert spec is not None
assert spec.loader is not None
generate_sync = importlib.util.module_from_spec(spec)
sys.modules["generate_sync"] = generate_sync
spec.loader.exec_module(generate_sync)


@pytest.mark.parametrize("root", generate_sync.ROOTS, ids=lambda p: str(p.relative_to(REPO)))
def test_generated_sync_code_is_fresh(root: Path, tmp_path: Path) -> None:
    expected = generate_sync.generate(root, out_root=tmp_path)
    committed = {p.relative_to(root / "_sync") for p in (root / "_sync").rglob("*.py")}
    assert committed == expected, "stale file set: run python tools/generate_sync.py"
    for rel in sorted(expected):
        assert filecmp.cmp(tmp_path / "_sync" / rel, root / "_sync" / rel, shallow=False), (
            f"_sync/{rel} is stale: run python tools/generate_sync.py"
        )


def test_async_sources_hold_nothing_a_token_rewrite_breaks() -> None:
    for root in generate_sync.ROOTS:
        for path in (root / "_async").rglob("*.py"):
            text = path.read_text()
            assert "import asyncio" not in text, f"{path}: asyncio belongs in _concurrency.py"
            assert "asyncio." not in text, f"{path}: asyncio belongs in _concurrency.py"
            assert "aclose" not in text, f"{path}: unasync does not rewrite aclose"


# Python 3.12 (PEP 701) tokenises an f-string into parts, 3.11 keeps it as
# one STRING token, and unasync renames names only in the parts.  A name to
# rewrite inside an f-string expression would make the generated code
# depend on the Python version the generator ran on.
RENAMED_IN_FSTRING = re.compile(
    r"""f["'][^"'\n]*\{[^}]*\b(Async[A-Z]\w*|""" + "|".join(generate_sync.REPLACEMENTS) + r")\b"
)


def test_no_renamed_name_inside_an_f_string() -> None:
    for root in generate_sync.ROOTS:
        for path in (root / "_async").rglob("*.py"):
            for number, line in enumerate(path.read_text().splitlines(), 1):
                assert not RENAMED_IN_FSTRING.search(line), f"{path}:{number}: move the name out of the f-string"
