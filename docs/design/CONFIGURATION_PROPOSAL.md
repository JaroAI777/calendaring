# One configuration file for caldav, calendaring-jmap and calendaring

**Status:** proposal for the pycal team, drafted by Claude Opus 5.5 on
2026-10-09. Nothing is decided.
**Feeds:** [roadmap 1.4](ROADMAP.md#14-configuration-and-credentials) and
[API spec §10, Q8](API_DESIGN.md#10-open-questions)

## The problem

A user with a CalDAV server, a JMAP account and a directory of `.ics` files
should describe them once. Today:

| | File | Format | Env vars |
|---|---|---|---|
| `caldav` | `~/.config/caldav/calendar.{conf,yaml,json}`, `/etc/caldav/…`, `CALDAV_CONFIG_FILE` | sections with `caldav_*` keys; `inherits`, `contains` (meta-sections), globs, `disable`, `${VAR}` expansion, `features:` profile | `CALDAV_*` |
| `calendaring-jmap` | `~/.config/calendaring-jmap/calendar.yaml`, `JMAP_CONFIG_FILE` | one flat mapping: `url`, `username`, `password`, `auth_type`, `timeout` | `JMAP_*` |
| `calendaring` | none yet (roadmap 1.4) | — | — |
| `plann` | caldav's file and parser (the parser started in plann and moved to caldav) | | |

Requirements:

1. One file covers every backend.
2. Each library works on its own: `caldav` must not need `calendaring` or
   `calendaring-jmap` installed, and vice versa.
3. No dependency cycles.
4. Existing caldav config files keep working unchanged.
5. Secrets never have to be stored in plain text (plann
   [issue #49](https://github.com/pycalendar/plann/issues/49) leaves
   credential storage to this library).

## The format: extend caldav's, don't invent a new one

caldav's format already has everything structural that a multi-backend
file needs: named sections, `inherits`, meta-sections with `contains`,
globs, `disable` and environment expansion. Its connection keys carry a
prefix (`caldav_url`). The proposal is to use the same prefix convention
for the other backends:

```yaml
work:
  caldav_url: https://dav.example.com/
  caldav_username: alice
  caldav_password_command: pass show dav/work      # new: secret from a command
  features: nextcloud

fastmail:
  jmap_url: https://api.fastmail.com/jmap/session
  jmap_username: alice@example.com
  jmap_password_keyring: fastmail                  # new: secret from the OS keyring

notes:
  files_path: ~/calendars/notes                    # vdir or single .ics

holidays:
  feed_url: webcal://example.com/holidays.ics

issues:
  gitea_url: https://gitea.example.com/
  gitea_token: ${GITEA_TOKEN}

everything:
  contains: [work, fastmail, notes, holidays, issues]
```

- **The prefix says which backend a section is for.** An explicit
  `backend:` key settles the rare section that has more than one prefix.
- **caldav already ignores the other backends' sections**, with one
  exception. Its parser skips a section that has neither a `caldav_url`
  nor a `features` key (`extract_conn_params_from_section` returns `None`,
  checked on 2026-10-09), so `jmap_*`, `files_*` and `gitea_*` sections are
  invisible to it. The exception: a non-CalDAV section with a `features:`
  key would be picked up, so `features` must stay CalDAV-only, or caldav
  must learn to check the prefix.
- **caldav does need two changes:**
  - It must understand the new secret keys below. Today it keeps only the
    keys in its `CONNKEYS` list, so `caldav_password_command` would be
    dropped silently, and caldav would connect with no password.
  - It must search the new file path (question 2 below).

  Under option B both land in caldav's parser; under option C they come
  with the shared package.
- **Secrets:** every `<prefix>_password` (or `_token`) can also come from
  `<prefix>_password_command` (the command's stdout, the way vdirsyncer's
  `fetch = ["command", …]` works) or `<prefix>_password_keyring` (looked
  up with `keyring`, an optional extra). `${VAR}` expansion stays as it is.

## Where the parser lives

The generic part of caldav's `config.py` (section expansion, `inherits`,
env expansion, file discovery) is about 150 of its 650 lines. The rest is
caldav-specific: `caldav_*` keys, feature profiles, and test-server
handling.

| Option | Packages | Duplication | Follows D1? | calendaring-jmap reads the full format standalone? |
|---|---|---|---|---|
| **A.** Each library keeps or copies its own parser | 0 new | 3 copies that drift | yes | yes, by copying |
| **B.** `calendaring` imports caldav's generic functions; calendaring-jmap keeps its simple flat loader | 0 new | none | not quite: generic code stays in caldav | **no**, only through calendaring |
| **C.** A small package (say `pycal-config`) holding the generic parser and the secrets lookup, used by all three | 1 new | none | yes | yes |
| **D.** Move the parser into `calendaring` and have caldav import it | 0 new | none | yes | — **rejected**: caldav would depend on calendaring, and calendaring on caldav, a cycle |

[D1](PRIOR_ART_AND_DECISIONS.md#d1-packaging-principle) is the principle
that logic which isn't CalDAV logic doesn't belong in `caldav`.

**Proposal: B now, and C only if the team wants calendaring-jmap to read
the shared file without calendaring.** B costs nothing today:
`calendaring` already depends on `caldav`, and caldav's parser is public
(`expand_config_section`, `config_section`, `read_config`,
`expand_env_vars`). What makes a later move to C cheap is that **the format
is documented once, as a specification**, not defined by whichever parser
reads it. If the code later moves from caldav into a package, no user's
file changes.

The number of packages matters. Every new one is another release, CI
setup and version matrix for the same few people. C is worth that only if
someone uses calendaring-jmap without calendaring and wants more than
one flat section.

## Questions for the team

1. Should `calendaring-jmap` on its own read the shared file, with
   meta-sections and inheritance? If yes, go with C; if no, B.
2. The file name. caldav's `~/.config/caldav/calendar.conf` is an odd home
   for JMAP and Gitea sections. **Proposal:** `~/.config/pycal/calendar.yaml`
   as the first place searched, with caldav's current paths still read as
   fallbacks, so that nobody has to move a file. caldav does not search
   `~/.config/pycal/` today, so this is one of the two caldav changes
   above.
3. Are `_password_command` and `_password_keyring` the right two
   mechanisms, or is one of them enough?

---

*Drafted with AI assistance (Claude Opus 5.5 via Claude Code) from the
source of `caldav` (`caldav/config.py`, `docs/source/configfile.rst`) and
`calendaring-jmap` (`_config.py`) as checked out on 2026-10-09.*
