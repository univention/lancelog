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
    setup_logging,
)


__all__ = [
    "__version__",
    "LOG_FORMAT_DEFAULT",
    "LOG_FORMAT_PRE_v030",
    "LOG_LEVEL_DEBUG_TO_TRACE",
    "DataFormat",
    "DataSectionOptions",
    "LogLevel",
    "logger",
    "setup_logging",
]
