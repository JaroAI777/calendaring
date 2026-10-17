The checks the sync/async design relies on are now tested: mypy and ruff
must flag the planted misuse in ``tests/specimens/`` in both modes, a
dropped coroutine must fail the test suite, and ``_async/`` must not use
third-party ``Async*`` names or ``await(...)``.
