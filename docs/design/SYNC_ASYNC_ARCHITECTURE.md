# Sync/async architecture: comparison

Creating Python packages that can serve both sync and async usage without significant code duplication is a challenge.  The current paragraph and the decision at the end is hand-written, the rest of the document is almost entirely AI-generated.  This document provides the research fundament for deciding on the async/sync architecture of the calendaring client library.  The document does not hold significant value after the decision has been made - hence it's not worth putting lots of editorial efforts into it.

---

**Roadmap item:** [0.2 Sync/async architecture](ROADMAP.md#02-syncasync-architecture)

**Status:** decided — [p4, async-first with unasync](#p4), async-first source with an `unasync`-generated sync
copy ([§11](#11-decision)). Revisited for the adapter pattern on 2026-10-10 ([§12](#12-revisited-the-adapter-pattern-2026-10-10)); the decision stands

**Deliverable:** this comparison, backed by runnable prototypes in
[`prototypes/sync_async/`](../../prototypes/sync_async/)

**In one paragraph:** of the five candidates, two hold up: a generator-based
Sans-I/O core with two thin typed façades (**[p2b, Sans-I/O with typed façades](#p2b)**), and async-first source with
an `unasync`-generated sync copy (**p4**). Dual-mode ([p1, runtime dual-mode](#p1)) is the design whose
failures prompted this item, greenlet ([p3, greenlet bridging](#p3)) is no safer and adds a C dependency
([§8](#8-greenlet-not-competitive)), and separate hand-written classes duplicate every method ([§3](#3-the-candidates)). p2b and p4
both give one implementation of every I/O method and correct public types in
both modes ([§6](#6-result-type-checking-and-a-finding-that-changes-the-shape-of-the-answer)). They differ in how they catch the composition mistake ([§5](#5-result-correctness-under-composition)) and
in what writing and maintaining them costs ([§7](#7-result-size-and-what-it-does-not-tell-us), [§10](#10-p2b-and-p4-compared)). The prototypes do not
settle which serves the project better; [§10](#10-p2b-and-p4-compared) sets out the trade, and the
comparison does not recommend one over the other. The author's decision, p4,
is in [§11](#11-decision); the open items in [§10](#10-p2b-and-p4-compared) marked (p2b) no longer apply.

**The trade in short.** Methods that make several round trips read the same in
both ([§10](#10-p2b-and-p4-compared) shows them side by side).

- **p2b** turns the exact caldav mistake — calling a public I/O method from
  inside another — into an exception, in sync tests as well as async ones. It
  has no generated code and no build step, and its bodies cannot `await`, so
  `asyncio` stays out of them by construction rather than by rule. It costs a
  façade layer (about 8.6 mechanical lines per I/O method, [§7](#7-result-size-and-what-it-does-not-tell-us)), a core typed
  through `Any` so the checker does not see inside the bodies, and a rule
  contributors must learn: compose with `yield from self._io_x()`, never call
  the public name. Forgetting the `yield from` is silent at runtime; a
  project-written AST check catches it, and so does pyright's opt-in, noisy
  `reportUnusedCallResult`.
- **p4** gives plain `async`/`await` bodies that a type checker sees into, and
  no façades. A missing `await` is flagged in its common shapes by stock type
  checkers and at runtime only by a `RuntimeWarning`, which `-W error` turns
  into a failure on paths an async test runs. It costs a generated `_sync/`
  copy that tracebacks and breakpoints point into, a freshness test, a
  type checker as a hard CI gate, and a rule contributors must learn: nothing in
  `_async/` that a token rewrite breaks (`asyncio`, third-party `Async*`
  names).

---

## 1. Method

Everything measured below has been prototyped and tested; four architectures are implemented against the *same* toy backend and driven by the *same* test suite, plus one API layer on top of the fourth (p5, [§12](#12-revisited-the-adapter-pattern-2026-10-10)):

| File | What it is |
|---|---|
| [`common.py`](../../prototypes/sync_async/common.py) | the toy backend, its sync/async/filesystem transports, and a fake server |
| [`p1_dual_mode.py`](../../prototypes/sync_async/p1_dual_mode.py) | runtime dual-mode — the caldav 3.x pattern, as the control |
| [`p2_sansio.py`](../../prototypes/sync_async/p2_sansio.py) | generator-based Sans-I/O |
| [`p2_sansio_bug.py`](../../prototypes/sync_async/p2_sansio_bug.py) | two planted shapes of the composition mistake: the public-name call the guard catches, and the dropped `_io_` generator only a static check catches |
| [`p2_typed.py`](../../prototypes/sync_async/p2_typed.py) | the same core plus typed façades — the runner-up, kept for reference |
| [`p3_greenlet.py`](../../prototypes/sync_async/p3_greenlet.py) | greenlet bridging, the SQLAlchemy pattern |
| [`p4_codegen/_async/tasks.py`](../../prototypes/sync_async/p4_codegen/_async/tasks.py) | async-first, the only hand-written copy — the chosen design |
| [`p4_codegen/_sync/tasks.py`](../../prototypes/sync_async/p4_codegen/_sync/tasks.py) | its sync copy, generated by `unasync` and committed |
| [`p4_codegen/generate.py`](../../prototypes/sync_async/p4_codegen/generate.py) | the generator; a test fails if the committed copy is stale |
| [`p4_codegen/_async/buggy.py`](../../prototypes/sync_async/p4_codegen/_async/buggy.py) | the caldav mistake, planted in async-first source |
| [`p5_adapter/`](../../prototypes/sync_async/p5_adapter/) | one item adapter with `.sync` / `.aio` façades and write strategies, on top of [p4, async-first with unasync](#p4)'s generator ([§12](#12-revisited-the-adapter-pattern-2026-10-10)) |
| [`test_conformance.py`](../../prototypes/sync_async/test_conformance.py) | one suite, 6 prototypes × 2 modes |
| [`test_p5_adapter.py`](../../prototypes/sync_async/test_p5_adapter.py) | what only [p5, adapter with per-mode façades](#p5) has: strategies, dirty tracking and its leak, the mode as a type parameter |
| [`measure.py`](../../prototypes/sync_async/measure.py) | produces every number quoted here |
| [`check_ast.py`](../../prototypes/sync_async/check_ast.py) | [p2b, Sans-I/O with typed façades](#p2b)'s static check; reference only, since p4 was chosen |

Reproduce from the repository root with `uv run --python 3.14 --with unasync
--with pytest --with pytest-asyncio --with greenlet --with mypy python -m pytest
prototypes/sync_async/ -q`, and the same for `measure.py` (add pyright to the
path for its column in [§5](#5-result-correctness-under-composition)). Current result: **141 passed, 1 xfailed**. Without
`unasync` or `mypy` the tests that need them skip, unless `CI` is set, in which
case they fail — a CI job that lacks a tool must not go green. Every fenced
output block below is printed by `measure.py`, on Python 3.14; two are
abridged, as marked.

**Names.** "p" is for *prototype*; the number is the order they were built in,
and the rest of the document refers to them by these short names:

- <a id="p1"></a>**p1**, runtime dual-mode: one class whose methods check at
  run time whether the transport is async (caldav 3.x), kept as the control.
- <a id="p2"></a>**p2**, generator Sans-I/O: each I/O method is written once
  as a generator that yields requests, driven by a sync or an async driver.
- <a id="p2b"></a>**p2b**, Sans-I/O with typed façades: p2's drivers and guard,
  with a core of its own and a hand-written sync and async façade class
  carrying the real signatures ([§6](#6-result-type-checking-and-a-finding-that-changes-the-shape-of-the-answer)).
- <a id="p3"></a>**p3**, greenlet bridging: a sync core run inside a greenlet
  that suspends on the async transport, as SQLAlchemy does ([§8](#8-greenlet-not-competitive)).
- <a id="p4"></a>**p4**, async-first with unasync: the async code is the
  source and `unasync` generates the committed sync copy. The chosen design
  ([§11](#11-decision)).
- <a id="p5"></a>**p5**, adapter with per-mode façades: one item class
  reached through `.sync` / `.aio`, with write strategies, on top of p4
  ([§12](#12-revisited-the-adapter-pattern-2026-10-10), added after the decision).

The xfail is the dual-mode bug. It is applied as a **strict** marker rather than
by calling `pytest.xfail()`, so it asserts two things rather than none: that the
server was not written to, and — because strict means an unexpected pass fails
the suite — that the day [p1, runtime dual-mode](#p1)'s bug stops reproducing, the suite says so.

### The toy backend

Two methods would not have been enough. The roadmap asks how each approach
survives a paginated HTTP backend and a filesystem backend, so the toy has four
shapes, and the last two are where designs actually break:

1. `get_task` — one request, one response
2. `save` — one request, mutating
3. `search` — **N requests in a loop** (pagination; what issue trackers force)
4. `complete` — **I/O, then logic, then more I/O** (composition)

Shape 4 is the important one. It is the exact shape of the caldav bug.

---

## 2. The problem, restated from evidence

caldav's [`ASYNC_DESIGN_CRITIQUE.md`](https://github.com/python-caldav/caldav/blob/master/docs/design/ASYNC_DESIGN_CRITIQUE.md)
records that a composite method which forgets its `is_async_client` check
discards a coroutine and does nothing. `p1_dual_mode.uncomplete()`
reproduces it verbatim, and `measure.py` prints it:

```
THE DUAL-MODE BUG  (p1, async: the object looks right, the server is stale)
  uncomplete() returned: BoundTask  status=NEEDS-ACTION
  server still says:     COMPLETED
```

The method returns an object of the right type, with the right attribute values,
and the server was never written to.

The critique also explains why the `RuntimeWarning: coroutine … was never
awaited` that Python does emit is too weak to count;
[`p1_dual_mode.py`](../../prototypes/sync_async/p1_dual_mode.py) reproduces it
(`measure.py` shows `p1_dual_mode.py:123 … self.save()  # BUG`).

So the failure is *not literally silent*, but it is silent enough that every
instance in caldav was found by writing a test for it rather than by observing
it. The comparison therefore asks, for each design, which shapes of the
mistake raise an exception, which do so in sync tests as well as async ones,
and which are caught statically, and by what.

---

## 3. The candidates

The roadmap lists five. Four were prototyped; one is cost-modelled only, with
the reason given.

| Approach | Prototyped | One source of truth | Build step | C dependency |
|---|---|---|---|---|
| Runtime dual-mode | yes (control) | no | no | no |
| Generator Sans-I/O | **yes** | yes | no | no |
| `greenlet` bridging | **yes** | yes | no | **yes** |
| Separate async classes | no — cost-modelled | no | no | no |
| Async-first + `unasync` codegen | **yes** | in source | dev-time only — output committed | no |

**Separate async classes** was not prototyped because its properties are not in
doubt: types are correct by construction, and every I/O method is written twice.
It is the honest baseline for "correct but duplicated". [p4, async-first with unasync](#p4) is in effect this
design with the second copy generated, and [§6](#6-result-type-checking-and-a-finding-that-changes-the-shape-of-the-answer) shows that [p2b, Sans-I/O with typed façades](#p2b) gets the same
typing benefit by hand-written façades instead.

**`unasync` codegen** follows httpcore rather than unasync's own `setup.py`
hook: the generated `_sync/` copy is **committed**, and
`test_generated_sync_code_is_fresh` regenerates it and fails on any difference.
That turns the build step into a dev-time step — installing the library runs
nothing, and `unasync` (which imports `setuptools` and `tokenize_rt`) is a
development dependency only.

**`asyncio.run()` wrapping** remains rejected without study, as the roadmap says.

---

## 4. Result: can one test suite drive both modes?

Yes, for all of them. [`test_conformance.py`](../../prototypes/sync_async/test_conformance.py)
parametrises over `{p1, p2, p2b, p3, p4} × {sync, async}`. The
only concession it makes is one three-line helper:

```python
async def maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value
```

This dimension therefore does **not** discriminate between the candidates, which
is itself worth knowing — it removes an argument that might otherwise have been
made for any of them.

---

## 5. Result: correctness under composition

This is where [p2b, Sans-I/O with typed façades](#p2b) and [p4, async-first with unasync](#p4) differ most. Neither catches every shape at runtime;
they cover different shapes with different tools.

| | Composite method that forgets the rule | Caught in sync mode? | Caught in async mode? | Catchable statically? |
|---|---|---|---|---|
| [p1](#p1) dual-mode | silently does nothing | **no — the code is correct in sync** | only a `RuntimeWarning` | no |
| [p2](#p2) / p2b, public name called | raises `SansIOMisuse` | yes | yes | partly — project-written AST check, with known holes |
| p2 / p2b, `_io_` body without `yield from` | silently does nothing | no | no | partly — the same AST check; pyright's opt-in `reportUnusedCallResult` |
| [p3](#p3) greenlet | silently does nothing | no | no | no |
| p4 unasync, missing `await` | does nothing in async mode | no — the generated sync copy is correct | only a `RuntimeWarning` | partly — some shapes, by stock type checkers |

p1's row is the whole problem: the mistake is **invisible in the mode most
developers run their tests in**. A contributor writes a composite method, the
sync suite passes, and the bug ships to async users.

**p3's row may look surprising**, since composition in greenlet is a plain
method call. But p3 also has a public wrapper (`save = public(_save)`) beside
the body (`_save`), so a composite body that calls `self.save()` instead of
`self._save()` reproduces p1's failure exactly — a coroutine from
`greenlet_spawn`, created and dropped. p3 has neither a runtime guard nor a
static check for it, so on this axis it is p1's equal, not p2's better. [§8](#8-greenlet-not-competitive)
follows the consequences.

p2 fixes the caldav shape with a `ContextVar`. Calling a public I/O method from
inside another one raises immediately, and it raises in sync mode too:

```
THE SANS-I/O GUARD  (fires in both modes, from p2_sansio_bug.py; abridged)
  sync   SansIOMisuse: a public I/O method was called from inside another I/O method.
  async  SansIOMisuse: a public I/O method was called from inside another I/O method.
```

**Where the guard is called turns out to be load-bearing**, and getting it wrong
recreates the very bug. `guard()` must run in a **synchronous frame at the call
site**. Putting it inside `_drive_async` does nothing, because that body does not
execute until the coroutine is awaited — so the discarded-coroutine mistake
discards the check along with it. This is why the async façades in `p2_typed` are
plain `def` returning a coroutine rather than `async def`. (`await
coll.get_task(...)` is unaffected, and [§6](#6-result-type-checking-and-a-finding-that-changes-the-shape-of-the-answer)'s typing results are unchanged.)

**The guard does not cover the other shape.** A body that calls the *private*
body but forgets `yield from` — `self._io_save()` as a bare statement — builds
a generator and drops it unstarted. No public name is called, so the guard never
runs, and Python does not warn about a generator that was never started. The
write is lost in both modes, silently, even under `-W error`.
`test_dropped_io_body_is_silent_at_runtime` plants it in p2b's shared core and
pins the hole; `p2_sansio_bug.py` holds the same shape (`_io_reopen`) for the
static check.

mypy does not flag an unused generator. pyright does, but only with
`reportUnusedCallResult`, which is off by default and in strict mode and flags
*every* unused return value, so enabling it means silencing legitimate ones
(assigning to `_`). Its noise on a real codebase has not been measured. Checked
by hand with `pyright` and `{"reportUnusedCallResult": "error"}` against
`p2_sansio_bug.py`: it reports line 45.

So for this shape the static check is the protection, not a belt to the
guard's braces. It runs against the same committed specimen:

```
THE STATIC CHECK  (same bug, found without running anything; abridged)
  OK - no Sans-I/O composition violations in p2_sansio.py, p2_typed.py
  -> exit 0
  p2_sansio_bug.py:37: save() is the public wrapper - in _io_uncomplete() use 'yield from self._io_save()'
  p2_sansio_bug.py:45: _io_save() in _io_reopen() without 'yield from'
  2 violation(s)
  -> exit 1
```

Both halves matter: a checker that flagged the façades' own driver calls would
reject the architecture it exists to protect. And it is a prototype with known
holes ([§10](#10-p2b-and-p4-compared), *What is not settled*, 6), so building it properly is part of
choosing p2b.

**p4 is partly caught by tooling that already exists.** Its row looks like
p1's at runtime — the slip is invisible in sync mode, because `self.save()` *is*
the correct sync call once `unasync` has removed the `await`
(`test_codegen_bug_is_async_only` asserts both halves). The difference is
upstream. In p1 the public method is typed `Union[Any, Coroutine]`, so a type
checker cannot tell a dropped coroutine from a used value. In p4 the source is
plain `async def save(self) -> AsyncTask`, and both mainstream checkers flag an
unused coroutine (mypy's `unused-coroutine`, pyright's `reportUnusedCoroutine`)
— but only for some shapes of the slip. `_async/buggy.py` plants three, and
`test_type_checker_flags_the_codegen_slip` pins the mypy results, misses
included:

```
THE CODEGEN SLIP  (p4: which shapes of a missing await a type checker flags)
  shape                               mypy  mypy --check-untyped-defs                    pyright
  bare                             flagged                    flagged                    flagged
  untyped                           MISSED                    flagged                    flagged
  comprehension                     MISSED                     MISSED                     MISSED
```

`bare` is the caldav shape. `untyped` is the same line in an unannotated helper:
default mypy does not check unannotated bodies at all, so **[1.3](ROADMAP.md#13-syncasync-scaffolding) must run pyright,
or mypy with `check_untyped_defs`**, if p4 is chosen. Further probing found
shapes both checkers miss:

- `r = self.save()` never awaited, also inside a loop (ruff's default F841
  flags the unused name, which makes it a third tool in the chain);
- `_ = self.save()`, `self.save() if dirty else None`, `dirty and self.save()`,
  `log("saved", self.save())`, `if (r := self.save()):`;
- `return self.save()` from a function annotated `-> Any` or not at all;
- any call on a receiver typed `Any` or `X | Coroutine` — which is every caldav
  object ([§10](#10-p2b-and-p4-compared), *What is not settled*, 5).

So p4's check covers the common shapes, not every shape, and it is static only.
p2b's runtime guard fires for every one of those shapes when the call is to one
of this library's public names, in sync tests as well. p2b's own silent shape,
the dropped `_io_` generator, is the counterweight (above). caldav's own
methods sit below the drivers, where neither design protects them (*What is
not settled*, 5).

One measure helps p1 and p4 alike: running the test suite with `-W error` makes
the never-awaited warning fail the async test that triggers it, through pytest's
unraisable-exception hook (`-W error::RuntimeWarning` alone does **not** — the
failure arrives as a `PytestUnraisableExceptionWarning`). Check with
`python -m pytest prototypes/sync_async/ -W error -k silently_drops`. It is a
backstop with holes of its own: it fires only if an *async* test runs that path,
and not at all for a coroutine kept alive in a long-lived container.

---

## 6. Result: type checking, and a finding that changes the shape of the answer

The roadmap asks for "mypy/pyright correctness of the public signatures". Three
cases were put to mypy for each prototype (2.3.1; re-checked with 2.4.0, same
results; pyright agrees for [p4, async-first with unasync](#p4))
([`typing_probe/`](../../prototypes/sync_async/typing_probe/)):

- **Case 1** — correct sync use. Must **not** error.
- **Case 2** — using the result without `await` in async mode. **Should** error.
- **Case 3** — `await`ing the result in sync mode. **Should** error.

| | Case 1 (correct) | Case 2 (missing await) | Case 3 (spurious await) |
|---|---|---|---|
| [p1](#p1) dual-mode | **false positive** | caught | missed |
| [p2](#p2) Sans-I/O bare | ok | **missed** | **missed** |
| [p3](#p3) greenlet | ok | **missed** | **missed** |
| [p2b](#p2b) typed façade | ok | caught | caught |
| p4 unasync | ok | caught | caught |

p1's `Union[Any, Coroutine]` is the worst outcome available: it rejects *correct*
code as well as catching one of the two errors. In practice a caller silences it
with `# type: ignore`, which then also silences the one real catch. p2 and p3, as
bare prototypes, return `Any` — their signatures do not lie, they say nothing.

**The finding.** Implementation duplication and type duplication are *separate
problems*, and no *runtime* mechanism solves both:

- The mode is a property of the **instance**. A type checker can only key on the
  **class**. Therefore correct types require two classes, no matter what the
  implementation looks like underneath.
  *(Corrected in [§12.2](#122-p5-one-adapter-per-mode-facades-strategies): a class generic over a phantom mode type, with
  self-typed members, also works. The type parameter is what the checker
  keys on.)*
- Sans-I/O and greenlet both solve implementation duplication and neither can
  solve the typing problem, because neither changes that fact.

Codegen is the exception, because it produces the two classes rather than
merging them: p4 has correct types with no façade at all, and mypy's messages
for it are identical to p2b's. For the runtime designs the answer is one core
plus two typed façades, which is what p2b is. Each façade method is a
`guard()` plus one line that drives the shared generator body, and it carries the
right signature. From [`p2_typed.py`](../../prototypes/sync_async/p2_typed.py):

```python
class SyncTask(Task, _TaskCore):
    def save(self) -> "SyncTask":
        guard()
        return _drive_sync(self._io_save(), self._transport)

class AsyncTask(Task, _TaskCore):
    def save(self) -> Coroutine[Any, Any, "AsyncTask"]:
        guard()
        return _drive_async(self._io_save(), self._transport)
```

The async façade is a plain `def` returning a coroutine, not `async def` — see
[§5](#5-result-correctness-under-composition) for why an `async def` here would recreate the bug.

There is **no logic** in a façade — no I/O, and no object construction either:
the collection façades differ only by a `_task_cls` attribute and the shared core
does the constructing. So there is nothing to keep in sync but signatures, and
[`test_every_io_body_has_both_facades`](../../prototypes/sync_async/test_conformance.py)
asserts that mechanically rather than leaving it to discipline.

The natural drift for this design is to move object construction out of the
shared bodies and into both façades, which duplicates real per-mode logic in
the one mechanism advertised as duplication-free. The completeness test above
does not catch that — only reading the façades does, so it belongs on [1.3](ROADMAP.md#13-syncasync-scaffolding)'s
review checklist.

mypy's message for Case 2 is even actionable: *"Coroutine[Any, Any, AsyncTask]
has no attribute summary. Maybe you forgot to use await?"*

This also answers the roadmap's explicit question — "can the object layer be
Sans-I/O without an ugly public API?" — with a demonstrated yes. Callers write
`task.save()` or `await task.save()`. Nothing resembling
`client.execute(task.build_save_request())` appears anywhere.

---

## 7. Result: size, and what it does not tell us

From `measure.py`, code lines only (no blanks, comments or docstrings):

| | total | fixed machinery | I/O methods | per I/O method |
|---|---|---|---|---|
| [p1](#p1) runtime dual-mode | 77 | 4 | 5 | 14.6 |
| [p2](#p2) generator Sans-I/O | 94 | 50 | 5 | 8.8 |
| [p3](#p3) greenlet | 91 | 43 | 5 | 9.6 |
| [p2b](#p2b) Sans-I/O + typed façades | 129 | 42 | 5 | 17.4 |
| [p4](#p4) unasync (hand-written only) | 40 | 3 | 5 | 7.4 |

p2b is `p2_typed.py` (91 lines: a core of its own plus the façades) and the
drivers and guard it imports from `p2_sansio.py` (38 lines).

p4 is counted by p2b's rule: module-level setup is fixed, the rest is per
method. Its 40 lines of async source become 80 shipped lines once the generated
copy is added, but only 40 are ever written or reviewed. Tooling is charged to
neither design: p2b's `check_ast.py` is 68 lines, p4's `generate.py` 33.

Extrapolating the slopes to caldav's **57** `_async_*` methods (counted with
`grep -rc "async def _async_" caldav/` on 2026-09-02): roughly **836 / 551 / 590
/ 1033 / 424** lines for p1 / p2 / p3 / p2b / p4. p4 is the smallest
hand-written, because a method is written once and the second copy is generated;
p2b is the largest, because each I/O method costs one core body *and* two façade
methods.

It is still **not** the argument for anything. Anyone choosing on line count is
choosing on the wrong axis, and the numbers move whenever the toy does: an
earlier draft of this table reported a ~25% gap because `measure.py` divided
every file by a hard-coded five methods, which charged p2 for a sixth body it
happened to contain and excluded module-level setup from "fixed". A
measurement that can drift like that should be read as an order of magnitude,
not a score. `measure.py` now counts the methods rather than
assuming them, and prints the p2b row.

The typed façades are p2b's real, visible price: about 8.6 lines per I/O method
on top of the core, which takes its per-method cost (17.4) past p1's (14.6). p4
gets the same signatures without hand-written façades, and pays instead with a
generated copy, its generator and a freshness test.

---

## 8. greenlet: not competitive

A backend error raised on **page 2 of a paginated search** — a failure partway
through a loop of I/O, which is the realistic bad case:

| | sync frames | async frames | object-layer frame visible |
|---|---|---|---|
| [p1](#p1) dual-mode | 4 | 8 | yes |
| [p2](#p2) Sans-I/O | 5 | 8 | yes |
| [p3](#p3) greenlet | 5 | **12** | yes |
| [p4](#p4) unasync | 4 | 8 | yes |

All four keep the failing object-layer line visible, which was the main risk.
The difference is that greenlet's async traceback is not merely longer, it is
**non-linear** (on Python 3.14; on 3.13 it is 9 frames with `greenlet_spawn`
once, so the length penalty is version-dependent): `greenlet_spawn` appears twice and the frames read as though
`await_()` called `greenlet_spawn()` called the transport, which is not the
causal order. Reading it requires knowing how the bridge works. Sans-I/O's
async traceback has the same frame count as plain dual-mode (8); in sync mode it
has one frame more (5 against 4), the driver.

p4's tracebacks are those of hand-written code, because that is what runs: no
driver, no bridge. The cost is *where* the sync frame points — at
`p4_codegen/_sync/tasks.py`, a file nobody wrote. `measure.py` prints that path
in full, since `_async/` and `_sync/` both have a `tasks.py`. The line number
matches the source: every `_async/` file starts with a one-line comment that the
generator replaces with its do-not-edit marker, so no line shifts. A debugger
breakpoint still has to be set in the generated file, and a fix typed there is
overwritten on the next regeneration — which the freshness test makes loud.

**Why greenlet is not competitive.** The case for it would be a rule that
*cannot be broken* rather than one enforced twice, with the C dependency and
this traceback as the price. [§5](#5-result-correctness-under-composition) removes the premise: the rule can be broken in
greenlet exactly as in dual-mode, and nothing catches it.

With that gone, what is left on greenlet's side is ergonomic rather than safety:
composition reads as ordinary blocking code, with no `yield from` and no
generator discipline for a contributor to learn. That is worth something. It is
not worth:

- a compiled C dependency in a library whose dependency set is otherwise pure
  Python,
- a 50%-longer, non-linear async traceback, and
- a bridge a future contributor must understand before they can debug anything,

**for a design that is no safer than the one whose failure modes prompted this
whole item.** greenlet also still needs [§6](#6-result-type-checking-and-a-finding-that-changes-the-shape-of-the-answer)'s typed façades, so it does not even
save that work.

---

## 9. Result: pagination and the filesystem backend

Both required by the roadmap, both covered by the shared suite for every
prototype:

- `test_search_paginates` asserts 5 tasks at page size 2 take exactly 3
  requests — the loop is genuinely driven, not faked.
- `test_backend_error_mid_pagination_propagates` fails on page 2 specifically.
- `test_filesystem_backend` runs the same object layer against a naturally
  blocking `FileStore`, with the async side pushed to a thread via
  `asyncio.to_thread`.

No candidate had difficulty with either. Every one of them, [p2b, Sans-I/O with typed façades](#p2b) and [p4, async-first with unasync](#p4)
included, passes the filesystem case because the per-mode part — a blocking
`FileStore` for sync, `asyncio.to_thread` for async — lives in hand-written
transports in `common.py`, outside the shared object layer. In p2b this is
enforced by construction: a Sans-I/O body cannot call `to_thread`, `gather` or
`sleep` at all, only yield requests to a hand-written driver. In p4 it is a
rule to keep, below.

What is specific to p4 is narrower: `unasync` is a token rewrite, so anything in
`_async/` must survive one. `test_unasync_is_a_token_rewrite` pins correct async
lines whose sync copies are wrong:

| async source | what `unasync` makes of it |
|---|---|
| `await asyncio.to_thread(path.read_text)` | `asyncio.to_thread(path.read_text)` — returns a coroutine |
| `await asyncio.gather(f(), g())` | `asyncio.gather(f(), g())` — meaningless without a loop |
| `await asyncio.sleep(backoff)` | `asyncio.sleep(backoff)` — a dropped coroutine; rate-limit backoff silently stops |
| `httpx.AsyncClient()` | `httpx.SyncClient()` — no such class |
| `await self._http.aclose()` | `self._http.aclose()` — not in unasync's table; httpx's sync method is `close` |
| `await anext(it)` | `anext(it)` |
| `-> Awaitable[int]`, `-> "list[AsyncTask]"` | unchanged — only whole-string forward references are renamed |
| `await(self.save())` | `self.save())` — a syntax error: the token after `await` is dropped blindly |

mypy over `_sync/` flags some of these (the dropped `sleep`), not all. So under
p4 the rule for contributors is: no `asyncio`, no third-party `Async*` names,
and no `await(...)` in `_async/` — anything else goes in the hand-written
per-mode layer, as httpcore keeps its backends and `_synchronization.py`
hand-written. A CI check for that rule would be short, but it has not been
written.

Two smaller consequences. `unasync` renames `AsyncTask` to `SyncTask`; if the
public sync classes should be unprefixed (`Task`, as httpcore does it), each
needs an entry in a replacement map. And comments and docstrings are not
translated: the generated `buggy.py` still talks about a missing await.

Not probed for either design: pagination exposed as an iterator rather than a
list (`async for` in p4; a generator-returning façade in p2b).

---

## 10. p2b and p4 compared

What each would require of roadmap item [1.3](ROADMAP.md#13-syncasync-scaffolding):

**[p2b, Sans-I/O with typed façades](#p2b), generator Sans-I/O core with typed façades**

1. The object layer is written once, as generator bodies (`_io_*`) that yield
   `Request`s and compose with `yield from`. Object construction stays in the
   core; a façade supplies only the classes to construct.
2. Each object gets a sync and an async façade class. A façade method is a
   `guard()` and one driver call, and carries the real signature. Async façade
   methods are plain `def` returning a coroutine, so the guard runs at the call
   site ([§5](#5-result-correctness-under-composition)).
3. Under the core sits a **hand-written** per-mode layer — the transports, and
   any thread, sleep or concurrency helper — because a body may only yield
   requests ([§9](#9-result-pagination-and-the-filesystem-backend)).
4. Enforcement, each piece with a test that it fires:
   - the runtime guard in every public entry point, exercised through a planted
     specimen in both modes (`test_misuse_is_caught_in_both_modes`,
     `test_facade_misuse_is_caught_in_both_modes`);
   - a static check built properly from `check_ast.py` (*What is not settled*,
     6), the only protection against a dropped `_io_` generator, run against
     both planted shapes;
   - a completeness test that every I/O body has both façades
     (`test_every_io_body_has_both_facades`);
   - mypy or pyright over the façades in CI, with [§6](#6-result-type-checking-and-a-finding-that-changes-the-shape-of-the-answer)'s three cases as a test
     that the checker catches a missing and a spurious `await` (not yet
     written; the probes exist).

**[p4, async-first with unasync](#p4), async-first source with a generated sync copy**

1. The object layer is written once, in `_async/`, as ordinary `async def` /
   `await` code with `Async*` class names.
2. A generator writes `_sync/`, recursively, deleting orphans; the output is
   committed, so installing runs nothing and `unasync` is a development
   dependency only.
3. Under the generated layer sits a **hand-written** per-mode layer — the
   transports, and any thread, sleep or concurrency helper — because `unasync`
   cannot translate those ([§9](#9-result-pagination-and-the-filesystem-backend)).
4. Enforcement, each piece with a test that it fires:
   - pyright, or mypy with `check_untyped_defs`, over `_async/` *and* `_sync/`
     in CI ([§5](#5-result-correctness-under-composition)); plus ruff's F841 for the assigned-and-unused shape;
   - a test that the checker flags a committed specimen
     (`test_type_checker_flags_the_codegen_slip`);
   - a freshness test comparing file sets as well as contents
     (`test_generated_sync_code_is_fresh`);
   - the shared conformance suite in both modes, under `-W error`;
   - a check that `_async/` uses no `asyncio`, no third-party `Async*` names and
     no `await(...)` ([§9](#9-result-pagination-and-the-filesystem-backend); not yet written).

For both, dev-only tools must fail the build when missing in CI, not skip.

Axis by axis:

| | p2b Sans-I/O + façades | p4 unasync |
|---|---|---|
| The caldav slip, public name ([§5](#5-result-correctness-under-composition)) | raises at runtime, both modes | common shapes flagged by stock type checkers; at runtime only a warning |
| A missing `yield from` / `await` ([§5](#5-result-correctness-under-composition)) | silent at runtime; project-written AST check, or pyright's opt-in, noisy `reportUnusedCallResult` | stock type checkers, common shapes; `RuntimeWarning`, which `-W error` turns into an async test failure |
| caldav layer (*What is not settled*, 5) | unprotected until a typed shim exists | unprotected until a typed shim exists |
| Public types ([§6](#6-result-type-checking-and-a-finding-that-changes-the-shape-of-the-answer)) | correct, via hand-written façades | correct, by construction |
| Types inside method bodies | weak: the core is typed through `Any` | checked |
| Hand-written per I/O method ([§7](#7-result-size-and-what-it-does-not-tell-us)) | 17.4 lines, in two styles | 7.4 lines, plain async |
| Enforcement to maintain | runtime guard, AST check, completeness test, type check | type-checker config and test, freshness test, `-W error`, token-rule check |
| Tracebacks ([§8](#8-greenlet-not-competitive)) | one driver frame extra in sync | as hand-written code, but in a generated file |
| What a contributor must learn | generators, `yield from`, the guard, and which name to call | async/await, the token rule, and that `_sync/` is generated |
| Non-HTTP backends ([§9](#9-result-pagination-and-the-filesystem-backend)) | bodies may only yield requests | `_async/` must survive a token rewrite |
| Build step | none | dev-time only; output committed |

**The method bodies.** A method that makes several round trips is written the
same way in both. `complete()` fetches, changes and saves:

```python
# p4, _async/tasks.py
async def complete(self) -> AsyncTask:
    fresh = await self._collection.get_task(self.uid)
    self.summary = fresh.summary
    self.status = "COMPLETED"
    await self.save()
    return self

# p2b, p2_typed.py
def _io_complete(self) -> IO[Any]:
    fresh = yield from self._coll._io_get_task(self.uid)
    self.summary = fresh.summary
    self.status = "COMPLETED"
    yield from self._io_save()
    return self
```

Calls also change name — `get_task` becomes `_io_get_task`, `save` becomes
`_io_save` — and `search()`'s pagination loop has `yield Request(...)` where [p4, async-first with unasync](#p4)
awaits the transport. Otherwise the bodies match. The differences in writing
them are elsewhere:

- **The façade layer.** [p2b, Sans-I/O with typed façades](#p2b) pays about 8.6 mechanical lines per I/O method,
  roughly 490 at caldav's scale ([§7](#7-result-size-and-what-it-does-not-tell-us)). The completeness test keeps them honest,
  but someone still writes and reviews them. p4 has none.
- **Typed bodies.** In p4, `fresh` is an `AsyncTask` and a typo in
  `fresh.summary` is a type error. In p2b, `fresh` is `Any`, because the shared
  core is typed `IO[Any]` with `Any`-typed back references; the checker sees
  the façades but not inside the bodies.
- **Familiarity.** Every Python contributor knows `async`/`await`. Generators
  that return values through `yield from`, and a rule about which name to call,
  are rarer.
- **What may appear in a body.** In p2b a body can only yield requests and
  cannot `await`, so `asyncio`, a third-party async client or a sleep is
  unusable there by construction; there is no allowed-name rule to keep. In p4
  `_async/` must avoid `asyncio`, third-party `Async*` names and `await(...)`,
  enforced by a check not yet written ([§9](#9-result-pagination-and-the-filesystem-backend)).
- **Names and generated code.** p4's `Async*` → `Sync*` rename needs a
  replacement-map entry for every name that should not follow it — unprefixed
  public classes, and every caldav name (*What is not settled*, 5). Its sync
  tracebacks and breakpoints point into a file nobody wrote ([§8](#8-greenlet-not-competitive)). p2b has
  neither.

**What would tip it.**

- Towards **p2b**: valuing a runtime check that the ordinary sync suite hits,
  for the shape that caused caldav's bugs; a preference for no generated code
  and no build step; and its open questions resolving well — if the shared core
  can be typed (*What is not settled*, 1) and the façades generated (2), its two
  main costs mostly go.
- Towards **p4**: valuing plain `async`/`await` for contributors, fully typed
  method bodies, and protection from stock, widely maintained tools rather than
  a project-written AST check; being willing to make a type checker a hard CI
  gate and to keep `_async/` within what a token rewrite can translate; and its
  open questions resolving well — `unasync` itself or a vendored equivalent
  (7), and the replacement map the caldav names need (5).

**What the reviews taught.** Every one of p2b's enforcement mechanisms was
broken in its first draft — the guard did not cover the façades, the static
check both missed the real mistake and rejected the design it protected, and
the completeness test did not exist while the document claimed it did. p4's
first draft repeated the pattern: its type-checker enforcement was printed but
not tested, and its freshness test missed orphaned and nested files and skipped
silently without `unasync`. A clean-context review found all of that, and the
fixes are in the prototypes and in the text above. The lesson holds for either
choice: whatever [1.3](ROADMAP.md#13-syncasync-scaffolding) builds, its enforcement needs tests written by someone who
did not write the enforcement.

### What is not settled

1. **Typing the core ([p2b, Sans-I/O with typed façades](#p2b)).** Typed bodies work for a single class: `p2_sansio.py`
   types its bodies as `IO["BoundTask"]`. The open question is a core shared by
   two façade classes. `p2_typed.py` types it `IO[Any]`, with `_coll: Any`, so
   `fresh` in `_io_complete` is `Any`. [1.3](ROADMAP.md#13-syncasync-scaffolding) should try making the core generic
   over the task class (or `Self`) with typed back references before accepting
   that.
2. **Generating the façades (p2b).** They are mechanical: a guard and a driver call.
   Whether a decorator or a small generator can produce them without losing
   their signatures is untested. If it can, most of [§7](#7-result-size-and-what-it-does-not-tell-us)'s size difference goes.
3. **Cancellation and timeouts** were not tested for any candidate. For p2b a
   driver cancelled mid-pagination must not leave a half-consumed generator in
   a bad state; for [p4, async-first with unasync](#p4) they are plain asyncio semantics.
4. **Threading.** Not tested. p2b's `_IN_DRIVER` is a `ContextVar`, correct
   per-task and per-thread, but untested with a driver that hands work to a
   thread pool. p4 has no per-thread state.
5. **The caldav bridge** (roadmap 2.1) is the real test. caldav's async names
   are aliases of its dual-mode classes (`AsyncTodo = Todo` in
   `caldav/aio.py`), whose methods return `Self | Coroutine`. A p4-style
   backend written against `caldav.aio` showed what that means:
   - mypy and pyright both reject the *correct* `await cal.save_todo(...)`
     (`Todo | Coroutine` is not awaitable), and both miss two planted missing
     awaits — [p1, runtime dual-mode](#p1)'s typing, false positives included;
   - `unasync` turns the imports into `SyncDAVClient`, `SyncCalendar` and
     `SyncTodo`, none of which exist, so every caldav name would need a
     replacement map entry (as does `get_async_davclient`).

   In p2b the caldav calls sit in the hand-written per-mode layer under the
   drivers, outside the bodies, where the guard does not run either. So a
   missing `await` in that layer is unprotected in **both** designs until it
   has a thin, honestly typed shim over caldav — separate sync and async
   wrappers, or a `cast` at every call. With the shim, p4's static check would
   reach it too. That work is the same whichever design 1.3 builds.
6. **`check_ast.py` is a prototype, not the gate 1.3 needs (p2b).** The suite runs
   it (`test_static_check_passes_the_real_modules_and_catches_the_specimen`),
   but a review found three holes, each confirmed with a probe: it only knows
   public names whose `_io_*` body is in the same file, so a call into another
   module's object passes; it flags any attribute of the same name, such as
   `re.search(...)` in a file that defines `_io_search`; and it exempts
   `g = self.save(); yield from g` as delegation. 1.3's check must match on
   `self` and known receivers, resolve names across modules or from a registry,
   and not exempt an assignment of a public call.
7. **`unasync` itself (p4).** It is a token rewrite that imports `setuptools`;
   httpcore uses a short script of its own instead. 1.3 would decide between
   depending on it and vendoring an equivalent — either works with the
   committed-output arrangement.

---

## 11. Decision

This section is written by the human, trying to sum up my understanding of the whole thing above, possibly weighting feelings more than facts.

For me, code generation makes sense when it goes from a higher level language to a lower level language.  I don't have problems with Gen-AI turning a simple paragraph in human language to 800 lines of Python code, I don't have problems with the compiler turning C code into a binary, etc.  When there is a need for Python code to duplicate Python code, it's a strong hint on shortcomings with the programming language.  From this perspective it feels more right to "play" with [p2b, Sans-I/O with typed façades](#p2b) than to go for [p4, async-first with unasync](#p4).  I do find it difficult to wrap my head around the generator design, but I also like challenges - I'd learn more from the p2b-approach than the p4-approach.  p2b feels "intellectually right".

Then again, looking into the "p2b"-code, it's butt ugly.  I also have the feeling that it can be hard to debug and hard for other human contributors to get in and understand things.

Async code is also harder to understand than sync code, and a lot harder to debug - but at least it's a "trodden path".  With unasync, the sync code will be generated - but still relatively easy to navigate.  The comments in the async "source of truth" are preserved in the generated copy as far as I can see.

So finally I've decided to go for unasync, for the better (though not perfect) aesthetics and debuggability.

I think it's a sorry state both that one has to spend so much efforts at coming to a design decision, and that it's a practice of picking the "least worst" architecture.

---

## 12. Revisited: the adapter pattern (2026-10-10)

The [PR 5 review](https://github.com/pycalendar/calendaring/pull/5#discussion_r4237462075)
proposed "adapters" over bound subclasses: one item object with
`item.sync.save()` and `await item.async.save()`, plus write strategies
(write now, gather and flush, background). The author asked whether the
adapter pattern, said to be used by SQLAlchemy, httpx and psycopg 3, changes
the conclusion.

### 12.1 What those libraries do

Read from their source (shallow clones, 2026-10-10: SQLAlchemy `9006469`,
httpx `b5addb6`, httpcore `10a6582`, psycopg `19e148a`):

| | Data objects | I/O classes | How the second mode is made | "Adapter" in the code |
|---|---|---|---|---|
| SQLAlchemy 2 | ORM instances, mode-free; dirty tracking by attribute instrumentation, flushed by the `Session` | `Session`; `AsyncSession` is a proxy holding `sync_session` | greenlet: `AsyncSession` runs the sync core inside `greenlet_spawn`, and `await_()` suspends it on the driver's coroutine | `AsyncAdapt_dbapi_connection` and friends make async *drivers* look like a sync DBAPI. That is [p3, greenlet bridging](#p3) |
| httpx | `Request` / `Response`, mode-free; `Response` has `read()` *and* `aread()`, and raises `RuntimeError` on the wrong one | `Client` / `AsyncClient` on a shared `BaseClient` | hand-written twice; httpcore below it generates sync with an unasync script | none |
| psycopg 3 | rows, mode-free | `Connection` / `AsyncConnection`, `Cursor` / `AsyncCursor` | `tools/async_to_sync.py`, an AST rewrite of `*_async.py`, output committed. That is [p4, async-first with unasync](#p4) | `psycopg.adapt` is Python ↔ PostgreSQL *type* conversion, unrelated to sync/async |

None of them has a mechanism not already tested: SQLAlchemy is p3, psycopg
and httpcore are p4, and httpx is "separate classes" ([§3](#3-the-candidates)). What they share
is an API shape: mode-free data and an I/O object per mode. A1 in the
[API spec](API_DESIGN.md#11-sync-and-async) already has that shape. The
one thing the comparison did not test is the review's variant: a single item class,
reached through per-mode facades, with the mode held by a strategy. That
is [p5, adapter with per-mode façades](#p5).

### 12.2 p5: one adapter, per-mode facades, strategies

[`p5_adapter/`](../../prototypes/sync_async/p5_adapter/). The I/O is written
once in `_async/ops.py` and generated by [p4, async-first with unasync](#p4)'s generator, so [p5, adapter with per-mode façades](#p5) is an API
layer *on top of* p4:

```python
item.summary = "x"          # mode-free; the setter borrows the data, marks it dirty
item.sync.save()            # sync facade (generated)
await item.aio.save()       # async facade; `item.async` is a SyntaxError
item.save()                 # bare: the item's strategy decides
                            #   Immediate, sync   writes now (caldav's task.save())
                            #   Immediate, async  raises ModeError, before any coroutine exists
                            #   Gather            queues; gather.flush() / await gather.flush()
```

Results (`test_conformance.py`, `test_p5_adapter.py`, 141 passed):

- **The shared suite** passes in both modes.
- **Duplication** is the same as p4: one hand-written copy of every I/O method.
  The adapter and the `Immediate` strategy are mode-free and hand-written
  once. `Gather` does I/O in `flush()`, so it lives in `_async/ops.py` and
  is generated per mode. `Autosync` is hand-written once and works in sync
  mode only.
- **The composition slip.** If an async op calls the item's bare `save()`,
  it raises `ModeError`, which is better than p4. If it calls the facade's
  `self.save()` without `await`, that is p4's slip unchanged, with p4's
  answer: the type checker.
- **Typing.** `TaskAdapter` is generic over a phantom mode type
  (`SyncMode`, `AsyncMode`, `GatherMode`), and `.sync`, `.aio` and the bare
  methods carry a self-type. mypy and pyright both accept the correct uses
  and both reject the wrong facade, the bare call in async mode and the
  missing `await` ([`probe_p5_adapter.py`](../../prototypes/sync_async/typing_probe/probe_p5_adapter.py),
  pinned for mypy by `test_type_checker_sees_the_mode`; pyright checked by
  hand). **This corrects [§6](#6-result-type-checking-and-a-finding-that-changes-the-shape-of-the-answer)**:
  "a type checker can only key on the class" holds only for an unparameterised
  class. The limit is that a helper annotated `TaskAdapter[Any]` turns the
  check off, while `TaskAdapter[object]` keeps it. The runtime `ModeError`
  is what catches the first.
- **Strategies.** Under `Autosync` (the review's mode 1), three setters cost
  three round trips. Under `Gather` (mode 2), four modifications of two items
  cost two round trips, in either mode, because queueing is not I/O. The
  background syncer (mode 3) needs `asyncio.create_task`, which unasync
  cannot translate, so it would be hand-written per mode. It was not
  prototyped.
- **The leak.** A write that bypasses the setters is not tracked, and a
  gathered flush drops it (`test_a_write_that_bypasses_the_setters_is_not_tracked`).
  [A2](API_DESIGN.md#31-the-base-a-typed-view-over-icalendar) makes
  `item.icalendar` public and the storage, so this is the normal case, not
  an edge case. Reliable tracking needs change notification in `icalendar`
  itself.
- **Size**, in code lines: p4's `_async/tasks.py` is 40. p5's
  `_async/ops.py` is 66 (gather included) and `adapter.py` is 87 (a property
  pair per field, borrowing, modes, strategies). The property pairs
  would be new cost in the real library: A2 has the item expose
  `icalendar`'s own properties through `item.component` instead of
  defining any of its own, so tracking changes there would need change
  notification in `icalendar` itself.

### 12.3 What this changes

- **The decision stands.** The adapter pattern answers a different
  question (the shape of the public API), and [p5, adapter with per-mode façades](#p5) runs on [p4, async-first with unasync](#p4)'s generator, extended only to take
  a source root (`src_root`). For [1.3](ROADMAP.md#13-syncasync-scaffolding) that is the only change: the generator
  handles more than one package.
- **A1 ([1.1](ROADMAP.md#11-unified-api-design-and-peer-review)).** Bound subclasses and an adapter with facades come out
  equal on duplication, on static safety (apart from the `Any` hole) and
  on caldav compatibility in sync mode. The differences:
  - async callers write `await task.aio.save()` under p5, against
    `await task.save()` under A1;
  - p5 has one item class to document;
  - a p5 item can be re-pointed at another strategy (`gather.track(item)`).

  The strategies themselves are orthogonal: A1's bound item delegates to its
  collection, and a collection can hold a strategy just as well. Nothing here
  calls for reopening A1. Two things carry over into it whichever shape it
  keeps: the call-site `ModeError`, and the phantom-type technique where a
  class has to serve more than one mode.
- **Change tracking and write strategies** are a feature, not an
  architecture, and the leak blocks them until `icalendar` can notify. They
  go to the API spec's open questions.

---

*Drafted with AI assistance (Claude Opus 5 and 5.5 via Claude Code) and revised
after clean-context reviews. Every number and every fenced output block
is reproducible from `prototypes/sync_async/`; the prototypes are the
deliverable as much as this document is.*
