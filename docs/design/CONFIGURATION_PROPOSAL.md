# One configuration file for caldav, calendaring-jmap and calendaring

**Status:** proposal for the pycal team, drafted by Claude Opus 5.5 on
2026-10-09. The format (F2 below) was chosen on 2026-10-14 by the author
and Sashank, in [PR 6](https://github.com/pycalendar/calendaring/pull/6).
Where the parser lives is not decided: option B is proposed, and has
Sashank's support.

**Feeds:** [roadmap 1.4](ROADMAP.md#14-configuration-and-credentials) and
[API spec §10, Q8](API_DESIGN.md#10-open-questions)

## The problem

A user with a CalDAV server, a JMAP account, a directory of `.ics` files
and a GitHub repository should describe them once. Today:

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
4. Existing caldav config files should keep working unchanged. This is
   wanted rather than required: a migration path would do, but there is
   no reason to break files that work.
5. Secrets never have to be stored in plain text (plann
   [issue #49](https://github.com/pycalendar/plann/issues/49) leaves
   credential storage to this library).

## The format (F2): a `kind` key, with the keys `Backend.connect()` takes

The structure is caldav's: named sections, `inherits`, meta-sections with
`contains`, globs, `disable` and `${VAR}` expansion. A section is the
keyword arguments of [`Backend.connect()`](API_DESIGN.md#22-backend):
`url`, `username`, `password`, `token`, `kind` and backend-specific
options.

```yaml
work:
  kind: caldav
  url: https://dav.example.com/
  username: alice
  password_command: [pass, show, dav/work]

fastmail:
  kind: jmap
  url: https://api.fastmail.com/jmap/session
  username: alice@example.com
  password_keyring: fastmail

notes:
  url: ~/calendars/notes             # a path means kind: files

holidays:
  url: webcal://example.com/holidays.ics     # webcal: means kind: feed

issues:
  kind: github                       # trackers cannot be detected
  repo: pycalendar/calendaring
  token_command: [gh, auth, token]

legacy:                              # an old caldav section, read unchanged
  caldav_url: https://old.example.com/
  caldav_user: alice

everything:
  contains: [work, fastmail, notes, holidays, issues, legacy]
```

- `kind` follows the rules `connect()` already has: a path or `file:`
  means files, `webcal:` means a feed, `http(s):` is probed for feed or
  CalDAV, and JMAP and the trackers must say `kind:`. Once the secret keys
  are resolved and the structural keys (`contains`, `inherits`, `disable`)
  are removed, `Workspace.from_config()` calls `connect(**section)` for
  each section, so new-style key names need no mapping. One gap: `connect()`
  takes `url` as a required argument, and a GitHub section needs none
  (`api.github.com` is the default). API_DESIGN §2.2 has to make `url`
  optional for such kinds.
- A section with `caldav_*` keys is read as `kind: caldav`, so existing
  files work in `calendaring` unchanged. `features` is the one key both
  styles share (once caldav knows `kind`, see below); any other mix of prefixed and unprefixed keys in one
  section is an error.
- calendaring-jmap's current file (`url`, `username`, `password`,
  `auth_type`, `timeout`) keeps every key name. Its users move to the
  shared file by indenting theirs under a section name and adding
  `kind: jmap`, since nothing probes for JMAP.
- A plugin backend registers a `kind` name, and its options are its own
  keys; nothing needs a prefix.
- Environment variables (`CALDAV_*`, `JMAP_*`) still win over the file,
  as they do in both libraries today.
- The cost: caldav reads only `caldav_*` sections. For caldav (and plann)
  to use the new-style `work` section above, caldav's parser must learn
  `kind: caldav` with unprefixed keys. That is about ten lines in
  `extract_conn_params_from_section`, but it is a caldav change, and until
  it ships, a user who wants plann to see a server keeps that section in
  the old style. Also, caldav's `*` expands to every section, and caldav
  picks up any section with a `features:` key, ignoring an unprefixed
  `url`. So a new-style section must not use `features` until caldav
  knows `kind` (see below).

### Alternatives considered

- **F1, backend prefixes** (`jmap_url`, `github_repo`, …, the way
  `caldav_url` is written): no format change in caldav, but every
  calendaring-jmap key gets renamed, and plugins compete for prefixes.
- **F3, a new TOML file** that `include`s caldav's: no PyYAML and no
  change to caldav's format, but two files, caldav and plann never see the
  new sections, and it is the fifth format roadmap 1.4 says to avoid.

### Compatibility with caldav

Checked against caldav `master` (`e7b5b1d8`) on 2026-10-11:

- **caldav ignores sections it cannot use, with one exception.**
  `extract_conn_params_from_section` returns `None` for a section with
  neither a `caldav_url` nor a `features` key, so `kind:` sections are
  skipped, even when `*` expands to them. The exception: a non-CalDAV
  section with a `features:` key would be picked up, so `features` must
  stay CalDAV-only, or caldav must learn to check `kind`.
- **`inherits` leaks across backends.** `config_section` merges the parent
  section's keys in, so a JMAP section that inherits from a section with a
  `caldav_url` (to share a username, say) looks like CalDAV to caldav.
  The shared parser should refuse inheritance between different kinds.
- **caldav needs two more changes:**
  - It must understand the new secret keys below. Today it keeps only the
    keys in its `CONNKEYS` list, so `caldav_password_command` would be
    dropped silently, and caldav would connect with no password.
  - It must search the new file path (question 2 below).

  Under parser option B below both land in caldav's parser; under
  parser option C they come with the shared package.
- **caldav parses YAML with `yaml.Loader`**, which builds arbitrary Python
  objects from tags (`caldav/config.py`, line 108). A file shared by
  several tools, and searched in `/etc/`, should be read with
  `yaml.safe_load`.

### Secrets

Every password (or token) can also come from `password_command` (the
command's stdout, the way vdirsyncer's `fetch = ["command", …]` works) or
`password_keyring` (looked up with `keyring`, an optional extra), spelled
`caldav_password_command` and so on in an old-style section. `${VAR}`
expansion stays as it is. GitHub is the case that shows `_command` earns
its place: `gh auth token` hands over the token the user already has, and
no keyring setup is needed.

- A `_command` is a list of the program and its arguments, run without a
  shell, so there is no quoting to get wrong.
- A file that holds a `_command` is refused if anyone but the user or root
  can write to it, the way ssh treats its config. caldav also reads
  `/etc/calendar.conf`, so the check belongs in the specification, not
  only in one parser.

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

Option B leaves calendaring-jmap close to reading the shared file on its
own: its flat loader already reads one section, so pointing it at a named
section is all it needs for files without `inherits` or meta-sections.

The number of packages matters. Every new one is another release, CI
setup and version matrix for the same few people. C is worth that only if
someone uses calendaring-jmap without calendaring and wants more than
one flat section.

## Questions for the team

1. Should `calendaring-jmap` on its own read the shared file, with
   meta-sections and inheritance? If yes, go with parser option C; if no, B.
   Sashank: no, so B; calendaring passes the section to calendaring-jmap,
   which keeps its simple file for standalone use.
2. The file name. caldav's `~/.config/caldav/calendar.conf` is an odd home
   for JMAP and GitHub sections. **Proposal:** `~/.config/pycal/calendar.yaml`
   as the first place searched, with caldav's current paths still read as
   fallbacks, so that nobody has to move a file. caldav does not search
   `~/.config/pycal/` today, so this is one of the caldav changes above.
3. Are `_password_command` and `_password_keyring` the right two
   mechanisms, or is one of them enough?

---

*Drafted with AI assistance (Claude Opus 5.5 via Claude Code) from the
source of `caldav` (`caldav/config.py`, `docs/source/configfile.rst`) and
`calendaring-jmap` (`_config.py`) as checked out on 2026-10-09, rechecked on 2026-10-11 (caldav `e7b5b1d8`,
calendaring-jmap `004c6b4`).*
