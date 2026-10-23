# Contributing to calendaring

Contributions are most welcome (but do inform about it if you've used AI or other tools).  If the length of this text scares you, then I'd rather want you to skip reading and just produce a pull-request in GitHub.  If you find it too difficult to write test code, etc, then you may skip it and hope the maintainer will fix it.

## What to include

Every submission should ideally include:

- **Test code** covering the new behaviour or bug fix
- **Documentation** updates where relevant
- **A changelog fragment** in `news/` (see below)

## Changelog fragments

The change log is assembled by [towncrier](https://towncrier.readthedocs.io/)
at release time. Add one small reStructuredText file per change to `news/`,
named `<issue or PR number>.<type>.rst`, for example `42.bugfix.rst`. The
types are listed under `[tool.towncrier]` in `pyproject.toml`: `breaking`,
`removal`, `feature`, `bugfix`, `documentation`, `deps`, `internal`, `chore`
and `security`. `towncrier create` makes the file for you. A pull request
with nothing worth mentioning can be labelled `skip-changelog`.

## Development setup

```
python3 -m venv .venv
. .venv/bin/activate
make dev
pre-commit install --hook-type pre-commit --hook-type pre-push --hook-type commit-msg
make test
```

`make dev` also installs caldav and the embedded Xandikos and Radicale
servers, which the CalDAV backend's conformance tests run against (they are
skipped when missing).  Radicale listens on its default port, 5232, whatever
caldav's test helper is told, so that port must be free.

## Sync and async code

The async code in the `_async/` directories is the source; the `_sync/`
directories next to them are generated from it with unasync and committed.
Edit `_async/` only, then run

```
python tools/generate_sync.py
```

The generator renames `AsyncX` to `SyncX`, drops `async` and `await`, and
applies the replacement map in `tools/generate_sync.py`.  Every file in
`_async/` starts with a one-line comment, which becomes the do-not-edit
header in its copy.  `tests/test_generated_sync.py` fails if a `_sync/`
copy is stale, and it also rejects what a token rewrite cannot translate:

- `asyncio`, threads, sleeps and anything else with no token-for-token sync
  equivalent go in `src/calendaring/_concurrency.py`, written once per mode,
  with an entry in the replacement map (`agather` -> `gather`); blocking
  I/O, such as the files backend's, goes through `await ato_thread(call, ...)`,
  which runs it in a worker thread in the async mode and directly in the sync
  one (`to_thread`);
- a third-party `Async*` name, such as `httpx.AsyncClient`, would become a
  `SyncClient` that does not exist: import it through a mode-free module and
  map it in the replacement map, as `_caldav_io.py` does for caldav's
  `AsyncDAVClient`;
- a library whose methods return `X | Coroutine` (caldav's dual-mode
  objects) hides a missing `await` from the type checker: call it only
  through a thin, honestly typed wrapper in `_async/`, as `_async/dav.py`
  does, where one helper resolves the union per mode (`_awaited`, which the
  generator turns into `_returned`);
- the token right after `await` is dropped, meant to be the space:
  write `await x`, never `await(x)`;
- a name the generator renames must not sit inside an f-string, since
  whether it gets renamed there depends on the Python version.

A forgotten `await` in `_async/` is a correct call once the generator has
removed it, so the sync tests cannot catch it.  mypy (strict) and ruff
(F841) can, and `tests/test_enforcement.py` checks that they still do, on
the planted mistakes in `tests/specimens/`.  So keep the code fully
annotated, and do not silence an `unused-coroutine` error.

### Adding a backend

1. Write it in `src/calendaring/_async/<name>.py` as `Async<Name>Backend`
   and `Async<Name>Collection`, subclassing `AsyncBackend` and
   `AsyncCollection`.  Set `kind = "<name>"` on the backend class, so
   `Backend.open(kind="<name>")` finds it.  The backend implements
   `_open` and `collections`; the collection implements the primitives
   (`_query`, `_fetch`, `_create`, `_update`, `_delete`).  Capability
   checks, loss policies, client-side filtering and the rest are written
   once in the base classes.  `_async/memory.py`, the reference backend,
   is the smallest complete example.  A backend with a time log of its
   own (a tracker) also overrides `_time_entries`, `_time_log`,
   `_add_time`, `_amend_time` and `_delete_time`; the defaults store
   entries as `VEVENT`s.
2. Declare its capabilities honestly, in the `capabilities` each
   constructor takes.  The conformance suite asserts that an operation
   declared unsupported fails as specified.
3. Add both class names to `REPLACEMENTS` in `tools/generate_sync.py`, so
   the sync classes are unprefixed (`<Name>Backend`, not
   `Sync<Name>Backend`).  A test fails if you forget.
4. Run the generator, and import both modes from a public module (as
   `calendaring/memory.py` does).  Import that module in
   `calendaring/__init__.py`: a backend registers its `kind` when it is
   imported, so until then `open()` cannot find it.
5. Add `tests/_async/test_<name>_conformance.py`, with a class that
   subclasses `calendaring.testing.AsyncConformance` and returns the
   backend from `open_backend()`.  The generator writes the sync tests.

## Commit messages

Please follow [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) and write messages in the imperative mood:

- `fix: correct time-range search handling for recurring events`
- `feat: add new check`
- `docs: update README`

Rather than:

- `This commit fixes the time-range search`
- `Added new check`
