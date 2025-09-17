# SPDX-License-Identifier: AGPL-3.0-only

import contextlib
from collections.abc import Generator

import pytest
from _pytest.logging import LogCaptureFixture
from loguru import logger


@pytest.fixture  # type:ignore
def caplog(caplog: LogCaptureFixture) -> Generator[LogCaptureFixture]:
    handler_id = logger.add(
        caplog.handler,
        format="{message}",
        level=0,
        filter=lambda record: record["level"].no >= caplog.handler.level,
        enqueue=False,  # Set to 'True' if your test is spawning child processes.
    )
    yield caplog
    with contextlib.suppress(ValueError):  # setup_logging() removed it
        logger.remove(handler_id)
