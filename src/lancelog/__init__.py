# SPDX-License-Identifier: AGPL-3.0-only

from importlib.metadata import version as _version

from loguru import logger


__version__: str = _version("lancelog")

from .setup import (
    LOG_FORMAT_DEFAULT,
    LOG_LEVEL_DEBUG_TO_TRACE,
    DataFormat,
    DataSectionOptions,
    LOG_FORMAT_PRE_v030,
    LogLevel,
    OtelOptions,
    setup_logging,
)
from .uvicorn_log import RequestTimingMiddleware, uvicorn_log_config


__all__ = [
    "__version__",
    "LOG_FORMAT_DEFAULT",
    "LOG_FORMAT_PRE_v030",
    "LOG_LEVEL_DEBUG_TO_TRACE",
    "DataFormat",
    "DataSectionOptions",
    "LogLevel",
    "OtelOptions",
    "RequestTimingMiddleware",
    "logger",
    "setup_logging",
    "uvicorn_log_config",
]
