# SPDX-License-Identifier: AGPL-3.0-only

"""
Set up logging in their applications in an easy and consistent way.

from lancelog import setup_logging
setup_logging()
"""

from __future__ import annotations

import contextlib
import functools
import importlib.util
import inspect
import logging
import sys
from enum import Enum
from importlib.metadata import version
from typing import TextIO

import logfmter
import loguru


with contextlib.suppress(ImportError):
    import orjson
from loguru import logger


LOG_FORMAT_DEFAULT = (
    "{_syslog_prio}"
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS ZZ}</green> | "
    "<level>{level:>7}</level> | "
    "{module}.{function}:{line} | "
    "<level>{message}</level> | "
    "{extra[serialized]}"
)
LOG_FORMAT_JSON = "{message}"
# Add to this, if you wish to move a logger from DEBUG to TRACE:
LOG_LEVEL_DEBUG_TO_TRACE = {"http.client", "urllib3.connectionpool"}


class DataFormat(str, Enum):
    JSON = "JSON"
    LOGFMT = "LOGFMT"


class LogLevel(str, Enum):
    TRACE = "TRACE"
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


def setup_logging(
    sink: TextIO = sys.stdout,
    fmt: str | None = None,
    level: int | str | LogLevel = LogLevel.INFO,
    filter: dict[str, str | bool] | None = None,
    colorize: bool | None = None,
    data_format: DataFormat = DataFormat.LOGFMT,
    syslog_prio_prefix: bool = False,
) -> None:
    """
    Configure logging.

    :param sink: A "file-like object" like `sys.stdout` (default), `sys.stderr`,
        an opened file, or a file path as `str` or `pathlib.Path`.
    :param fmt: The template used to format logged messages. Does not apply for JSON output.
        Default is `lancelog.setup.LOG_FORMAT_DEFAULT`.
    :param level: Minimum severity level of messages to send to the sink. Default is "INFO".
    :param filter: Decide for each logged message whether it should be emitted or discarded.
        A mapping from module names to minimum required level.
        The closest parent will be searched in the dict.
        Values are levels (`int`|`str`) or a `bool` to emit or discard unconditionally.
    :param colorize: Whether to use color markups in messages.
        By default, enabled if `sink` is connected to a terminal.
    :param data_format: Encode messages to Logfmt (`DataFormat.LOGFMT`, default) or JSON (`DataFormat.JSON`).
    :param syslog_prio_prefix: Whether to prefix logs with a Syslog priority marker. Disabled by default.
        Syslog and Journald require this to detect the log level of a log message.
        Auto-disabled, if `sink` is connected to a terminal.
    :return: None
    """
    if data_format == DataFormat.JSON and not importlib.util.find_spec("orjson"):
        raise ImportError(
            "The 'orjson' library is required to serialize logs to JSON. Please run 'pip install lancelog[json]'."
        )

    logger.remove()
    sink_is_tty = hasattr(sink, "write") and sink.isatty()
    kwargs = {
        "sink": sink,
        "format": fmt if fmt is not None else LOG_FORMAT_JSON if data_format == DataFormat.JSON else LOG_FORMAT_DEFAULT,
        "level": level.value if isinstance(level, LogLevel) else level,
        "colorize": colorize if colorize is not None else data_format != DataFormat.JSON and sink_is_tty,
        "filter": filter,
        "serialize": data_format == DataFormat.JSON,
    }
    logger.add(**kwargs)
    logger.configure(
        patcher=functools.partial(
            _patching,
            data_format=data_format,
            syslog_prio_prefix=syslog_prio_prefix and not sink_is_tty,
        )
    )
    logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)
    logger.debug("Configured logging.", lancelog_version=version("lancelog"), config=kwargs, sink_is_tty=sink_is_tty)


def _log_level_to_syslog_priority(level: int) -> int:
    """Convert Python log level to Syslog priority."""
    if level < 20:  # < logging.INFO -> Priority Debug
        return 7
    elif level < 30:  # < logging.WARNING -> Priority Informational
        return 6
    elif level < 40:  # < logging.ERROR -> Priority Warning
        return 4
    elif level < 50:  # < logging.CRITICAL -> Priority Error
        return 3
    else:  # -> Priority Critical
        return 2


def _patching(record: loguru.Record, data_format: DataFormat, syslog_prio_prefix: bool) -> None:
    """
    Serialize log record to Logfmt or JSON,
    optionally add sd-daemon prefix with Syslog priority.
    """
    if data_format == DataFormat.JSON:
        record["extra"]["serialized"] = orjson.dumps(
            record["extra"],
            default=str,
            option=orjson.OPT_NON_STR_KEYS | orjson.OPT_SORT_KEYS,
        ).decode()
    elif data_format == DataFormat.LOGFMT:
        record["extra"]["serialized"] = logfmter.Logfmter.format_params(record["extra"])
    else:
        raise ValueError(f"Unsupported format in 'data_format': {data_format!r}")
    record["_syslog_prio"] = f"<{_log_level_to_syslog_priority(record['level'].no)}>" if syslog_prio_prefix else ""


class _InterceptHandler(logging.Handler):
    """
    Redirect standard Python logging to Loguru.

    Copied from https://loguru.readthedocs.io/en/stable/overview.html#entirely-compatible-with-standard-logging
    and added code to move logs from debug to trace.
    """

    def emit(self, record: logging.LogRecord) -> None:
        # Get corresponding Loguru level if it exists.
        level: str | int
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Move logs from debug to trace.
        if record.levelname == "DEBUG" and record.name in LOG_LEVEL_DEBUG_TO_TRACE:
            level = "TRACE"

        # Find caller from where originated the logged message.
        frame, depth = inspect.currentframe(), 0
        while frame and (depth == 0 or frame.f_code.co_filename == logging.__file__):
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())
