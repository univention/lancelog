# SPDX-License-Identifier: AGPL-3.0-only

import dataclasses
import datetime
import json
import os
import re
import sys
import threading
from importlib.metadata import version

import pytest
from _pytest.capture import CaptureFixture
from loguru import logger

from lancelog import LOG_FORMAT_DEFAULT, DataFormat, DataSectionOptions, LOG_FORMAT_PRE_v030, setup_logging
from lancelog.setup import ADR0010_REGEX, PRE030_REGEX, _log_level_to_syslog_priority


def test_debug_version_msg(capsys: CaptureFixture) -> None:
    # have to use stderr, because of https://github.com/pytest-dev/pytest/issues/5997
    setup_logging(sink=sys.stderr, level="DEBUG")
    captured = capsys.readouterr()
    assert "Configured logging" in captured.err
    assert f"version={version('lancelog')}" in captured.err
    assert f'format="{LOG_FORMAT_DEFAULT}"' in captured.err
    assert "colorize=False" in captured.err
    assert "filter=None" in captured.err
    assert "serialize=False" in captured.err
    assert "enqueue=False" in captured.err
    assert "data_format=LOGFMT" in captured.err
    assert "syslog_prio_prefix=false" in captured.err
    assert "request_id_func=None" in captured.err
    assert "data_options=None" in captured.err
    assert "sink_is_tty=false" in captured.err
    assert "_source=setup.setup_logging:" in captured.err


def test_log_level_to_syslog_priority() -> None:
    level2syslog = {
        1000: 2,
        60: 2,
        50: 2,
    }
    level2syslog.update(dict.fromkeys(range(40, 50), 3))
    level2syslog.update(dict.fromkeys(range(30, 40), 4))
    level2syslog.update(dict.fromkeys(range(20, 30), 6))
    level2syslog.update(dict.fromkeys(range(-2, 20), 7))

    for k, v in level2syslog.items():
        assert _log_level_to_syslog_priority(k) == v


def test_adr0010_format(capsys: CaptureFixture) -> None:
    setup_logging(sink=sys.stderr)
    logger.info("Hello", target="World")
    captured = capsys.readouterr()
    m = re.match(ADR0010_REGEX, captured.err)
    if not m:
        raise AssertionError("Regex matching of log record failed.")  # pragma: nocover
    parsed = m.groupdict()
    dt = datetime.datetime.fromisoformat(parsed["date"])
    assert datetime.datetime.now(tz=datetime.UTC) - dt < datetime.timedelta(seconds=1)
    assert parsed["level"] == "INFO"
    assert parsed["request_id"] == f"{'-':>10}"
    assert parsed["message"] == "Hello"
    assert "target=World" in parsed["data"].split()
    assert "_source=test_setup.test_adr0010_format:" in parsed["data"]


def test_adr0010_format_newline_escape(capsys: CaptureFixture) -> None:
    setup_logging(sink=sys.stderr)
    logger.info("Hello \n new", target="World")
    captured = capsys.readouterr()
    m = re.match(ADR0010_REGEX, captured.err)
    if not m:
        raise AssertionError("Regex matching of log record failed.")  # pragma: nocover
    parsed = m.groupdict()
    assert parsed["message"] == "'Hello \\n new'"
    assert "target=World" in parsed["data"].split()
    assert "_source=test_setup.test_adr0010_format_newline_escape:" in parsed["data"]


def test_adr0010_format_pipe_escape(capsys: CaptureFixture) -> None:
    setup_logging(sink=sys.stderr)
    logger.info("Hello\t| foo=bar", target="World")
    captured = capsys.readouterr()
    m = re.match(ADR0010_REGEX, captured.err)
    if not m:
        raise AssertionError("Regex matching of log record failed.")  # pragma: nocover
    parsed = m.groupdict()
    assert parsed["message"] == r"Hello	\| foo=bar"
    assert "\t| " not in parsed["message"]
    assert "target=World" in parsed["data"].split()
    assert "|" not in parsed["data"]


def test_pre_v030_format(capsys: CaptureFixture) -> None:
    setup_logging(sink=sys.stderr, fmt=LOG_FORMAT_PRE_v030)
    logger.info("Hello", target="World")
    captured = capsys.readouterr()
    m = re.match(PRE030_REGEX, captured.err)
    if not m:
        raise AssertionError("Regex matching of log record failed.")  # pragma: nocover
    parsed = m.groupdict()
    iso8601_date = f"{parsed['date'][:10]}T{parsed['date'][11:23]}{parsed['date'][24:]}"
    dt = datetime.datetime.fromisoformat(iso8601_date)
    assert datetime.datetime.now(tz=datetime.UTC) - dt < datetime.timedelta(seconds=1)
    assert parsed["level"] == "INFO"
    assert parsed["source"].startswith("test_setup.test_pre_v030_format:")
    assert parsed["message"] == "Hello"
    assert "target=World" in parsed["data"].split()
    assert "_source=" not in parsed["data"]


