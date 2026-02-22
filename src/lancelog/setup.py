# SPDX-License-Identifier: AGPL-3.0-only

"""
Set up logging in their applications in an easy and consistent way.

from lancelog import setup_logging
setup_logging()
"""

from __future__ import annotations

import functools
import inspect
import logging
import os
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from importlib.metadata import version
from typing import TextIO

import logfmter
import loguru
from loguru import logger
from loguru._better_exceptions import ExceptionFormatter


# Regular expression that matches a log record of LOG_FORMAT_DEFAULT:
ADR0010_REGEX = (
    r"^(?P<date>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3,6}\+\d{2}:?\d{2}) +"
    r"(?P<level>\w+?) +\[(?P<request_id>.*?)\] (?P<message>.+?)\t\| (?P<data>.*?)$"
)
LOG_FORMAT_DEFAULT = (
    "{_syslog_prio}"
    "<green>{time:YYYY-MM-DDTHH:mm:ss.SSSZ}</green> "
    "<level>{level:<8}</level> "
    "[{request_id:>10}] "
    "<level>{message}</level>\t| "
    "{extra[serialized]}"
)
# Regular expression that matches a log record before ADR 0010 / version 0.3.0:
PRE030_REGEX = (
    r"^(?P<date>\d\d\d\d-\d\d-\d\d \d\d:\d\d:\d\d.\d\d\d \+\d\d\d\d) \| +"
    r"(?P<level>\w+?) \| (?P<source>.+?) \| (?P<message>.+?) \| (?P<data>.*?)$"
)
# Log format before ADR 0010 / version 0.3.0:
LOG_FORMAT_PRE_v030 = (
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
REQUEST_ID_LENGTH = 10  # request IDs will be shortened to this length

_exc_formatter = ExceptionFormatter(backtrace=True, diagnose=True, colorize=False)


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


@dataclass
class DataSectionOptions:
    log_level: bool = False  # value: str ("INFO")
    request_id: bool = False  # value: str ("9f5ea85512")
    process_id: bool = False  # value: int (165163)
    source_ref_key: str = "_source"  # name of field for source of log record, value: "{module}.{function}:{line}"
    time: bool = False  # ISO8601 format, value: str
    timestamp: bool = False  # UNIX timestamp, value: float
    thread_id: bool = False  # value: int
    thread_name: bool = False  # value: str


_DEFAULT_DATA_OPTIONS = DataSectionOptions()


def setup_logging(
    sink: TextIO | str | os.PathLike[str] = sys.stdout,
    fmt: str | None = None,
    level: int | str | LogLevel = LogLevel.INFO,
    filter: dict[str, str | bool] | None = None,
    colorize: bool | None = None,
    data_format: DataFormat = DataFormat.LOGFMT,
    syslog_prio_prefix: bool = False,
    enqueue: bool = False,
    request_id_func: Callable[[], str] | None = None,
    data_options: DataSectionOptions | None = None,
) -> None:
    """
    Configure logging.

    :param sink: A "file-like object" like `sys.stdout` (default), `sys.stderr`,
        an opened file, or a file path as `str` or `pathlib.Path`.
    :param fmt: The template used to format logged messages. Does not apply for JSON output.
        The default format (`lancelog.LOG_FORMAT_DEFAULT`) in version 0.3.0 conforms to ADR 0010.
        If you wish to use the previous format, set `fmt` to `lancelog.LOG_FORMAT_PRE_v030`.
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
    :param bool enqueue: Whether log messages should be sent through a multiprocessing-safe queue to the sink.
        Useful when logging concurrently from multiple processes or threads. Makes logging calls non-blocking.
        Disabled by default.
    :param Callable request_id_func: Function that returns a request ID string to be added to each log record.
        If omitted, the request ID will be `-`.
    :param DataSectionOptions data_options: Optional values added to the data section of every log record.
        When writing JSON, values will be in to the `extra` field.
    :return: None
    """
    sink_is_tty = not isinstance(sink, str | os.PathLike) and sink.isatty()
    if data_format not in (DataFormat.JSON, DataFormat.LOGFMT):
        raise ValueError(f"Unsupported 'data_format' setting: {data_format!r}")
    if fmt is not None:
        record_fmt = fmt
    elif data_format == DataFormat.JSON:
        record_fmt = LOG_FORMAT_JSON
    else:
        record_fmt = LOG_FORMAT_DEFAULT
    kwargs = {
        "sink": sink,
        "format": record_fmt,
        "level": level.value if isinstance(level, LogLevel) else level,
        "colorize": colorize if colorize is not None else data_format != DataFormat.JSON and sink_is_tty,
        "filter": filter,
        "serialize": data_format == DataFormat.JSON,
        "enqueue": enqueue,
    }
    logger.remove()
    logger.add(**kwargs)
    logger.configure(
        patcher=functools.partial(
            _patching,
            data_format=data_format,
            data_options=data_options,
            fmt=record_fmt,
            request_id_func=request_id_func,
            syslog_prio_prefix=syslog_prio_prefix and not sink_is_tty,
        )
    )
    logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)
    logger.debug(
        "Configured logging.",
        version=version("lancelog"),
        **{k: v if isinstance(v, str) else repr(v) for k, v in kwargs.items()},
        data_format=data_format.value,
        syslog_prio_prefix=syslog_prio_prefix,
        request_id_func=repr(request_id_func),
        data_options=repr(data_options),
        LOG_LEVEL_DEBUG_TO_TRACE=sorted(LOG_LEVEL_DEBUG_TO_TRACE),
    )


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


def _patching(
    record: loguru.Record,
    data_format: DataFormat,
    data_options: DataSectionOptions | None,
    fmt: str,
    request_id_func: Callable[[], str] | None,
    syslog_prio_prefix: bool,
) -> None:
    """
    Serialize log record to Logfmt or JSON, add optional data.
    Escape pipe and new lines in message.
    """
    if record["level"].name.startswith("Level "):
        # Unknown log level, check if Python logging knows it. It'll fall back to "Level <int>".
        record["level"].name = logging.getLevelName(record["level"].no).replace(" ", "")
    if "\n" in record["message"]:
        record["message"] = repr(record["message"])
    if "\t| " in record["message"]:
        record["message"] = record["message"].replace("\t| ", "\t\| ")  # noqa: W605
    if record["exception"] and all(record["exception"]):
        record["extra"]["_traceback"] = "".join(_exc_formatter.format_exception(*record["exception"]))
    data_options = data_options or DataSectionOptions()
    request_id = request_id_func() if request_id_func else "-"
    if data_options.log_level:
        record["extra"]["log_level"] = record["level"].name
    if data_options.request_id:
        record["extra"]["request_id"] = request_id
    if data_options.process_id:
        record["extra"]["process_id"] = os.getpid()
    if data_options.time:
        record["extra"]["time"] = record["time"].isoformat()
    if data_options.timestamp:
        record["extra"]["timestamp"] = record["time"].timestamp()
    if data_options.thread_id:
        record["extra"]["thread_id"] = threading.current_thread().ident
    if data_options.thread_name:
        record["extra"]["thread_name"] = threading.current_thread().name

    if data_format == DataFormat.JSON:
        record["extra"]["request_id"] = request_id
    elif data_format == DataFormat.LOGFMT:
        if fmt != LOG_FORMAT_PRE_v030:
            record["request_id"] = request_id[:REQUEST_ID_LENGTH]
            record["extra"][data_options.source_ref_key] = f"{record['module']}.{record['function']}:{record['line']}"
        record["extra"]["serialized"] = logfmter.Logfmter.format_params(record["extra"])
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
