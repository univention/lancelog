# Changelog

## [0.3.0] - 2025-09-15

- feat: use Loguru's JSON serialization code, remove orjson requirement

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
