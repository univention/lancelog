# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Lancelog** is a Python library that provides easy, consistent logging setup for applications. It wraps [Loguru](https://github.com/Delgan/loguru) and intercepts Python's standard `logging` module, redirecting all log records into Loguru for unified handling. It implements Univention's ADRs for logging (ADR 0004, 0005, 0007, 0008, 0010).

## Commands

This project uses [uv](https://docs.astral.sh/uv/) as the package manager.

```bash
# Install dependencies (including test extras)
uv sync --all-extras

# Run all tests with coverage
uv run pytest

# Run a single test file
uv run pytest tests/test_setup.py

# Run a single test by name
uv run pytest tests/test_setup.py::test_function_name -k "keyword"

# Linting and formatting
uv run ruff check --fix src/ tests/
uv run ruff format src/ tests/

# Type checking
uv run mypy src/ tests/

# Run pre-commit hooks manually
pre-commit run --all-files
```

Tests require **100% coverage** (`--cov-fail-under=100`); any new code must be covered.

## Architecture

The library is intentionally minimal — roughly 260 lines of core code in two source files:

### `src/lancelog/setup.py`

Contains everything:

- **`setup_logging()`** — the main public function. Configures Loguru (removes default handler, adds a new one with custom format/sink/level), installs `_InterceptHandler` on the root Python logger, and applies `_patching()` as a Loguru record patcher.

- **`_InterceptHandler(logging.Handler)`** — redirects Python `logging` calls into Loguru. Handles custom log levels, and moves `DEBUG` logs from libraries listed in `LOG_LEVEL_DEBUG_TO_TRACE` down to `TRACE` to suppress noisy sensitive output.

- **`_patching(record)`** — a Loguru patcher function applied to every log record. It serializes structured data fields to Logfmt or JSON, escapes special characters (newlines → `\n`, pipes → `|`), optionally prepends a Syslog priority prefix, and formats tracebacks.

- **`DataFormat(Enum)`** — `JSON` or `LOGFMT` (default).
- **`LogLevel(Enum)`** — `TRACE`, `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`.
- **`DataSectionOptions(dataclass)`** — controls which extra fields are appended to log lines (hostname, PID, thread, module, etc.).

### `src/lancelog/__init__.py`

Public API surface: exports `setup_logging`, `logger`, `LogLevel`, `DataFormat`, `DataSectionOptions`, `LOG_FORMAT_DEFAULT`, `LOG_FORMAT_PRE_v030`, `LOG_LEVEL_DEBUG_TO_TRACE`.

### Log Format

Default format (ADR 0010, Logfmt):

```text
<SYSLOG_PRI>timestamp | level | logger | message | key=value key=value ...
```

The `_patching` function serializes the `extra` dict (populated via `logger.bind(key=value)` or `with logger.contextualize(key=value)`) into the data section. Exceptions are serialized inline.

## Key Design Details

- **Structured logging**: message text and data fields are separate. Use `logger.bind(key=val).info("event")` or `with logger.contextualize(key=val): logger.info("event")` rather than interpolating data into the message string.
- **Request IDs** are truncated to 10 characters when added via `DataSectionOptions`.
- **`LOG_LEVEL_DEBUG_TO_TRACE`** is a `frozenset` of logger names whose `DEBUG` records are demoted to `TRACE`. Add library names there to suppress noisy libraries without silencing them entirely.
- Legacy format (`LOG_FORMAT_PRE_v030`) is preserved for backward compatibility but the ADR 0010 format is the default since v0.3.0.

## Code Style

- **Line length**: 120 characters (Ruff).
- **Type checking**: MyPy in `strict` mode — all new code must be fully typed.
- **Commit messages**: conventional commits (`fix:`, `feat:`, `ci:`, `test:`, `chore:`).
- **Python version**: 3.11 minimum (`.python-version` file pins dev to 3.11).
