# calendaring

*One Python API for calendars and task lists: CalDAV, JMAP, iCalendar feeds, local files and issue trackers.*

**Purpose:** today, a program that works with calendar data has to pick a library for each place the data lives: `caldav` for a CalDAV server, `calendaring-jmap` for a JMAP server, `icalendar` and file handling for `.ics` files on disk.  This library gives one way to read and change that data, wherever it lives.  An application written against it can be pointed at a CalDAV server, a local `.ics` file or an issue tracker by changing its configuration, not its code.  Keeping two of those systems in step is a different job, done by [calendaring-sync](https://github.com/pycalendar/calendaring-sync).  The design is in [docs/design/API_DESIGN.md](docs/design/API_DESIGN.md).

The library is called `calendaring`: the same name on GitHub, on PyPI and as the import (see [the 0.3 decisions](docs/design/PRIOR_ART_AND_DECISIONS.md#d2-the-name)).  It was earlier called `calendaring-client`.  "Calendaring" is the IETF's word, and in RFC 5545 it covers tasks too, but few people know that, so the tagline above carries the message that this library supports tasks and not only events.

## Status

**Pre-alpha, not released yet.**  Nothing is on PyPI so far.  What exists today is the framework: the unified API as abstract base classes (`Workspace`, `Backend`, `Collection` and the item classes) in both sync and async mode, a backend conformance suite (`calendaring.testing`), and an in-memory reference backend (`calendaring.memory`).  No real backend is implemented yet.

| Release | Contents |
|---|---|
| 0.1.0 | CalDAV backend behind the unified API, configuration file, sync + async |
| 0.2.0 | Local files and iCalendar feeds |
| 0.3.0 | JMAP |
| 0.4.0 | First task-tracker backend, time tracking, capability matrix |
| 1.0.0 | Conformance across all backends, full documentation, API declared stable |

The plan and its status are in [docs/design/ROADMAP.md](docs/design/ROADMAP.md).

## Installation and usage

Python 3.11 or newer.  Until the first release, install from git:

```
pip install git+https://github.com/pycalendar/calendaring.git
```

With only the in-memory backend available, this is what it looks like today:

```python
from calendaring import Component, Workspace
from calendaring.memory import MemoryBackend

with Workspace([MemoryBackend()]) as workspace:
    tasks = workspace.backends[0].create_collection("Tasks", components={Component.TASK})
    task = tasks.add_task(summary="Update the README", priority=1)
    task.complete()
    for item in workspace.search(todo=True, include_completed=True).items:
        print(item.component["SUMMARY"], item.component["STATUS"])
```

`AsyncWorkspace`, `AsyncBackend` and so on are the async equivalents.

## Background

I've been the maintainer of the Python CalDAV client package for many years now.  The ironic thing is that I started using the library because I "didn't want to get my hands dirty".  I had to do some fixes for the library to work towards my server, and soon enough the owner of the project passed me the maintainer hat.  To be honest, I think neither the CalDAV protocol nor the iCalendar format is a particularly good standard.  They are sort of the only open standards we have, so it's important to support them, but it's also important to support new and emerging standards.

There are no good standards for tasks.  Apart from iCalendar, which supports task lists but not very well, there are only vendor formats.  For instance, iCalendar has no way to record a time estimate or the time spent on a task.  The estimate is about to be fixed (`ESTIMATED-DURATION` in the [task extensions draft](https://datatracker.ietf.org/doc/draft-ietf-calext-ical-tasks/), now in the RFC Editor queue), but time spent is still a gap.  See [the task model survey](docs/design/TASK_MODEL_SURVEY.md).

## Mission statement

This is intended to be a general Python API for accessing task and calendaring information, regardless of where it's stored and how to access the backend.  Version 0.1.0 won't be much more than a wrapper for the CalDAV library - but it will provide a clean, general and well-documented interface, and a clean, general and well-documented [configuration file](docs/design/CONFIGURATION_PROPOSAL.md).  Local files, iCalendar feeds, JMAP and a first issue tracker follow in the next releases (see [Status](#status)).

I've considered supporting other standards in the CalDAV library, but it's outside the scope for that library - hence the need for another package.

### Personal motivation

* I feel that the combination of a lack of a good task list and "too many TODO-lists" has been a consistent pain for most of my life.
* I would like to have all relevant tasks and calendar events open in front of me when planning my days, weeks and months regardless of where the tasks are stored.
* I'd like to throw standardized attributes on my tasks and issues (like time spent, time estimate, priority, due-time) regardless of where they exist (this may involve synchronizing information from external systems with my own calendar/task store).  I do hope that this library can help me achieve this.
* I have the [`plann`](https://github.com/tobixen/plann) utility, a CLI and library for more advanced calendaring and task management.  It offers things that are out of the scope of both CalDAV and this library, including "panic planning" where it tries to fit the tasks with the highest priority into the available time before the deadlines.  It uses the CalDAV library, but I'd like to use it towards other systems as well.  `plann` will be the first real user of this library.

## Backends

Funded (see [Funding](#funding) and the [roadmap](docs/design/ROADMAP.md)):

* **CalDAV**, through the `caldav` library.  It's (IMO) not a very good standard, but it is sort of the only standard for distributing and updating calendaring information.
* **iCalendar feeds** (read-only, `https://` and `webcal://`).  It has become quite normal to offer users access to calendar data through iCalendar links.  It's much simpler than CalDAV, but offers fewer features.  Several years ago I got requests for feed support in the CalDAV library, and rejected it for being outside its scope.  With [icalendar-searcher](https://github.com/pycalendar/icalendar-searcher), split out of the CalDAV library, search behaves the same towards a CalDAV server and an iCalendar feed.
* **Local iCalendar files** (`.ics` files and vdir directories), read and write.  The API works the same regardless of whether it is used towards a remote calendar server or towards local files.
* **JMAP**, through [calendaring-jmap](https://github.com/pycalendar/calendaring-jmap).
* **One issue tracker**: Gitea, chosen by [the task model survey](docs/design/TASK_MODEL_SURVEY.md#part-5-which-tracker-to-implement-first), which also covers Vikunja, GitLab, Kanboard, Taskwarrior, Emacs org-mode, OpenProject, Request Tracker, EteSync and plain-text task files.

Wanted, but outside the funded scope:

* Sending iCalendar data as email attachments (iTIP), and perhaps accessing mailboxes through IMAP and/or JMAP.  Many mail systems come bundled with calendar systems and interoperate with other systems this way.
* Mobilizon, bidirectionally.  It's a federated server designed for spreading calendar event information, and it's quite high on my list.
* GitHub issues, the first tracker after Gitea.  It is proprietary, but it is where this project and its siblings keep their own issues.
* More task trackers, org-mode and Markdown task files.
* Proprietary systems offering calendaring and/or task management without supporting standards very well.  I'm not keen on working with those systems, but I'd happily accept pull requests for supporting any of them.

## Standards

* iCalendar - RFC 5545, with RFC 7986 (new properties) and RFC 9253 (relationships)
* The iCalendar task extensions draft (`ESTIMATED-DURATION`)
* CalDAV - RFC 4791, through `caldav`
* JMAP - RFC 8620 and JMAP Calendars, through `calendaring-jmap`
* vdir, for local file storage

The full list, with the status of each standard and what is left out, is in [PRIOR_ART_AND_DECISIONS.md, Part 2](docs/design/PRIOR_ART_AND_DECISIONS.md#part-2-standards).

## Prior art and users

[PRIOR_ART_AND_DECISIONS.md, Part 1](docs/design/PRIOR_ART_AND_DECISIONS.md#part-1-prior-art) looks at icalcli, vdirsyncer/khal/todoman, `ical`, Home Assistant and calendaring-jmap.  None of them is a multi-backend *library* with first-class task support; icalcli, for example, is a CLI with no task support at all.

Over the last years many CalDAV issues have come in through users of Home Assistant.  Home Assistant has its own multi-backend calendar and to-do abstraction, one layer above this library, and its CalDAV integration could be built on this library.  More generally, I think that any project that either reads iCalendar feeds or uses the CalDAV client library could have use for such a "swiss army knife".

## Documentation

* [API design](docs/design/API_DESIGN.md)
* [Roadmap](docs/design/ROADMAP.md)
* [Task model survey](docs/design/TASK_MODEL_SURVEY.md)
* [Sync/async architecture](docs/design/SYNC_ASYNC_ARCHITECTURE.md)
* [Configuration proposal](docs/design/CONFIGURATION_PROPOSAL.md)
* [Prior art, standards and project decisions](docs/design/PRIOR_ART_AND_DECISIONS.md)
* [Change log](docs/changelog.rst)
* [Contributing](CONTRIBUTING.md) and [security policy](SECURITY.md)

## Funding

[<img src="https://nlnet.nl/logo/banner.svg" alt="NLnet foundation logo" width="20%" />](https://nlnet.nl)
&nbsp;&nbsp;
[<img src="https://nlnet.nl/image/logos/NGI0Core_tag.svg" alt="NGI Zero Core Logo" width="20%" />](https://nlnet.nl/core)

This project is funded through the [NGI Zero Core](https://nlnet.nl/core) fund, a
fund established by [NLnet](https://nlnet.nl/) with financial support from the
European Commission's [Next Generation Internet](https://ngi.eu/) programme, under
the aegis of DG Communications Networks, Content and Technology.

## Licence

`calendaring` is dual-licensed: GPL-3.0-or-later ([`COPYING.GPL`](COPYING.GPL)) **or** Apache-2.0 ([`COPYING.APACHE`](COPYING.APACHE)), at your choice.

The JMAP backend will depend on `calendaring-jmap`, which is AGPL-3.0-or-later.  It is an optional extra (`calendaring[jmap]`).  A program that includes it must, as a whole, be conveyed under AGPL-3.0 terms; `calendaring`'s own code remains available under either licence.

Future hard dependencies may be AGPL-licensed too ([edit-icalendar](https://github.com/pycalendar/edit-icalendar) is a likely one), which would make every installation contain AGPL code.  The licence of this library may be changed to AGPL-3.0-or-later if that happens; see [D3](docs/design/PRIOR_ART_AND_DECISIONS.md#d3-licence).
