"""The committed ``_sync/`` copies are what the generator makes from ``_async/`` today."""

import ast
import filecmp
import importlib
import importlib.util
import inspect
import io
import re
import sys
import tokenize
from pathlib import Path

import pytest
import unasync

from calendaring.items import Item

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


def async_sources() -> list[Path]:
    return sorted(p for root in generate_sync.ROOTS for p in (root / "_async").rglob("*.py"))


def names(path: Path) -> list[tokenize.TokenInfo]:
    return [t for t in tokenize.generate_tokens(io.StringIO(path.read_text()).readline) if t.type == tokenize.NAME]


def renamed_by_unasync(name: str) -> bool:
    """unasync's own rule: ``AsyncX`` becomes ``SyncX`` whatever it names."""
    return name.startswith("Async") and name[5:6].isupper()


def test_async_sources_use_no_third_party_async_name() -> None:
    # httpx.AsyncClient would become httpx.SyncClient, which does not exist.
    # Allowed: unasync's own table (AsyncIterator -> Iterator), our own
    # replacement map, and the classes this repository defines in _async/.
    ours = {
        node.name
        for path in async_sources()
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, ast.ClassDef)
    }
    allowed = ours | set(unasync._ASYNC_TO_SYNC) | set(generate_sync.REPLACEMENTS)
    for path in async_sources():
        for token in tokenize.generate_tokens(io.StringIO(path.read_text()).readline):
            # unasync also renames a string that is nothing but a name (a forward reference)
            name = token.string[1:-1] if token.type == tokenize.STRING else token.string
            if token.type in (tokenize.NAME, tokenize.STRING) and renamed_by_unasync(name):
                assert name in allowed, (
                    f"{path}:{token.start[0]}: {name} is not ours; wrap it in the per-mode layer "
                    "(calendaring/_concurrency.py) or add a replacement to tools/generate_sync.py"
                )


def test_async_sources_hold_no_await_call() -> None:
    # unasync drops the token after `await` blindly, meant to be the space:
    # await(self.save()) becomes self.save()) (0.2 §9).  `await (x).y` is fine,
    # anything touching `await` is not.
    for path in async_sources():
        tokens = list(tokenize.generate_tokens(io.StringIO(path.read_text()).readline))
        for this, following in zip(tokens, tokens[1:], strict=False):
            if this.type == tokenize.NAME and this.string == "await":
                assert this.end != following.start, f"{path}:{this.start[0]}: put a space after `await`"


def test_every_replacement_is_still_used() -> None:
    used = {token.string for path in async_sources() for token in names(path)}
    # "_async" stays: it guards an absolute import of the package's own async modules.
    stale = set(generate_sync.REPLACEMENTS) - used - {"_async"}
    assert not stale, f"drop these from REPLACEMENTS in tools/generate_sync.py: {sorted(stale)}"


@pytest.mark.parametrize(
    "module", sorted(p.stem for p in (REPO / "src" / "calendaring" / "_sync").glob("*.py") if p.stem != "__init__")
)
def test_only_bound_items_keep_the_sync_prefix(module: str) -> None:
    # API_DESIGN.md §1.1: the sync I/O classes are unprefixed (Workspace, not
    # SyncWorkspace), the bound items are not (SyncTask), nor is the suite a
    # backend's tests subclass (SyncConformance).  A new I/O class
    # in _async/ needs its entry in REPLACEMENTS, or it ships as SyncX.
    generated = importlib.import_module(f"calendaring._sync.{module}")
    for name, cls in inspect.getmembers(generated, inspect.isclass):
        if cls.__module__ == generated.__name__ and name.startswith("Sync") and name != "SyncConformance":
            assert issubclass(cls, Item), f"{module}.{name}: add Async{name[4:]} to REPLACEMENTS"


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


# The Go convention (golang.org/s/generatedcode), in Python comment syntax:
# tools that skip generated code look for this line near the top of the file.
GENERATED_HEADER = re.compile(r"^# Code generated .* DO NOT EDIT\.$")


def test_generated_files_carry_the_standard_header() -> None:
    for root in generate_sync.ROOTS:
        for path in (root / "_sync").rglob("*.py"):
            first = path.read_text().splitlines()[:1]
            assert first, f"{path} is empty"
            assert GENERATED_HEADER.match(first[0]), f"{path}: {first[0]!r}"


def test_an_empty_async_source_is_refused(tmp_path: Path) -> None:
    (tmp_path / "_async").mkdir()
    (tmp_path / "_async" / "empty.py").write_text("")
    with pytest.raises(ValueError, match="empty"):
        generate_sync.generate(tmp_path)
