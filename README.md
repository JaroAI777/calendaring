# A Calendaring and Task Client

*One Python API for calendars and task lists: CalDAV, JMAP, iCalendar feeds, local files and issue trackers.*

**Purpose:** today, a program that works with calendar data has to pick a library for each place the data lives: `caldav` for a CalDAV server, `calendaring-jmap` for a JMAP server, `icalendar` and file handling for `.ics` files on disk.  This library gives one way to read and change that data, wherever it lives.  An application written against it can be pointed at a CalDAV server, a local `.ics` file or an issue tracker by changing its configuration, not its code.  Keeping two of those systems in step is a different job, done by [calendaring-sync](https://github.com/pycalendar/calendaring-sync).  The design is in [docs/design/API_DESIGN.md](docs/design/API_DESIGN.md).

The library is called `calendaring`: the same name on GitHub, on PyPI and as the import (see [the 0.3 decisions](docs/design/PRIOR_ART_AND_DECISIONS.md#d2-the-name)).  It was earlier called `calendaring-client`.  "Calendaring" is the IETF's word, and in RFC 5545 it covers tasks too, but few people know that, so the tagline above carries the message that this library supports tasks and not only events.

## Background

I've been the maintainer of the Python CalDAV client package for many years now.  The ironic thing is that I started using the library because I "didn't want to get my hands dirty".  I had to do some fixes for the library to work towards my server, and soon enough the owner of the project passed me the maintainer hat.  To be honest, I think neither the CalDAV protocol nor the iCalendar format is particuarly good standards.  They are sort of the only open standards we have, so it's important to support them, but it's also important to support new and emerging standards.

There exists no good standards for tasks.  There are some industry standards, except for that the iCalendar standard offers support for task lists, but it's not a very good standard for it.  For instance, it's not very clear how to add information about time estimates and time spent on a task.

## Mission statement

This is intended to be a general Python API for accessing task and calendaring information, regardless of where it's stored and how to access the backend.  In version 0.1.0 it won't be much more than a wrapper for the CalDAV library already - but it will provide a clean, general and well-documented interface, it will support a clean, general and well-documented configuration file, with the intention to support other backends and not only CalDAV servers.  In the future, support for JMAP, text files and other calendar-like or task-management software systems will be implemented.

### Personal motivation

* I feel that the combination of a lack of a good task list and "too many TODO-lists" has been a consistent pain for most of my life.
* I would like to have all relevant tasks and calendar events open in front of me when planning my days, weeks and months regardless of where the tasks are stored.
* I'd like to throw standardized attributes on my tasks and issues (like time spent, time estimate, priority, due-time) regardless of where they exist (this may involve synchronizing information from external systems with my own calendar/task store).  I do hope that this library can help me achieve this.
* I have the `plann`-utility which is a CLI/frontend for CalDAV servers - but I'd like to use it towards other systems as well.

## Existing systems

### icalcli

https://pypi.org/project/icalcli/ (source: https://github.com/jrvarma/icalcli, MIT license) is a command-line calendar manager forked from `gcalcli` (the Google Calendar CLI), decoupling the front-end from any specific backend via a pluggable backend interface.  It ships with a local `.ics` file backend and an EteSync/EteBase backend.  There is no CalDAV support and no HTTP iCal feed fetching.

Critically, it has **zero VTODO/task support** — it is built entirely around `VEVENT`.

The interface is a CLI (interactive REPL + subcommands for agenda/week/month views, CRUD, search).  It is not designed as a library API.

The project is actively maintained (last release 1.1.6; last commit 2026-09-10).

**Conclusion:** icalcli and this project are complementary rather than competing.  icalcli is a user-facing CLI tool for viewing and editing calendar *events* from local files; this project aims to be a multi-backend *library* with first-class task support.  The pluggable backend design in icalcli is worth studying for inspiration.

## Why it's important

Today one may have to choose different libraries for fetching calendaring information from different kinds of software.  I think it's important to have a "swiss army knife"-library for accessing calendars - one should not have to use different libraries for different calendar providers.  I've considered supporting other standards in the CalDAV library, but it's a bit outside the scope for that library - hence the need for another package.

## Backends to be supported

* CalDAV, certainly.  It's (IMO) not a very good standard, but it is sort of the only standard for distributing and updating calendaring information.
* Support for "iCalendar feeds": It has become quite normal to offer users access to calendar data through iCalendar links.  It's much simpler than CalDAV, but offers less features.  Already several years ago I got requests from people wanting support for iCalendar feeds in the CalDAV library.  I think in the end I rejected it for being outside the scope of the CalDAV library.  It should be very simple to detect if a link is an iCalendar feed.  I split out an icalendar-searcher library while refactoring the CalDAV library - with this library it's possible to do client-side filtering and have a search-function that behaves the same towards CalDAV servers and an iCalendar feed.
* Support for local data files (.ical or .ics).  Those can be edited too.  The library should simply offer an API that works the same, regardless of weather the client is used towards a remote calendar server or towards a local selection of ical-files.
* Many mail systems comes bundled with calendar systems, and can interoperate with other systems by sending iCal data as email attachments.  The library should support sending iCal data as email attachments.  Perhaps even support accessing mailboxes through JMAP and/or IMAP.
* I've been playing a bit with Mobilizon lately.  It's basically a federated server designed for spreading calendar event information.  It's quite high on my list to support bidirectional support for this software.
* There are lots of proprietary systems offering calendaring and/or task management, but without supporting standards very well.  I'm not keen on working with those systems, but I'd happily accept pull-request for supporting any of them.
* TODO: Research on Gitea, Vikunja, Kanboard, EteSync, Taskwarrior, OpenProject, GitLab, Emacs org-mode, Request Tracker and Simple Markdown files.  Some of those are neither calendaring system nor task list managers, but still frequently used for storing tasks/issues.
  * OpenProject looks a bit like some commercial solutions backed by heavy marketing investments slightly abusing the "open" keyword, abusing the .org domain space, and possibly just wanting to make quick money out of the EU digital sovereignity awakening,  but I may be very wrong - more research is certainly needed.
  * Focalboard was dropped from this list: its repository states that it is not maintained, and a Focalboard board has no fixed task schema at all - status, priority, dates and estimates are whatever properties the board author happened to create - so there would be no stable convention for a backend to map onto.


TODO: more research to be done.

## Relevant standards

* iCalendar - RFC 5545 with friends
* jCal - RFC 7265
* ActivityPub
* iTip - RFC 5546
* Emacs .org-mode

TODO: do more research to see if there are other standards

## Users

* I'm also developing plann - a CLI and a library for more advanced calendaring and task management.  It offers things that are out of the scope of both CalDAV and this library, including "panic planning" where it's trying to fit the tasks with the highest priority into the available time before the deadlines.  It's using the CalDAV library, but I would like to support more than only CalDAV servers.
* Over the last years quite many CalDAV issues has come in through users of HomeAssistant.  I would like to reach out to the HomeAssistant community and see if they have use for something like this (unless they already have implemented something internally).
* I think that any project that either reads iCalendar feeds or uses the CalDAV client library could have use for such a "swiss army knife".

## License

`calendaring` is dual-licensed: GPL-3.0-or-later ([`COPYING.GPL`](COPYING.GPL)) **or** Apache-2.0 ([`COPYING.APACHE`](COPYING.APACHE)), at your choice.

The JMAP backend will depend on `calendaring-jmap`, which is AGPL-3.0-or-later.  It is an optional extra (`calendaring[jmap]`).  A program that includes it must, as a whole, be conveyed under AGPL-3.0 terms; `calendaring`'s own code remains available under either licence.  The same holds for the hard dependency `icalendar-searcher` until its pending relicensing to match lands.
