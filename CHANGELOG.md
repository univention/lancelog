# Changelog

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
