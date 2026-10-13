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
make dev
pre-commit install --hook-type pre-commit --hook-type pre-push --hook-type commit-msg
make test
```

## Commit messages

Please follow [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) and write messages in the imperative mood:

- `fix: correct time-range search handling for recurring events`
- `feat: add new check`
- `docs: update README`

Rather than:

- `This commit fixes the time-range search`
- `Added new check`
