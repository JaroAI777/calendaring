# Survey: how real systems model tasks

**Roadmap item:** [0.1 Task and issue-tracker data model](ROADMAP.md#01-task-and-issue-tracker-data-model)
**Status:** complete in draft
**Deliverable:** this analysis, plus a proposed task data model (Part 4) and a
recommendation for which tracker to implement first (Part 5)

---

## How to read this document

The README's core complaint is that no good open standard for tasks exists, and
that iCalendar in particular has "no clear way to add information about time
estimates and time spent on a task". This document tests that complaint against
what the standards actually say today and against what eleven real systems
actually do, and then proposes a model.

**On sourcing.** Every factual claim about a system is meant to be traceable to a
primary source — a specification, a schema, an API reference, or the source code —
and the source is linked at the point of the claim. Where something could not be
verified from a primary source it is marked **[unverified]** rather than smoothed
over. This document was drafted with AI assistance; the marking discipline exists
so that the author can check the claims rather than having to trust them.

**On scope.** This is research plus a model proposal. Actual implementation is outside the scope.  Twenty hours across eleven systems is roughly 1.5 h each plus write-up: enough for an informed survey, not for deep expertise in any one of them.

### Coverage

| Part | Subject | State |
|---|---|---|
| 1 | The standards baseline | drafted |
| 2 | The eleven systems | drafted — all eleven |
| 3 | Cross-cutting comparison | drafted |
| 4 | Proposed model | drafted
| 5 | Which tracker to implement first | Gitea |

### The dimensions each system is surveyed against

Taken from the roadmap item, and used as the section structure for every system
in Part 2, so that the systems can be compared row by row in Part 3.

1. **Identity** — stable client-assignable ID, or server-assigned integer?
2. **Time estimate and time spent** — the gap the README names explicitly
3. **Status lifecycle** — fixed enum, or user-definable per project/board?
4. **Priority** — numeric, ordinal, or a label?
5. **Dates** — due, start, scheduled, deferred, and which exist at all
6. **Recurrence** — supported, faked, or absent
7. **Dependencies and parent/child relations**
8. **Assignment, ownership, and multi-user semantics**
9. **Labels/tags/categories**, and whether they are free-form
10. **Change detection** — ETag, revision counter, `updated` timestamp, or nothing
11. **Server-side search and filter**
12. **Auth model, pagination, rate limiting** — a real implementation cost

---

## Part 1: The standards baseline

### 1.1 RFC 5545 alone

Plain [RFC 5545](https://datatracker.ietf.org/doc/html/rfc5545) `VTODO` gives a
task: `UID`, `SUMMARY`, `DESCRIPTION`, `DTSTART`, `DUE` **or** `DURATION` (never
both), `COMPLETED`, `PERCENT-COMPLETE`, `PRIORITY` (0–9, 1 highest), `STATUS`
(`NEEDS-ACTION`, `IN-PROCESS`, `COMPLETED`, `CANCELLED`), `CATEGORIES`,
`RELATED-TO` with `RELTYPE` of `PARENT`/`CHILD`/`SIBLING`, `RRULE`, and the
`DTSTAMP`/`CREATED`/`LAST-MODIFIED` timestamps.

The author's own analysis of what this does and does not permit is in plann's
[`TASK_MANAGEMENT.md`](https://github.com/pycalendar/plann/blob/master/TASK_MANAGEMENT.md)
and [`NEXT_LEVEL.md`](https://github.com/pycalendar/plann/blob/master/NEXT_LEVEL.md),
and is not repeated here. The short version of its conclusion: of seven things one
wants to record about a task, RFC 5545 has room for three, so `DURATION` gets
pressed into service as the time estimate and `DTSTART` is redefined as "the
latest you can start and still finish by `DUE`, given that this task gets full priority".

**That convention is now in conflict with an emerging standard.** See 1.2.

#### What do `DTSTART`, `DUE` and `DURATION` mean on a task?

RFC 5545 does not define them well. The only thing beyond doubt is that, given
`DTSTART`, `DURATION` is an alternative to `DUE`: an implicit
`DUE = DTSTART + DURATION`, or an implicit `DURATION = DUE - DTSTART`. That still
leaves several readings, each with its own problem:

| `DTSTART` | `DUE` | `DURATION` | Problem |
|---|---|---|---|
| Earliest time it makes sense to start | Deadline | The window in which to consider working on it | `DTSTART` is vague, and may have to move earlier depending on how many other tasks are on hand |
| Planned start | Deadline | Planned start to deadline — not the time available, as other events, tasks and non-calendar things (bedtime) compete for it | `DURATION` means little |
| Actual start | Deadline | Actual start to deadline; negative for overdue work | Breaks recurring tasks |
| Latest possible start, considering competing tasks | Deadline | Estimate plus slack plus competing tasks' estimates | `DURATION` means little; `DTSTART` is a computed value depending on the rest of the calendar |
| Latest possible start, were this the only thing on the calendar (plann) | Deadline | The time estimate | `DTSTART` is destroyed as a meaningful value |
| Start of a time slot reserved for the task | End of that time slot | The time estimate | The deadline is lost, and the timestamps need moving whenever the slot is spent on more important things |

`DUE` can only be one of two things: the deadline (hard, soft, or wishful thinking),
or the end of a time slot reserved for the task — and in the latter case `DTSTART`
can only be the planned start. RFC 5545 defines `DUE` as "the date and time that a
to-do is expected to be completed", which can be read either way. This survey, and
plann's design notes, read it as the deadline; plann pins a task to the calendar
with a separate `VEVENT` linked through `RELATED-TO` instead of moving the task's
own timestamps.

The tasks draft (1.2) settles on the first reading. That was also plann's original
idea for `DTSTART`; it was abandoned only because the estimate needed somewhere to
live, which `ESTIMATED-DURATION` now provides.

### 1.2 The task extensions draft — the estimate gap is closing

[`draft-ietf-calext-ical-tasks-17`](https://datatracker.ietf.org/doc/draft-ietf-calext-ical-tasks/)
("Task Extensions to iCalendar", Apthorp & Douglass, 10 December 2025) is a draft intended to become a Proposed Standard updating RFC 5545.  It is at the RFC Editor, so it seems very likely that it will be published as an RFC within months.  It focuses mostly on workflows involving multiple users.  It adds granularity to task statuses, and it introduces a new property `ESTIMATED-DURATION`, which closes the estimate half of the README's gap.

`ESTIMATED-DURATION` is the estimate, and `DTSTART` - `DUE` is the *window* a task may be performed in.  The model in plann is to use `DURATION` as the estimate, and derive `DTSTART` from it.

It seems pretty obvious that we should support the tasks draft - time estimates should be embedded in `ESTIMATED-DURATION`, and `DURATION` should be the window for performing the task. This means icalendar task data created through plann has to be rewritten to support the tasks draft.

### 1.3 The time-spent gap is **not** closing

Neither the tasks draft nor JSCalendar defines anything for time actually spent.  There is also no de-facto convention to be compatible with.  Every known client that tracks time spent keeps it to itself, it's not exported to iCalendar format or to other systems.  There are no known systems using X-properties for storing this information.

A quick check of a handful of clients on 2026-09-24:

| Client | What it writes for time | Evidence |
|---|---|---|
| Emacs org-mode (`ox-icalendar.el`, Emacs master) | **nothing** — clock lines are dropped (`:translate-alist '((clock . nil) …`) and `Effort` is never exported; its only `X-` properties are `X-WR-CALNAME`, `X-WR-CALDESC`, `X-WR-TIMEZONE` and `X-PUBLISHED-TTL` | exporter source |
| Taskwarrior | **nothing** — core has no iCalendar import or export at all; time lives in UDAs and any bridge is third-party | no `VTODO`/iCalendar code in the source tree |
| Tasks.org (Android) | **nothing** — it has an estimate and a timer in its own model, but its CalDAV mapping writes neither; its `X-` properties are `X-APPLE-SORT-ORDER`, `X-OC-HIDESUBTASKS`, `X-MOZ-SNOOZE-TIME`, `X-MOZ-LASTACK` | `caldav/iCalendar.kt` |
| Nextcloud Tasks | **nothing** — `X-APPLE-SORT-ORDER`, `X-OC-HIDESUBTASKS`, `X-OC-HIDECOMPLETEDSUBTASKS`, `X-PINNED`, none time-related | `src/models/task.js` |
| jtx Board | **nothing** found | no estimate or time-tracking code in its Kotlin sources |
| plann | **no `X-` property** — `DURATION` as the estimate (see 1.2); `X-ATTENDED` exists only as the design note below | source and `NEXT_LEVEL.md` |

Thunderbird, Evolution, Apple Reminders and Outlook were not checked.

The author's three candidate workarounds, from plann's
[`NEXT_LEVEL.md`](https://github.com/pycalendar/plann/blob/master/NEXT_LEVEL.md),
with his own assessment:

1. `DTEND`/`DURATION` on `VJOURNAL` — "probably a no-go", legacy validators will reject it
2. A new `PARTSTAT` value, `X-ATTENDED` — his current preferred workaround
3. A new component, `VTIMESPENT`

Note that candidate 2 gets more complicated when considering the tasks draft; it adds `PARTSTAT=FAILED` and a whole `VSTATUS` component in the same area of the spec.

### 1.4 RFC 9253 — relationships are solved

[RFC 9253](https://datatracker.ietf.org/doc/html/rfc9253) ("Support for iCalendar
Relationships", published) is more capable than the `PARENT`/`CHILD`/`SIBLING`
that plann's design notes assume, and the notes say so themselves ("When writing
this, I was not aware of RFC9253").

It adds `RELTYPE` values `FINISHTOSTART`, `FINISHTOFINISH`, `STARTTOFINISH`,
`STARTTOSTART` (the four classical project-management dependency types), plus
`FIRST`, `NEXT`, `DEPENDS-ON`, `REFID` and `CONCEPT`; a `GAP` parameter expressing
lead and lag time between two related components; and three new properties —
`LINK` (a typed reference to external information, with a `LINKREL` parameter),
`CONCEPT` (formal, URI-valued categorisation as against `CATEGORIES`' informal
tagging) and `REFID` (a grouping key with no implied semantics).

`PARENT`/`CHILD`, `DEPENDS-ON` and the four finish-to-start family cover the
hierarchy and dependency relations Gitea, GitLab and Vikunja express, with room
left over. For those, mapping *into* iCalendar is lossless rather than lossy — one
of the few places where that is true. It is not a superset of everything:
duplicate and copy relations (Vikunja's `duplicateof`, `copiedfrom` and their
inverses) and plain references (RT's `RefersTo`) have no `RELTYPE` and must go
through `LINK` or an `X-` value. `LINK` also gives a clean way to keep a pointer
back to the originating tracker issue.

### 1.5 JSCalendar (RFC 8984) — the other lineage

[RFC 8984](https://www.rfc-editor.org/rfc/rfc8984.txt) matters because
`calendaring-jmap` will speak it, so the unified model must map to it as well as
to iCalendar.

Its `Task` object has those properties:

- **`estimatedDuration`** (`Duration`) — the estimate, as a first-class field, no
  overloading of the window (§5.2.3) - mirrors the `ESTIMATED-DURATION` from the tasks draft
- **`percentComplete`** (`UnsignedInt`, 0–100) - as in RFC 5545
- **`progress`** (`String`) — `needs-action`, `in-process`, `completed`, `failed`,
  `cancelled`, the tasks draft's values minus `PENDING`, but also extensible via the IANA "JSCalendar Enum Values" registry or a
  vendor prefix (§5.2.5).
- **`progressUpdated`** (`UTCDateTime`) — when `progress` was last set (§5.2.6)
- **per-participant `progress` and `progressUpdated`**, with the task-level
  `progress` *derived* from the participants' when not set explicitly: `completed`
  if all participants are, else `failed` if any is, else `in-process` if any is,
  else `needs-action` (§5.2.5)
- **`priority`** — `Int` 0–9, 1 highest, as in RFC 5545
- **`relatedTo`** — `Relation` objects keyed by UID, but only `first`, `next`,
  `child`, `parent`; **RFC 9253's dependency types have no JSCalendar equivalent**
- **`uid`**, **`updated`**, **`sequence`** for identity and change detection

### 1.6 Differences

| Capability | RFC 5545 | + tasks draft | RFC 9253 | JSCalendar |
|---|---|---|---|---|
| Time estimate | overloaded onto `DURATION` | **`ESTIMATED-DURATION`** | — | **`estimatedDuration`** |
| Status | 4 values | 6 values + `VSTATUS` | — | 5 values, extensible |
| Per-participant status | `PARTSTAT` | `PARTSTAT` + `FAILED` | — | per-participant `progress` |
| Parent/child | `RELTYPE` | — | `RELTYPE` | `relatedTo` |
| Dependencies | — | — | **`DEPENDS-ON`, F2S family, `GAP`** | **absent** |
| External links | `URL`, `ATTACH` | — | **`LINK` + `LINKREL`** | `links` |
| Formal categories | `CATEGORIES` | — | **`CONCEPT`** | `categories` / `keywords` |

---

## Part 2: The systems

Each system opens with **What it is** and closes with an **Assessment**. In
between, systems with a task model of their own get the same twelve headings, so
Part 3 can compare them row by row; EteSync, Focalboard and the plain-text
formats are described more briefly because they have none. Where a dimension does not exist in a system, that absence is itself the finding
and is recorded as **absent** rather than omitted.

### 2.1 Gitea

**What it is:** Web-UI exposing git-repositories and adding issue tracking and collaboration functionality.  An issue is a task.

Source: [`modules/structs/issue.go`](https://github.com/go-gitea/gitea/blob/main/modules/structs/issue.go),
[`issue_tracked_time.go`](https://github.com/go-gitea/gitea/blob/main/modules/structs/issue_tracked_time.go),
[`issue_stopwatch.go`](https://github.com/go-gitea/gitea/blob/main/modules/structs/issue_stopwatch.go)
— read from the Go structs that generate the Swagger spec, not from prose docs.

- **Identity.** Two server-assigned integers: `id` (global) and `index`, serialised
  as `number` (per-repository). Neither is client-assignable. There is no field
  anywhere for a foreign UID, so a `VTODO` synced into Gitea cannot carry its
  iCalendar `UID` except inside the body text or as a label.
- **Time estimate and time spent.** Both present, and modelled differently from
  each other. The estimate is a scalar on the issue: `TimeEstimate int64`
  (`time_estimate`, seconds). Spent time is a **log**: `TrackedTime{id, created,
  time (seconds), user_name, issue_id}`, one row per logging event, plus a
  separate running `StopWatch{created, seconds, issue_index}`. Note the shape:
  an entry is *a duration stamped with a creation time and a user* — **not** an
  interval. There is no end time, so "I worked 2h" and "I worked 14:00–16:00" are
  the same record. `AddTimeOption` accepts a `created` timestamp and a `user_name`,
  so entries can be backdated and logged on another user's behalf.
- **Status lifecycle.** A fixed two-value enum, `StateType`: `open` or `closed`.
  That is the whole lifecycle. There is no in-progress, no cancelled, no failed;
  any richer workflow is expressed with labels or a project board column.
- **Priority.** **Absent.** There is no priority field on the issue struct. Gitea
  users express priority with labels by convention.
- **Dates.** One: `Deadline *time.Time`, serialised as `due_date`. No start date,
  no scheduled date, no deferral. Plus the audit timestamps `created_at`,
  `updated_at`, `closed_at`.
- **Recurrence.** **Absent.**
- **Dependencies and parent/child.** Dependencies yes, hierarchy no. The API has
  issue dependencies (`/dependencies`) and blocking (`/blocks`), so a
  blocks/blocked-by graph exists, but there is no parent/child nesting of issues.
  Milestones and projects group issues but are not issue-to-issue relations.
- **Assignment.** Multiple `assignees` (with a deprecated singular `assignee`),
  plus `poster` as creator. Multi-user throughout.
- **Labels.** Free-form per repository, with colour; `Label` objects, referenced by
  id on create. Also `milestone` (one) and `projects` (many).
- **Change detection.** Better than expected: `ContentVersion int`, documented as
  "The version of the issue content for optimistic locking", and `EditIssueOption`
  takes a matching `content_version` "to detect conflicts during editing". That is
  a usable ETag surrogate for the body. `updated_at` covers the rest.
- **Search and filter.** Server-side filtering by state, labels, milestone,
  assignee, and full-text `q`; plus a cross-repo issue search.
- **Auth, pagination, rate limiting.** Token or basic auth; `page`/`limit` paging.
  Rate limiting is deployment-dependent (Gitea is self-hosted and ships no default
  API rate limit) **[unverified]**.

**Assessment:** Gitea is the closest fit of the trackers surveyed to what roadmap item 1.6 needs:
it has *both* an estimate and a per-entry spent-time log on one REST API, and it
is self-hostable in a small container for the conformance suite. GitLab and
OpenProject have both quantities too, but GitLab reaches per-entry timelogs only
through GraphQL, and OpenProject is a much larger system to run in CI.
Gitea's poverty elsewhere — two status values, no priority, one date — is
arguably a *feature* for 2.5, because a backend that can express almost
nothing is the honest test of whether the capability declaration works. See
Part 5.

### 2.2 GitLab

**What it is:** Web-UI exposing git-repositories and adding issue tracking and collaboration functionality.  An issue is a task.  Not fully "FLOSS" - the full task data model is not available through the "Free tier".

Source: [GitLab issues API](https://docs.gitlab.com/api/issues/).

- **Identity.** `id` (global) and `iid` (project-scoped), both server-assigned.
  No client-assignable identifier.
- **Time estimate and time spent.** Both, in seconds, but **spent time is exposed
  as an accumulated scalar**: `time_estimate` and `total_time_spent`, with
  `human_time_estimate` and `human_total_time_spent` as pre-formatted strings. The
  underlying per-entry timelogs exist but are reached through separate endpoints
  (`add_spent_time`, and timelogs in GraphQL) rather than being part of the issue
  object. So the *default* read of a GitLab issue gives you a total and loses the
  log.
- **Status lifecycle.** Two values, `opened` and `closed`. As with Gitea, richer
  workflow lives in labels (GitLab's "scoped labels", `workflow::in-progress`) or
  board lists.
- **Priority.** **Absent as such.** There is `weight` (a non-negative integer,
  Premium/Ultimate), `health_status` (`on_track`, `needs_attention`, `at_risk`,
  Ultimate) and `severity` for incidents — none of them a priority in the
  iCalendar sense, and the useful ones are behind paid tiers.
- **Dates.** `due_date` (`YYYY-MM-DD`, date only — no time of day) and, per the
  current docs, `start_date`, marked as introduced in GitLab 19.1. Plus
  `created_at`, `updated_at`, `closed_at`.
- **Recurrence.** **Absent.**
- **Dependencies and parent/child.** Linked issues with a `link_type` of
  `relates_to`, `blocks` or `is_blocked_by` (blocking link types are Premium).
  Hierarchy is `epic` (Premium/Ultimate), and in newer versions work-item parent/
  child. A large part of GitLab's relational model is not available on a free
  instance, which matters for a conformance suite that has to run somewhere.
- **Assignment.** Multiple `assignees` (multiple assignees is Premium; free allows
  one) **[unverified]**, plus `author` and `closed_by`.
- **Labels.** Free-form strings, returned as a plain array of names. Scoped labels
  (`key::value`) are a Premium convention layered on the same free-form field.
- **Change detection.** `updated_at` only. No ETag, no revision counter, no
  optimistic-locking token on the issue object.
- **Search and filter.** Extensive: state, labels, milestone, assignee, author,
  `search` over title/description, `created_after`/`updated_after`, and more.
- **Auth, pagination, rate limiting.** Personal/project/group access tokens or
  OAuth; offset and keyset pagination. gitlab.com enforces documented request rate
  limits; self-managed instances configure their own.

**Assessment:** GitLab's tiering is a practical problem for this project; several of the dimensions the survey cares about (blocking links, epics, multiple assignees, health status) cannot be exercised using the open-source version of GitLab.

### 2.3 Vikunja

**What it is:** Task/project management with Web-UI, API and smartphone app.

Source: the instance's own OpenAPI documents —
[v1 `docs.json`](https://try.vikunja.io/api/v1/docs.json) and
[v2 `openapi.json`](https://try.vikunja.io/api/v2/openapi.json), read from a live
2.6.0 instance.

- **Identity.** `id` (integer, global), `index` (per-project integer) and
  `identifier` (a string built from project identifier plus index, e.g. `PROJ-12`).
  All server-assigned.
- **Time estimate and time spent.** **No estimate field at all.** Spent time,
  however, is the richest model in the survey: a first-class `TimeEntry` resource
  with `start_time`, `end_time` (nullable — "Null means a live timer is still
  running"), `comment`, `user_id`, and attachment to *either* a task or a project
  (`Exactly one of task_id / project_id`). Endpoints: `GET/POST /time-entries`,
  `GET /tasks/{id}/time-entries`, `GET /projects/{id}/time-entries`,
  `POST /time-entries/timer/stop`, and full CRUD on `/time-entries/{id}`. The task
  itself carries only `time_entries_count`, available via `expand`.
- **Status lifecycle.** A boolean, `done`, plus `done_at`. The *visible* workflow
  is board buckets (`bucket_id`, `buckets`), which are user-definable per view.
  So Vikunja is the clearest example of the split between a fixed binary
  completion flag and a user-definable board column.
- **Priority.** `priority` integer, and the spec's own description is
  "**Can be anything you want**, it is possible to sort by this later" — an
  unbounded integer with no defined scale, in contrast to iCalendar's fixed 0–9.
- **Dates.** The richest set surveyed: `due_date`, `start_date`, `end_date`,
  `done_at`, `created`, `updated`, `deleted_at` (soft delete, 30 days), plus
  `reminders` as a separate array.
- **Recurrence.** Present and unusual: `repeat_after` (an amount **in seconds**)
  with `repeat_mode` ∈ {0, 1, 2}, and the spec's own wording is `0` = "after
  `repeat_after` seconds", `1` = "monthly (ignores `repeat_after`)", `2` = "from
  the current date rather than the last set date". So mode 0 measures from the
  last set date and mode 2 from completion — which is exactly the interval-versus-
  fixed distinction plann's `TASK_MANAGEMENT.md` draws, and mode 2 is org-mode's
  `.+` cookie. Second-based intervals still cannot express "the last Friday of
  the month": there is no `BY*` equivalent.
- **Dependencies and parent/child.** The richest relation vocabulary surveyed.
  `RelationKind` ∈ `unknown`, `subtask`, `parenttask`, `related`, `duplicateof`,
  `duplicates`, `blocking`, `blocked`, `precedes`, `follows`, `copiedfrom`,
  `copiedto`. That covers hierarchy, dependency *and* sequencing — a near-exact
  match for what RFC 9253 expresses.
- **Assignment.** `assignees` array; `created_by`.
- **Labels.** Free-form `labels` array (read-only on the task; managed through a
  separate endpoint), plus `hex_color` on the task itself.
- **Change detection.** `updated` timestamp only.
- **Search and filter.** A filter query language over task fields, plus
  `expand` for related collections.
- **Auth, pagination, rate limiting.** API tokens (`Authorization: Bearer`) or
  JWT from `/login`; Vikunja Cloud requires tokens. Paginated collections
  (`PaginatedTimeEntry` and friends in v2). Two API generations coexist: v1 is
  deprecated in 3.0 and removed in 4.0, v2 is OpenAPI 3.1 — a backend written
  today should target v2.

**Assessment:** Vikunja has the richest time-spent model surveyed, but lacks time estimates.

### 2.4 Kanboard

**What it is:** Task management web-ui with a kanban board.

Source: [Kanboard API — task procedures](https://docs.kanboard.org/v1/api/task_procedures/).

- **Identity.** `id`, server-assigned integer. A `reference` string field exists
  for "external ticket identifier" — the only field in any surveyed tracker
  explicitly intended to hold a foreign key, which makes it the natural home for
  an iCalendar `UID`.
- **Time estimate and time spent.** Both, as plain scalars on the task:
  `time_estimated` and `time_spent`, in hours.
- **Status lifecycle.** `is_active` (1/0) as the completion flag, with the real
  workflow in `column_id` — board columns, which are **user-definable per project**.
  Kanboard is the canonical "status is whatever the board says" case.
- **Priority.** `priority`, an integer, with the range configured per project
  **[unverified]**. Also `score` for story points.
- **Dates.** `date_due`, `date_started`, `date_completed`, `date_creation`,
  `date_modification` — a genuine start *and* due, unlike the forge trackers.
- **Recurrence.** Present, and expressed as a small state machine:
  `recurrence_status`, `recurrence_trigger`, `recurrence_factor`,
  `recurrence_timeframe`, `recurrence_basedate`. Trigger-based rather than
  calendar-rule-based — closer to Vikunja's `repeat_after` than to `RRULE`.
- **Dependencies and parent/child.** Subtasks are a separate resource with their
  own time fields **[unverified]**; task-to-task links exist as a separate link
  API with named link types **[unverified]**.
- **Assignment.** `owner_id` — a **single** assignee — plus `creator_id`.
- **Labels.** `tags` (array of strings) and `category_id` (one category).
- **Change detection.** `date_modification` only.
- **Search and filter.** A query language over task fields.
- **Auth, pagination, rate limiting.** JSON-RPC over HTTP with basic auth using an
  API token; no pagination in the classic task procedures **[unverified]**.

**Assessment:** Kanboard has both an estimate and time spent, but only as
overwritable scalars — no log, so it can store a total but not the entries
behind it. It is the only tracker with a field meant for a foreign key
(`reference`), which makes it the cheapest tracker to round-trip an iCalendar
`UID` through. Status is whatever the board's columns say, so it would mainly
test the native-status passthrough.

### 2.5 Taskwarrior

**What it is:** Task management CLI.

Sources: [`task.1`](https://github.com/GothenburgBitFactory/taskwarrior/blob/develop/doc/man/task.1.in)
and the [TaskChampion task model](https://gothenburgbitfactory.org/taskchampion/tasks.html).
Note that Taskwarrior 3.x replaced its own storage with TaskChampion, so the data
model is now TaskChampion's key-value model with Taskwarrior conventions on top.

- **Identity.** **`uuid` — the only client-assignable stable identifier of any
  *tracker* surveyed.** (Not of the survey as a whole: EteSync carries an
  iCalendar `UID`, `org-id` mints one, and Obsidian Tasks has `🆔` — all three
  are client-side too, and all three are file formats rather than trackers.)
  Taskwarrior is local-first: tasks are created offline
  with no server involved, so the client necessarily mints the UUID, and sync
  reconciles replicas afterwards. The man page says plainly that "Tasks are
  identified by their UUID"; the small integer `id` is a display convenience that
  is reassigned as the pending list changes and must never be stored.
- **Time estimate and time spent.** **Neither, natively.** There is `start` ("the
  most recent time at which this task was started") and `end`, which gives at most
  one open interval and no history — starting and stopping repeatedly does not
  accumulate. In practice users add an `estimate` UDA and delegate real time
  tracking to Timewarrior via the `on-modify` hook. This is the survey's clearest
  case of a system that punted.
- **Status lifecycle.** `pending` (default), `completed`, `deleted`, `recurring`.
  Fixed, and note that `recurring` is a *status*, not a flag — the recurring
  template is itself a task.
- **Priority.** `H`, `M`, `L`, or unset — an ordinal *label*, not a number. The
  values are themselves a UDA (`uda.priority.values`) and so are redefinable.
- **Dates.** The richest of any system surveyed, and the only one that
  distinguishes all the senses plann's design notes ask for: `entry` (created),
  `start`, `end`, `due`, `scheduled` (when work should begin), `wait` (hide until),
  `until` (expiry), `modified`.
- **Recurrence.** `recur` with `parent`/`imask`/`rtype`/`mask`; `rtype` chooses
  between periodic and chained recurrence — the same "fixed-time versus interval"
  distinction plann's `TASK_MANAGEMENT.md` draws, made explicit in the data model.
  TaskChampion itself "does not implement recurrence directly"; it is a
  Taskwarrior-level convention over TaskChampion keys.
- **Dependencies and parent/child.** `depends`, a set of UUIDs, stored as `dep_<uuid>` keys.
  Dependency only — no parent/child except as produced by recurrence.
- **Assignment.** **Absent.** Taskwarrior is single-user by design.
- **Labels.** `tags`, free-form, stored as `tag_<tag>` keys; plus `project`, a
  single dotted hierarchical string.
- **Change detection.** `modified` timestamp, and beneath it TaskChampion's
  operation-based replication — the sync model is a log of operations against
  replicas, not a version number on a document. That is a genuinely different
  change-detection paradigm from everything else here.
- **Search and filter.** A rich local filter language; there is no server to
  filter on. The "server" (taskchampion-sync-server) syncs opaque operations and
  cannot query.
- **Auth, pagination, rate limiting.** Not applicable in the usual sense: the
  library would read the local replica or drive the `task` binary.

**Assessment:** Taskwarrior is the system whose *identity* model matches
iCalendar exactly (client-minted UUID, offline creation, sync afterwards).  It has no HTTP API - the backend interface has to execute the `task` CLI or access the database files, which stretches the tracker architecture.

### 2.6 Emacs org-mode

**What it is:** File format and Emacs Lisp code.  The file may contain a list of tasks (and many other things).

Sources: read from the installed Emacs 30.2 org sources — `org.el`, `org-clock.el`,
`org-duration.el`.

- **Identity.** No identifier by default; a headline is located by its position in
  a file. `org-id` adds an `:ID:` property containing a UUID, client-generated, but
  it is opt-in and many org files have none. So identity ranges from "none" to
  "client-assignable UUID" depending on configuration.
- **Time estimate and time spent.** **Both, and this is the system that gets
  closest to what the library needs.** The estimate is the `Effort` property
  (`org-effort-property` = `"Effort"`, "the property that is being used to keep
  track of effort estimates"), formatted per `org-duration.el`. Spent time is a
  **log of true intervals**: `CLOCK:` lines (`org-clock-string` = `"CLOCK:"`)
  inside a `:LOGBOOK:` drawer, each recording a start timestamp, an end timestamp
  and the computed duration:
  `CLOCK: [2026-09-01 Tue 10:00]--[2026-09-01 Tue 11:30] =>  1:30`.
  A running clock is a `CLOCK:` line with a start and no end — the same
  null-end-means-running convention Vikunja uses.
  Durations are minute-based with fuzzy calendar units (`org-duration-units`:
  `min`, `h`, `d`, `w`, `m` = 30 d, `y` = 365.25 d) — **not** ISO 8601, and `m`
  meaning month rather than minute is a live conversion hazard.
- **Status lifecycle.** **Fully user-definable**, and the reference case for that
  dimension. `org-todo-keywords` defaults to `((sequence "TODO" "DONE"))` but is
  arbitrary, and a sequence may declare where the "done" states begin with a `|`.
  Keywords can be sequences (workflow steps) or types (categories of item). Any
  mapping to a fixed status enum is lossy in both directions.
- **Priority.** A single character in brackets, `[#A]`, ranging between
  `org-priority-highest` (default `?A`) and `org-priority-lowest` (default `?C`),
  defaulting to `org-priority-default` (`?B`). Configurable, and *may* be numeric
  if configured that way.
- **Dates.** `SCHEDULED:` (when you intend to start) and `DEADLINE:` (when it must
  be done) as distinct planning lines — precisely the distinction RFC 5545 lacks —
  plus plain and inactive timestamps, `CLOSED:`, and per-property timestamps.
- **Recurrence.** Repeater cookies on a timestamp: `+1w` (from the scheduled
  date), `++1w` (advance past today), `.+1w` (from the completion date). The three
  cookies encode exactly the fixed-time versus interval distinction plann's design
  notes describe, and `.+` is the "one week after it was actually done" case that
  `RRULE` cannot express.
- **Dependencies and parent/child.** Hierarchy is inherent — org *is* a tree. The
  `:ORDERED:` property plus `org-enforce-todo-dependencies` blocks a parent until
  children are done. Named cross-tree dependencies (`BLOCKER`/`TRIGGER`) come from
  `org-depend.el` in org-contrib, not core **[unverified]**.
- **Assignment.** **Absent** natively; a convention over properties or tags.
- **Labels.** Tags, inherited down the outline tree — an inheritance model nothing
  else here has — plus arbitrary properties in a `:PROPERTIES:` drawer.
- **Change detection.** **Absent.** A file's mtime, and nothing per-headline.
- **Search and filter.** Rich local queries (agenda views, `org-ql`), no server.
- **Auth, pagination, rate limiting.** Not applicable; it is a text file.

**Assessment:** org-mode is the single most informative system in this survey
for roadmap item 1.6, because it independently arrived at the same decomposition the
library needs: a scalar `Effort` estimate on the item, and a separate
append-only log of start/end intervals for time actually spent.

### 2.7 OpenProject

**What it is:** Project management web application.

Source: [OpenProject API v3 — work packages](https://www.openproject.org/docs/api/endpoints/work-packages/).

- **Identity.** `id`, server-assigned integer. No client-assignable identifier.
- **Time estimate and time spent.** The most complete: **three** quantities, not
  two. `estimatedTime` (work excluding descendants), `derivedEstimatedTime`
  (including descendants, read-only), `remainingTime`, and `spentTime` — the last
  aggregated read-only from a separate `TimeEntry` resource created via a `logTime`
  action. Plus `percentageDone` and `derivedPercentageDone`. The parent/child
  rollup of both estimate and completion is unique in this survey.
- **Status lifecycle.** **Admin-configurable**, as a first-class resource:
  statuses are addressed as `/api/v3/statuses/{id}`, not as an enum. A client
  cannot know the value set without fetching it.
- **Priority.** Also a configurable resource, `/api/v3/priorities/{id}`.
- **Dates.** `startDate` and `dueDate` (and `date` for milestones), plus
  `createdAt`/`updatedAt`.
- **Recurrence.** **Absent** on work packages **[unverified]**.
- **Dependencies and parent/child.** `parent` for hierarchy, plus a `relations`
  sub-resource. The relation type vocabulary is not enumerated in the endpoint
  documentation read here **[unverified]** — OpenProject is known to implement the
  classical `follows`/`precedes`/`blocks`/`blocked`/`relates`/`duplicates`/
  `includes`/`partof`/`requires`/`required` set, but that was not confirmed from a
  primary source.
- **Assignment.** Two distinct roles: `assignee` ("intended worker") and
  `responsible` ("accountable for outcome"). No other surveyed system separates
  these, and iCalendar cannot express the distinction except through `ATTENDEE`
  `ROLE` parameters.
- **Labels.** Categories and version, plus `type` — no free-form tags **[unverified]**.
- **Change detection.** **`lockVersion`**, "the version of the item as used for
  optimistic locking", sent back on update and rejected if stale. This is a proper
  ETag equivalent, the strongest change detection in the survey.
- **Search and filter.** Extensive server-side filter/sort query language over the
  HAL+JSON collection.
- **Auth, pagination, rate limiting.** API key or OAuth2; `offset`/`pageSize`.

**Assessment:** An earlier README recorded a first impression that OpenProject "looks a
bit like some commercial solutions backed by heavy marketing investments slightly
abusing the 'open' keyword". On the data model, that impression is not borne out:
it has the most complete time model of any system surveyed (estimate, remaining,
spent and percentage, rolled up over the hierarchy), and the strongest change
detection (`lockVersion`). The cost is weight: statuses and priorities are
server-side resources that must be fetched before anything can be mapped, and it
is the largest system surveyed to run in CI. Whether the *project* is a good
partner is a separate question this survey does not answer.

### 2.8 Request Tracker

**What it is:** A tool primarily designed for answering and tracking inbound email requests, optimised for IT departments.  A request ("ticket") is a task, although many of them can be solved by sending one sentence of text and pressing "resolve".

Source: [`RT::Ticket`](https://docs.bestpractical.com/rt/5.0.5/RT/Ticket.html) (RT 5.0.5).

- **Identity.** `id`, server-assigned integer.
- **Time estimate and time spent.** Three quantities again, all **in minutes**:
  `TimeEstimated`, `TimeWorked`, `TimeLeft`. `TimeWorked` is **an accumulating
  total, not a log** — RT's per-transaction time is recorded on the transaction
  objects, but the ticket field is a running sum. RT is the oldest system here and
  had built-in time accounting decades before the forge trackers did.
- **Status lifecycle.** **Configurable per queue** via "Lifecycles" — a queue
  defines its own status set *and the legal transitions between them*. RT is the
  only surveyed system that models the state *machine* and not just the state.
- **Priority.** An integer **0–99**, plus `InitialPriority` and `FinalPriority`,
  which together let RT ramp a ticket's priority automatically as its `Due`
  approaches. No other system surveyed has a time-varying priority.
- **Dates.** The most carefully distinguished set after Taskwarrior's, and it has
  the one distinction plann's design notes single out as missing from RFC 5545:
  `Starts` (scheduled start) **and** `Started` (actual start) as separate fields,
  plus `Due`, `Resolved`, `Told` (last contact), `Created`, `LastUpdated`.
- **Recurrence.** **Absent** in core **[unverified]**.
- **Dependencies and parent/child.** A full link vocabulary: `MemberOf`/`HasMember`
  (aliased `Parents`/`Children`), `DependsOn`/`DependedOnBy`, `RefersTo`/
  `ReferredToBy` — hierarchy, dependency and reference, each with an inverse.
- **Assignment.** `Owner` (single), plus `Requestor`, `Cc`, `AdminCc` as groups —
  a richer correspondent model than anywhere else, and the closest analogue to
  iCalendar's `ORGANIZER`/`ATTENDEE` split.
- **Labels.** Custom fields per queue; no free-form tags in core.
- **Change detection.** `LastUpdated` timestamp.
- **Search and filter.** TicketSQL, a genuine server-side query language.
- **Auth, pagination, rate limiting.** REST2 with token auth; paginated.

**Assessment:** RT has the richest *workflow* model surveyed — per-queue
lifecycles with legal transitions, a time-ramping priority, and the
`Starts`/`Started` distinction — but time spent is a running total on the
ticket, with the per-entry time buried in the transaction history. Its
correspondent model (`Requestor`/`Cc`/`AdminCc`) is the closest analogue to
iCalendar's `ORGANIZER`/`ATTENDEE`. As a backend it would mainly stress status
normalisation and the native-status passthrough.

### 2.9 EteSync / Etebase

**What it is:** Not a task model - it's an encrypted transport layer for iCalendar.

Sources: [`etesync-web/src/pim-types.ts`](https://github.com/etesync/etesync-web/blob/master/src/pim-types.ts),
[`etesync/server`](https://github.com/etesync/server).

EteSync's `TaskType` extends its `EventType` and wraps `ICAL.js` directly: status is
iCalendar's `TaskStatusType`, priority is iCalendar's `TaskPriorityType`, and
parent/child is `RELATED-TO` (`relatedTo` reads and writes a single parent UID).
A grep of its type definitions finds **no `X-` properties at all**.

Every dimension therefore answers "whatever RFC 5545 says" — including the time
gap. EteSync inherits the estimate and time-spent problem rather than solving it.

- **Identity.** iCalendar `UID` — client-assignable, like CalDAV.
- **Change detection.** Etebase revisions on encrypted items, not per-property.
- **Auth.** Etebase account plus an encryption password; **the server cannot read
  or filter content**, so all search is client-side by construction.

**Assessment:** For the library, EteSync is architecturally a *sibling of the
local-file backend*, not of the trackers: opaque blobs of iCalendar, no
server-side query, client-side filtering via `icalendar-searcher`. That is a
much cheaper backend to add than any tracker, and it is a natural candidate
for "beyond the funded scope". Note the server repository's last commit is
from July 2024 — the project is not archived, but it is not busy either.

### 2.10 Focalboard

**What it is:** A web-based, kanban-style project board from Mattermost, available standalone and as a Mattermost plugin.

Source: [`mattermost/focalboard`](https://github.com/mattermost/focalboard).

**The repository opens with: "This repository is currently not maintained. If
you're interested in becoming a maintainer please let us know."** Standalone
Focalboard has been separated from the Mattermost plugin
(`mattermost/mattermost-plugin-boards`), and the standalone product is the one
the README's list referred to.

Its model is a generic board: cards carry **user-defined properties** of chosen
types (select, date, person, number, …), so there is no fixed task schema at all
to survey — status, priority, dates and estimates are all whatever the board
author created, discoverable only by reading the board's property definitions
first.

**Assessment:** Focalboard should be dropped from the candidate list. Both
reasons are independently sufficient: it is unmaintained, and a schemaless
board means a backend would have to map user-defined property *instances* onto
the unified model with no reliable convention to key on. It remains
interesting as the extreme case for the capability-declaration design — a
backend whose schema is only knowable at runtime — and the API should not make
that impossible to express later.

### 2.11 Plain-Text/Markdown files

**What it is:** Plain-text task lists, edited by hand or through an editor plugin.

There is no single format — anything goes, so the library could define its own Markdown convention.  This section surveys the two conventions with meaningful adoption.

**[todo.txt](https://github.com/todotxt/todo.txt)** — one task per line:
`x` marks completion; `(A)`–`(Z)` is priority, first on the line; two bare
`YYYY-MM-DD` dates mean **completion then creation**, in that order (`x
2011-03-02 2011-03-01 Review Tim's pull request`), so that completed tasks sort
by completion date; `+project` and `@context` are
tags; and arbitrary `key:value` pairs carry everything else, with `due:` as the
only widely conventional key. **No identifier, no estimate, no time spent.**

**[Obsidian Tasks](https://publish.obsidian.md/tasks/Reference/Task+Formats/Tasks+Emoji+Format)**
— Markdown checkboxes with emoji signifiers, and the most complete Markdown
convention in use:

| Signifier | Field | Signifier | Field |
|---|---|---|---|
| `➕` | created date | `🔺⏫🔼🔽⏬` | priority, highest→lowest |
| `🛫` | start date | `🔁` | recurrence (`every … [when done]`) |
| `⏳` | scheduled date | `🆔` | task id |
| `📅` | due date | `⛔` | depends on (comma-separated ids) |
| `✅` | done date | `🏁` | on-completion behaviour |
| `❌` | cancelled date | | |

It has four distinct date senses, a cancelled state, recurrence including the
"when done" variant, **and an id/depends-on pair** — a client-assignable
identifier, which puts it ahead of every hosted tracker on that dimension. It has
**no time estimate and no time tracking**.

- **Change detection.** File mtime, for either format. Nothing per task.
- **Search.** Client-side only.

**Assessment:** Markdown files are a *serialisation*, not a system, and the
honest way to support them is as a configurable line-format over the same
in-memory model the local `.ics` backend uses. Note that both conventions
independently chose a `start` / `scheduled` / `due` triple, which iCalendar
cannot express.

---

## Part 3: Cross-cutting comparison

Legend: **●** first-class support · **◐** partial, lossy or convention-based ·
**○** absent · **conf** administrator- or user-configurable.

### 3.1 The headline table

| | Gitea | GitLab | Vikunja | Kanboard | Taskwarrior | org-mode | OpenProject | RT | EteSync | Focalboard | Markdown |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Client-assignable id | ○ | ○ | ○ | ◐ `reference` | **●** uuid | ◐ `org-id` | ○ | ○ | **●** UID | ○ | ◐ Obsidian `🆔` |
| Time **estimate** | ● | ● | ○ | ● | ◐ UDA | ● `Effort` | ● | ● | ○ | conf | ○ |
| Time **spent** | ● log | ◐ total | ● log | ◐ scalar | ○ | ● log | ◐ total + log | ◐ total | ○ | conf | ○ |
| Remaining time | ○ | ○ | ○ | ○ | ○ | ○ | ● | ● | ○ | conf | ○ |
| Percent complete | ○ | ○ | ● | ○ | ○ | ◐ cookie | ● | ○ | ● | conf | ○ |
| Status richness | 2 | 2 | 2 | conf | 4 | conf | conf | conf+FSM | 4 (iCal) | conf | 2–3 |
| Priority | ○ | ○ | ● int ∞ | ● int | ● H/M/L | ● A–C | ● conf | ● 0–99 | ● 0–9 | conf | ● A–Z |
| Start date | ○ | ● 19.1+ | ● | ● | ● | ● SCHEDULED | ● | ● Starts | ● | conf | ● |
| Actual-start date | ○ | ○ | ○ | ○ | ● `start` | ◐ clock | ○ | ● Started | ○ | conf | ○ |
| Due date | ● | ● date-only | ● | ● | ● | ● DEADLINE | ● | ● | ● | conf | ● |
| Defer / wait | ○ | ○ | ○ | ○ | ● `wait` | ○ | ○ | ○ | ○ | conf | ○ |
| Recurrence | ○ | ○ | ◐ seconds | ◐ trigger | ● | ● 3 modes | ○ | ○ | ● RRULE | ○ | ◐ |
| Parent/child | ○ | ◐ epic | ● | ◐ subtask | ○ | ● tree | ● | ● | ● | ○ | ○ |
| Dependencies | ● | ◐ paid | ● | ◐ | ● | ◐ contrib | ● | ● | ○ | ○ | ◐ Obsidian |
| Multi-assignee | ● | ◐ paid | ● | ○ single | ○ | ○ | ● 2 roles | ◐ single owner | ● | conf | ○ |
| Free-form labels | ● | ● | ● | ● | ● | ● inherited | ◐ | ◐ CF | ● | conf | ● |
| Optimistic locking | **●** | ○ | ○ | ○ | ◐ op-log | ○ | **●** | ○ | ● rev | ○ | ○ |
| Server-side search | ● | ● | ● | ● | n/a | n/a | ● | ● | **○ by design** | ● | n/a |

### 3.2 Dimension by dimension

**Identity.** Every one of the six hosted trackers — Gitea, GitLab, Vikunja,
Kanboard, OpenProject, RT — hands out a server-assigned integer, and only one of
them has anywhere to put a foreign identifier: Kanboard's `reference` field
("external ticket identifier"). The five file-based systems split the other way,
and instructively: Taskwarrior and EteSync mint client-side UUIDs *because both
are built to work offline first*, which is also why iCalendar does; org-mode and
the two Markdown conventions have no identifier at all unless one is opted into
(`org-id`, Obsidian's `🆔`).

So the dividing line is not hosted-versus-local but **who creates the object
first**. A system that must work before it has ever spoken to a server has no
choice but to let the client mint the identifier.

**The unified model cannot assume UID round-trips, and must say
so.** A task created in this library and pushed to Gitea comes back with a
different identity and no memory of the original. The model needs three
separate concepts: the library's own `uid`, the backend's native id, and an
*optional* foreign-id slot that a backend declares whether it can persist.
Every synchronisation feature downstream depends on which of the three a given
backend supports.

**Time estimate.** Present in seven of the nine systems that have a task model
of their own (EteSync carries plain `VTODO`, and Focalboard's schema is
whatever each board defines), and always as a **single scalar duration on the
task**. There is no disagreement about shape anywhere — only about
units (Gitea and GitLab seconds, Kanboard hours, RT minutes, org-mode minutes with
fuzzy calendar units, OpenProject ISO 8601). This is the easy half.

**Time spent — the central question.** Four distinct shapes:

| Shape | Systems | Entry contains |
|---|---|---|
| **Interval log** | Vikunja `TimeEntry`, org-mode `CLOCK:` | start, end (null = running), comment, user |
| **Stamped-duration log** | Gitea `TrackedTime` | duration, created-at, user |
| **Accumulated scalar** | GitLab `total_time_spent`, Kanboard `time_spent`, RT `TimeWorked`, OpenProject `spentTime` | a number |
| **Absent** | Taskwarrior, EteSync/`VTODO`, todo.txt, Obsidian Tasks | — |

The four are not alternatives; they are a **lattice**. An interval log yields a
stamped-duration log by dropping the end time, and either yields the scalar by
summing. Nothing goes the other way. GitLab and OpenProject make this explicit:
both *have* per-entry logs internally and expose the scalar as the derived,
read-only convenience on the task.

**This settles the open question in roadmap item 1.6.** Spent time
must be modelled as an **append-only log of entries**, each with an optional
start and end, a duration, a user and a comment, with the scalar total
derived. Two independent systems (org-mode and Vikunja) converged on exactly
this, five more can be read into it losslessly-downward, and the alternative —
modelling the scalar and bolting a log on later — cannot represent what
org-mode and Vikunja already store. A backend that can only hold a scalar
declares the log unsupported and accepts writes of the aggregate.

**Status.** Three families, and no two members of the middle family agree:

- **Binary** — Gitea, GitLab (`open`/`closed`), Vikunja and Kanboard (a `done`
  flag). Everything richer lives in labels or board columns.
- **Fixed small enum** — Taskwarrior (4), iCalendar/EteSync (4, or 6 with the
  tasks draft), JSCalendar (5).
- **Configurable** — org-mode keywords, Kanboard columns, OpenProject status
  resources, RT queue lifecycles. RT alone also models the legal *transitions*.

**A normalised status enum is necessary and insufficient.** The
model needs both: a normalised value drawn from the tasks draft's six
(`PENDING`, `NEEDS-ACTION`, `IN-PROCESS`, `COMPLETED`, `CANCELLED`, `FAILED`),
and the backend's **native status string preserved alongside it**, because
round-tripping a Kanboard card through a normalised enum would otherwise move
it to a different column. The tasks draft's six values are a good
normalisation target precisely because they are the widest fixed vocabulary
anyone has standardised.

**Priority.** Five incompatible scales: absent (Gitea, GitLab), 0–9 with 1 highest
(iCalendar, JSCalendar), 0–99 with a time-ramp (RT), unbounded integer with no
defined meaning (Vikunja — "can be anything you want"), and ordinal letters
(Taskwarrior `H`/`M`/`L`, org-mode `A`–`C`, todo.txt `A`–`Z`). Note also that
plann's `TASK_MANAGEMENT.md` assigns *semantics* to iCalendar's 1–9 that no other
system shares — priority as a statement about how hard the deadline is.

**Priority needs the same treatment as status:** normalise to iCalendar's 0–9, keep
the native value, and document the mapping as lossy in both directions.

**Dates.** The set of distinct senses across all systems is larger than any single
system implements: created, planned-start, actual-start, scheduled/next-action,
defer-until, due/deadline, completed, cancelled, last-modified, last-contact.
RFC 5545 has room for three. Taskwarrior implements seven, RT distinguishes
`Starts` from `Started`, and both Markdown conventions independently chose a
`start`/`scheduled`/`due` triple.

**`DTSTART` overloading is not a plann quirk, it is the universal
symptom.** Every system that takes tasks seriously ends up needing at least
start-vs-scheduled-vs-due, and iCalendar forces those three into two slots.
The unified model should carry the senses separately and map down to iCalendar
with a documented, configurable policy — this is exactly where the capability
matrix earns its keep.

**Recurrence.** Absent in the forge trackers and in OpenProject and RT; present but
weak in Vikunja (an interval in seconds) and Kanboard (a trigger state machine);
strong in Taskwarrior (`rtype` chooses periodic vs chained) and org-mode (`+`,
`++`, `.+` cookies). org-mode's `.+1w` — "one week after it was actually done" —
is the case plann's design notes call interval-style recurrence, and **`RRULE`
cannot express it**.

**Dependencies.** Vikunja's eleven relation kinds and RT's six links are close
matches for RFC 9253's vocabulary; Gitea and OpenProject are subsets. This
confirms the RFC 9253 baseline ([1.4](#14-rfc-9253--relationships-are-solved)), with its limit: for hierarchy and
dependency, iCalendar-with-9253 covers everything the trackers express, and
mapping into calendaring is lossless. Duplicate, copy and plain-reference
relations are the exception and need `LINK` or an `X-` value.

**Change detection.** Three systems have proper optimistic-locking tokens — Gitea's
`content_version`, OpenProject's `lockVersion` and EteSync's item revision — which
behave like an ETag and let a client detect a lost update. Everything else offers only an `updated`
timestamp, which cannot distinguish "unchanged" from "changed twice within the
clock resolution". Taskwarrior is in a category of its own: an operation log
replicated between peers, with no document version at all.

**Auth, pagination, rate limiting.** Every hosted tracker is bearer-token over
HTTPS with offset pagination — uninteresting and uniform, and therefore cheap
after the first one. The *interesting* costs are elsewhere: EteSync forces all
filtering client-side by construction, Taskwarrior has no server to talk to, and
GitLab hides several surveyed dimensions behind paid tiers.

---

## Part 4: A proposed task model

### 4.1 Shape

The survey supports a model with four layers, and the layering matters more than
the field list:

1. **A normalised core** — the fields nearly everything has, with a defined
   vocabulary the library owns.
2. **Native passthrough** — for every normalised field whose vocabulary is
   contested (status, priority), the backend's own value carried alongside,
   never discarded.
3. **A capability declaration** — per backend, per field, machine-readable, so
   that "Gitea has no priority" is an asserted fact the conformance suite checks
   and the capability matrix (roadmap item 4.3) generates from.
4. **An escape hatch** — access to backend-native data the model does not cover,
   so callers are not forced to lie.

### 4.2 The time model

```
Task.estimate        : Duration | None      # scalar; ESTIMATED-DURATION
Task.remaining       : Duration | None      # OpenProject, RT only
Task.time_log        : TimeLog              # append-only sequence
Task.time_spent      : Duration             # derived: sum(time_log)
Task.percent_complete: int | None           # last TimeEntry that sets it, else backend scalar

TimeEntry.start      : datetime | None      # None where backend stores no interval
TimeEntry.end        : datetime | None      # None => still running
TimeEntry.duration   : Duration             # always present; derived if start/end given
TimeEntry.user       : Principal | None
TimeEntry.comment    : str | None
TimeEntry.percent_complete: int | None      # 0-100
TimeEntry.id         : str | None           # backend-native, for update/delete
```

`estimate`, `remaining` and `percent_complete` are three independent facts and the
survey shows systems that carry each without the others; none may be derived from
another.

The `time_log` consists of TimeEntries that can be summed up to get the actual time spent working on the project.  It also tells when work was started and when the task was completed.

In addition extra timestamps may be applied:

* Creation time for the task
* Earliest possible start (`dtstart` in tasks draft)
* Expected start
* Expected completion
* Due

### 4.3 The iCalendar representation of time spent — a recommendation

The gap is real and the library must choose. plann's
[`NEXT_LEVEL.md`](https://github.com/pycalendar/plann/blob/master/NEXT_LEVEL.md)
lists three candidates; the survey adds two more, one of which its own author
half-proposed elsewhere in the same document.

| | Round-trips through an unmodified CalDAV server? | Server-side date-range search? | Expresses an interval? | Multiple entries per task? |
|---|---|---|---|---|
| A. `DTEND` on `VJOURNAL` | **No** — invalid per RFC 5545 | yes | yes | yes |
| B. `PARTSTAT=X-ATTENDED` | yes | no | no | **no** |
| C. New `VTIMESPENT` component | **No** — servers reject unknown components | no | yes | yes |
| D. **Child `VEVENT` per entry**, `RELATED-TO` the `VTODO` | **yes** | **yes** | **yes** | **yes** |
| E. Repeating `X-` property on the `VTODO` | yes | no | with parameters | yes |

**The recommendation is D**, with E as the fallback for backends that cannot
create sibling objects.

A work-log entry is a thing that happened between two timestamps.  A `VEVENT`
also describes something happening between a `DTSTART` and a `DTEND`.  Let
`RELATED-TO;RELTYPE=PARENT` point at the task's `UID`, and add a marker
distinguishing "work done" from "planned meeting" (see 4.4). It maps
**one-to-one onto Vikunja's `TimeEntry` and org-mode's `CLOCK:` lines**, the two
systems the survey found had already solved this.

Time-stamped entries are what make "how much time did I spend on tasks last
week?" answerable at all: a scalar total only says that a task which has run
for several weeks has taken 16 hours so far. Every option except B keeps
entries; the "server-side date-range search" column is about who does the
filtering when asking that question. With D, each entry is a `VEVENT` with
its own `DTSTART`/`DTEND`, so a CalDAV `calendar-query` with a time-range filter
makes the server return just that week's entries, and the client sums them.
With B, C and E the entries are hidden inside the `VTODO` (or a component the
server does not understand). The server can filter `VTODO`s only on the task's
own dates, not on when work was logged, so the client must download every task
and dig the entries out itself. A would be filterable (RFC 4791 defines
time-range filtering on `VJOURNAL`), but it is invalid RFC 5545.

The cost is that work-log events appear in ordinary calendar views. The mitigation
is to put them in their own collection, which is a configuration question rather
than a data-model one.

Note that this **does not discard** plann's `PARTSTAT=X-ATTENDED` idea, because the
two answer different questions. `X-ATTENDED` marks an *existing planned event* as
actually attended, adding no object; option D *creates* a record where the work was
not planned in advance. A complete implementation wants both, and the survey found
no system that has an equivalent of `X-ATTENDED` at all — it is a genuinely
original idea, and it is the narrower of the two.

Option D is also the one plann's own `NEXT_LEVEL.md` gestures at when it proposes
"a general rule that all activities should be registered on the calendar as a
`VTODO`/`VEVENT` parent/child pair, where the `VTODO` contains time estimates and
the `VEVENT` contains planned time". The proposal here is that same pairing used
for time *recorded* rather than time *planned*.

### 4.4 Details to be decided later

* How to tag a `VEVENT` as a work-log; can be done through `CATEGORIES:WORKLOG`, an `X-` property, or membership of a dedicated collection.

* Should we carry the `remaining` duration, considering that only two of the nine systems with a task model of their own support it?

* How to transport the native-status passthrough (e.g. a plain string or a typed object - RT and OpenProject can also report the legal transitions).

---

## Part 5: Which tracker to implement first

Roadmap item 2.5 budgets twelve hours for one tracker backend, chosen here.

**Recommendation: Gitea.**

The choice is not about which tracker is best; it is about which one best *tests
the abstraction* within twelve hours, since 2.5's stated purpose is that "if the
unified API cannot express this backend without contortion, the finding is more
valuable than the code".

Gitea wins on four counts:

1. **It has both an estimate and a per-entry spent-time log, on one REST API.**
   GitLab and OpenProject have both too, but GitLab's per-entry log is
   GraphQL-only and OpenProject is far heavier to run in CI. So Gitea is the
   cheapest way to exercise the whole of roadmap item 1.6 end to end, including the
   log/scalar distinction that [3.2](#32-dimension-by-dimension) turns on.
2. **It has an optimistic-locking token** (`content_version`), so it exercises the
   change-detection design against something better than a timestamp — the same
   problem the local-file backend (2.2) has to solve with an ETag surrogate.
3. **It is poor everywhere else** — two status values, no priority, one date, no
   recurrence. That is a feature: the capability declaration and the
   capability-aware conformance suite are the load-bearing parts of this design,
   and a rich backend would not stress them. A backend that must answer "no" to
   half the suite is the honest test.
4. **It is trivially self-hostable** in a container for CI, with no paid tier
   hiding half the model, and its API contract is generated from Go structs that
   can be read directly rather than from prose that can drift.

**Runner-up: Vikunja**, and it is close. It has the best time model in the survey
(true intervals, live timers), the richest relation vocabulary, and the fullest
date set. It loses on three things: no time estimate at all, a v1→v2 API
transition in flight (v1 removed in 4.0) that would cost real hours to track, and
a priority field with no defined scale, which makes the normalisation mapping
arbitrary. It is the obvious *second* tracker, and if the goal were the best
demonstration rather than the hardest test, it would be first.

**Not recommended:** GitLab (paid tiers hide the interesting dimensions),
OpenProject (richest model, therefore the weakest test of the capability
machinery, and the largest API surface for twelve hours), Focalboard
(unmaintained — see [2.10](#210-focalboard)), Taskwarrior and org-mode (no server; valuable
backends, but they test the *local-file* architecture rather than the tracker
architecture, and belong with 2.2 rather than 2.5).

---

## Sources

### Standards
- [RFC 5545 — iCalendar](https://datatracker.ietf.org/doc/html/rfc5545)
- [RFC 8984 — JSCalendar](https://www.rfc-editor.org/rfc/rfc8984.txt)
- [RFC 9253 — Support for iCalendar Relationships](https://datatracker.ietf.org/doc/html/rfc9253)
- [draft-ietf-calext-ical-tasks-17 — Task Extensions to iCalendar](https://datatracker.ietf.org/doc/draft-ietf-calext-ical-tasks/)
  ([text of -17](https://www.ietf.org/archive/id/draft-ietf-calext-ical-tasks-17.txt))

### Systems surveyed
- Gitea — [`modules/structs/issue.go`](https://github.com/go-gitea/gitea/blob/main/modules/structs/issue.go), [`issue_tracked_time.go`](https://github.com/go-gitea/gitea/blob/main/modules/structs/issue_tracked_time.go), [`issue_stopwatch.go`](https://github.com/go-gitea/gitea/blob/main/modules/structs/issue_stopwatch.go)
- GitLab — [issues API](https://docs.gitlab.com/api/issues/)
- Vikunja — [v1 `docs.json`](https://try.vikunja.io/api/v1/docs.json), [v2 `openapi.json`](https://try.vikunja.io/api/v2/openapi.json) (read from a live 2.6.0 instance)
- Kanboard — [task procedures](https://docs.kanboard.org/v1/api/task_procedures/)
- Taskwarrior — [`task.1`](https://github.com/GothenburgBitFactory/taskwarrior/blob/develop/doc/man/task.1.in), [TaskChampion task model](https://gothenburgbitfactory.org/taskchampion/tasks.html)
- Emacs org-mode — `org.el`, `org-clock.el`, `org-duration.el` as installed (Emacs 30.2)
- OpenProject — [API v3 work packages](https://www.openproject.org/docs/api/endpoints/work-packages/)
- Request Tracker — [`RT::Ticket`](https://docs.bestpractical.com/rt/5.0.5/RT/Ticket.html)
- EteSync — [`etesync-web/src/pim-types.ts`](https://github.com/etesync/etesync-web/blob/master/src/pim-types.ts)
- Focalboard — [`mattermost/focalboard`](https://github.com/mattermost/focalboard)
- Markdown — [todo.txt](https://github.com/todotxt/todo.txt), [Obsidian Tasks emoji format](https://publish.obsidian.md/tasks/Reference/Task+Formats/Tasks+Emoji+Format)

### Prior art by the same author
- [plann `TASK_MANAGEMENT.md`](https://github.com/pycalendar/plann/blob/master/TASK_MANAGEMENT.md)
- [plann `NEXT_LEVEL.md`](https://github.com/pycalendar/plann/blob/master/NEXT_LEVEL.md)
- [plann `DESIGN.md`](https://github.com/pycalendar/plann/blob/master/DESIGN.md)

---

*Original document written with AI assistance - Claude Opus 5 via Claude Code - read through, understood and edited by the author. Claims marked **[unverified]** have not been checked against a primary source.*
