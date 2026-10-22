# Changelog

## [0.4.0] - 2026-10-22

- fix: strip trailing newlines from intercepted stdlib log messages.
  Such messages (for example uvicorn's `Exception in ASGI application\n`) were rendered `repr()`-quoted
  and now render bare; update log pipeline rules that match the quoted form.
- fix: log a stdlib record whose message cannot be formatted as `WARNING` `Could not format log record.`
  instead of raising into the application

## [0.3.4] - 2026-05-20

- fix: request_id_func returning non-string crashes logging with TypeError
- fix: DeprecationWarning: invalid escape sequence

## [0.3.3] - 2026-02-22

- feat: export `__version__` in the public API
- refactor: allocate `DataSectionOptions` default once
- fix: broaden `sink` type hint in `setup_logging()`
- ci: switch pre-commit runner from `pre-commit` to `prek`
- chore: add `CLAUDE.md` with development guidance for Claude Code
- update SonarCube badges in README.md

## [0.3.2] - 2025-11-28

- fix: bump logfmter to a version that fixes escaping

## [0.3.1] - 2025-09-24

- fix: export 'logger' from 'loguru' to simplify imports
- test: fix side effect of test
- ci: build Debian package

## [0.3.0] - 2025-09-15

- feat: adapt format of log record to updated ADR:
  [0010 Log Format](https://git.knut.univention.de/univention/decision-records/-/blob/main/dev/0010-log-messages.md)
  ([univention/dev/libraries/lancelog#3](https://git.knut.univention.de/univention/dev/libraries/lancelog/-/issues/3))
  - BREAKING CHANGE: While Lancelog's API didn't change, the modification of
    the output format can be a breaking change for those that parse log files.
    To revert to the old format set `fmt` to `lancelog.LOG_FORMAT_PRE_v030`.
- feat: use Loguru's JSON serialization code, remove orjson requirement
- feat: provide options to enrich data section
  ([univention/dev/libraries/lancelog#4](https://git.knut.univention.de/univention/dev/libraries/lancelog/-/issues/4))
- feat: when calling `logger.exception()`, traceback are both printed as multi-line text and added to data in field `_traceback`
- test: add tests for all use cases and full coverage

## [0.2.3] - 2025-05-30

- fix: switch project to src-layout, add tests

## [0.2.2] - 2025-05-28

- fix(Debian build): fix package metadata using pybuild with plugin pybuild-plugin-pyproject
- fix(Debian dependencies): logfmter is required, orjson is optional

## [0.2.0] - 2025-03-20

- feat: add `py.typed` marker file, so library user's mypy uses it

## [0.1.1] - 2025-03-19

- fix: load `orjson` only when required (univention/dev/libraries/lancelog#1)

## [0.1.0] - 2025-03-18

:seedling: Initial release.
