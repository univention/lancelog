# SPDX-License-Identifier: AGPL-3.0-only

import json
import logging
import re
import sys
from collections.abc import Callable, Generator
from typing import Any
from unittest.mock import patch

import pytest
from _pytest.capture import CaptureFixture

from lancelog import LOG_LEVEL_DEBUG_TO_TRACE, DataFormat, setup_logging
from lancelog.setup import ADR0010_REGEX


PY_LOGGER_NAME_1 = "test_abc"


@pytest.fixture  # type: ignore
def remove_logger_from_debug_to_trace() -> Generator[None, Any, None]:
    yield
    LOG_LEVEL_DEBUG_TO_TRACE.remove(PY_LOGGER_NAME_1)


def test_intercept_python_logging(capsys: CaptureFixture) -> None:
    setup_logging(sink=sys.stderr)
    py_logger = logging.getLogger(__name__)
    py_logger.info("Hello World")
    captured = capsys.readouterr()
    m = re.match(ADR0010_REGEX, captured.err)
    if not m:
        raise AssertionError("Regex matching of log record failed.")  # pragma: nocover
    parsed = m.groupdict()
    assert parsed["message"] == "Hello World"


def test_log_level_debug_to_trace(
    capsys: CaptureFixture, remove_logger_from_debug_to_trace: Callable[[], Generator[None, Any, None]]
) -> None:
    py_logger = logging.getLogger(PY_LOGGER_NAME_1)
    setup_logging(sink=sys.stderr, level="TRACE")
    py_logger.debug("Hello debug")
    captured = capsys.readouterr()
    m = re.match(ADR0010_REGEX, captured.err.split("\n")[1])
    if not m:
        raise AssertionError("Regex matching of log record failed.")  # pragma: nocover
    parsed = m.groupdict()
    assert parsed["message"] == "Hello debug"
    assert parsed["level"] == "DEBUG"

    LOG_LEVEL_DEBUG_TO_TRACE.add(PY_LOGGER_NAME_1)
    py_logger.debug("Hello trace")
    captured = capsys.readouterr()
    m = re.match(ADR0010_REGEX, captured.err)
    if not m:
        raise AssertionError("Regex matching of log record failed.")  # pragma: nocover
    parsed = m.groupdict()
    assert parsed["message"] == "Hello trace"
    assert parsed["level"] == "TRACE"


def test_handle_unknown_log_level(capsys: CaptureFixture) -> None:
    # register a new log level with Python logging
    DEBUG_LEVEL_NUM_INFOWARN = 25
    logging.addLevelName(DEBUG_LEVEL_NUM_INFOWARN, "INFOWARN")

    def infowarn(self, message: str, *args, **kws) -> None:  # type: ignore
        if self.isEnabledFor(DEBUG_LEVEL_NUM_INFOWARN):
            self._log(DEBUG_LEVEL_NUM_INFOWARN, message, args, **kws)

    logging.Logger.infowarn = infowarn  # type: ignore
    py_logger_name = "test_def"
    py_logger = logging.getLogger(py_logger_name)

    # test logfmt
    setup_logging(sink=sys.stderr, level="TRACE")
    py_logger.infowarn("Hello infowarn logfmt")  # type: ignore
    captured = capsys.readouterr()
    m = re.match(ADR0010_REGEX, captured.err.split("\n")[1])
    if not m:
        raise AssertionError("Regex matching of log record failed.")  # pragma: nocover
    parsed = m.groupdict()
    assert parsed["message"] == "Hello infowarn logfmt"
    assert parsed["level"] == "INFOWARN"

    # test json
    setup_logging(sink=sys.stderr, level="TRACE", data_format=DataFormat.JSON)
    py_logger.infowarn("Hello infowarn json")  # type: ignore
    captured = capsys.readouterr()
    line = captured.err.split("\n")[1]
    parsed2 = json.loads(line)
    assert parsed2["text"] == "Hello infowarn json\n"
    assert parsed2["record"]["message"] == "Hello infowarn json"
    assert parsed2["record"]["level"]["no"] == 25
    assert parsed2["record"]["level"]["name"] == "INFOWARN"


def test_intercept_source_points_to_caller(capsys: CaptureFixture) -> None:
    setup_logging(sink=sys.stderr)
    logging.getLogger(__name__).info("Hello")
    assert "_source=test_intercept_handler.test_intercept_source_points_to_caller:" in capsys.readouterr().err


def _logged_message(capsys: CaptureFixture, message: str) -> str:
    setup_logging(sink=sys.stderr)
    logging.getLogger(__name__).info(message)
    m = re.match(ADR0010_REGEX, capsys.readouterr().err)
    if not m:
        raise AssertionError("Regex matching of log record failed.")  # pragma: nocover
    return m.groupdict()["message"]


def test_intercept_strips_single_trailing_newline(capsys: CaptureFixture) -> None:
    assert _logged_message(capsys, "Exception in app\n") == "Exception in app"


def test_intercept_keeps_inner_newlines(capsys: CaptureFixture) -> None:
    assert _logged_message(capsys, "a\nb\n") == repr("a\nb")


def test_intercept_emit_failure_does_not_propagate(capsys: CaptureFixture) -> None:
    setup_logging(sink=sys.stderr)
    with patch("lancelog.setup._loguru_level", side_effect=RuntimeError("boom")):
        logging.getLogger(__name__).info("Hello")
    err = capsys.readouterr().err
    assert "--- Logging error ---" in err
    assert "RuntimeError: boom" in err


def test_unformattable_stdlib_record_logged_as_warning(capsys: CaptureFixture) -> None:
    setup_logging(sink=sys.stderr)
    logging.getLogger(__name__).info("%d", "x")
    err = capsys.readouterr().err
    m = re.match(ADR0010_REGEX, err)
    if not m:
        raise AssertionError("Regex matching of log record failed.")  # pragma: nocover
    parsed = m.groupdict()
    assert parsed["level"] == "WARNING"
    assert parsed["message"] == "Could not format log record."
    for token in (
        "msg='%d'",
        "args=('x',)",
        'error="TypeError(',
        "_source=test_intercept_handler.test_unformattable_stdlib_record_logged_as_warning:",
    ):
        assert token in parsed["data"]
    assert "Logging error" not in err


def test_unformattable_stdlib_record_keeps_traceback(capsys: CaptureFixture) -> None:
    setup_logging(sink=sys.stderr, data_format=DataFormat.JSON)
    try:
        raise ZeroDivisionError("zero")
    except ZeroDivisionError:
        logging.getLogger(__name__).exception("%d", "x")
    extra = json.loads(capsys.readouterr().err)["record"]["extra"]
    assert "ZeroDivisionError" in extra["_traceback"]


def test_intercept_strips_multiple_trailing_newlines(capsys: CaptureFixture) -> None:
    assert _logged_message(capsys, "a\n\n") == "a"


def test_intercept_keeps_newline_only_message(capsys: CaptureFixture) -> None:
    assert _logged_message(capsys, "\n") == repr("\n")
