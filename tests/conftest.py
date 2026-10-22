# SPDX-License-Identifier: AGPL-3.0-only

import logging
from collections.abc import Generator
from typing import Any

import pytest


UVICORN_LOGGERS = ("uvicorn", "uvicorn.access", "uvicorn.asgi", "uvicorn.error")


@pytest.fixture(autouse=True)  # type: ignore
def reset_uvicorn_loggers() -> Generator[None, Any, None]:
    yield
    for name in UVICORN_LOGGERS:
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True
        lg.setLevel(logging.NOTSET)