def test_json_format(capsys: CaptureFixture) -> None:
    setup_logging(sink=sys.stderr, data_format=DataFormat.JSON)
    msg = "Hello"
    logger.info(msg, target="World")
    captured = capsys.readouterr()
    parsed = json.loads(captured.err)
    import pprint

    pprint.pprint(parsed)
    assert parsed["text"] == f"{msg}\n"
    assert parsed["record"]["extra"] == {"request_id": "-", "target": "World"}
    assert parsed["record"]["file"] == {"name": "test_setup.py", "path": __file__}
    assert parsed["record"]["function"] == "test_json_format"
    assert parsed["record"]["level"]["name"] == "INFO"
    assert isinstance(parsed["record"]["line"], int)
    assert parsed["record"]["message"] == "Hello"
    assert parsed["record"]["module"] == "test_setup"
    assert parsed["record"]["name"] == "test_setup"
    assert "process" in parsed["record"]
    assert "thread" in parsed["record"]
    dt = datetime.datetime.fromisoformat(parsed["record"]["time"]["repr"])
    assert datetime.datetime.now(tz=datetime.UTC) - dt < datetime.timedelta(seconds=1)
    dt = datetime.datetime.fromtimestamp(parsed["record"]["time"]["timestamp"])
    assert datetime.datetime.now() - dt < datetime.timedelta(seconds=1)


def test_tb_in_data_and_multi_line_output(capsys: CaptureFixture) -> None:
    def foo() -> None:
        try:
            1 / 0  # noqa: B018
        except Exception:
            logger.exception("Hello", target="World")

    setup_logging(sink=sys.stderr)
    foo()
    captured = capsys.readouterr()
    m = re.match(ADR0010_REGEX, captured.err.split("\n")[0])
    if not m:
        raise AssertionError("Regex matching of log record failed.")  # pragma: nocover
    parsed = m.groupdict()
    assert parsed["level"] == "ERROR"
    assert parsed["message"] == "Hello"
    # TB in data
    m = re.match(r"^target=World _traceback=\"(?P<tb>.*?)\" _source=.*$", parsed["data"])
    if not m:
        raise AssertionError("Regex matching of log record failed.")  # pragma: nocover
    tb = m.groupdict()["tb"]
    assert tb.startswith("Traceback (most recent call last):\\n")
    assert tb.endswith("\\nZeroDivisionError: division by zero\\n")
    # TB printed directly to output
    assert captured.err.split("\n")[1] == "Traceback (most recent call last):"
    assert "".join(captured.err.split("\n")[-2:]).strip() == "ZeroDivisionError: division by zero"


@pytest.mark.parametrize(  # type: ignore
    "opt,val,exp",
    [
        ("log_level", True, "log_level=INFO"),
        ("request_id", True, "request_id=-"),
        ("process_id", True, f"process_id={os.getpid()}"),
        ("source_ref_key", "SRC", "SRC=test_setup.test_data_section_options:"),
        ("time", True, f"time={datetime.datetime.now():%Y-%m-%dT%H:%M:}"),
        ("timestamp", True, f"timestamp={str(datetime.datetime.now().timestamp())[:8]}"),
        ("thread_id", True, f"thread_id={threading.current_thread().ident}"),
        ("thread_name", True, "thread_name=MainThread"),
    ],
)
def test_data_section_options(capsys: CaptureFixture, opt: str, val: bool | str, exp: str) -> None:
    data_options = DataSectionOptions(**{opt: val})  # type: ignore
    setup_logging(sink=sys.stderr, data_options=data_options)
    logger.info("Hello World")
    captured = capsys.readouterr()
    m = re.match(ADR0010_REGEX, captured.err)
    if not m:
        raise AssertionError("Regex matching of log record failed.")  # pragma: nocover
    parsed = m.groupdict()
    assert exp in parsed["data"]

    for field in dataclasses.fields(DataSectionOptions):
        if field.name != opt:
            assert f"{field.name}=" not in parsed["data"]
    if opt == "source_ref_key":
        assert "_source=" not in parsed["data"]
    else:
        assert "_source=test_setup.test_data_section_options:" in parsed["data"]


@pytest.mark.parametrize(
    "opt,exp",
    [
        (DataFormat.JSON, '{"text": "Hello World\\n", "record":'),
        (DataFormat.LOGFMT, f"{datetime.datetime.now():%Y-%m-%dT%H:}"),
        ("foo", "exception"),
    ],
)  # type: ignore
def test_data_format_choices(capsys: CaptureFixture, opt: DataFormat, exp: str) -> None:
    if exp == "exception":
        with pytest.raises(ValueError) as excinfo:
            setup_logging(sink=sys.stderr, data_format=opt)
        assert "Unsupported 'data_format' setting: 'foo'" in str(excinfo.value)
    else:
        setup_logging(sink=sys.stderr, data_format=opt)
        logger.info("Hello World")
        captured = capsys.readouterr()
        assert captured.err.startswith(exp)


def test_request_id(capsys: CaptureFixture) -> None:
    setup_logging(sink=sys.stderr, request_id_func=lambda: "foo-bar-baz")
    logger.info("Hello")
    captured = capsys.readouterr()
    m = re.match(ADR0010_REGEX, captured.err)
    if not m:
        raise AssertionError("Regex matching of log record failed.")  # pragma: nocover
    parsed = m.groupdict()
    assert parsed["request_id"] == "foo-bar-ba"
