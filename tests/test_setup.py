# SPDX-License-Identifier: AGPL-3.0-only

import datetime
import re
import sys
from importlib.metadata import version

from _pytest.capture import CaptureFixture
from loguru import logger

from lancelog import LOG_FORMAT_DEFAULT, LOG_FORMAT_PRE_v030, setup_logging
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
        raise AssertionError("Regex matching of log record failed.")
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
        raise AssertionError("Regex matching of log record failed.")
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
        raise AssertionError("Regex matching of log record failed.")
    parsed = m.groupdict()
    assert parsed["message"] == "Hello	\| foo=bar"
    assert "\t| " not in parsed["message"]
    assert "target=World" in parsed["data"].split()
    assert "|" not in parsed["data"]


def test_pre_v030_format(capsys: CaptureFixture) -> None:
    setup_logging(sink=sys.stderr, fmt=LOG_FORMAT_PRE_v030)
    logger.info("Hello", target="World")
    captured = capsys.readouterr()
    m = re.match(PRE030_REGEX, captured.err)
    if not m:
        raise AssertionError("Regex matching of log record failed.")
    parsed = m.groupdict()
    iso8601_date = f"{parsed['date'][:10]}T{parsed['date'][11:23]}{parsed['date'][24:]}"
    dt = datetime.datetime.fromisoformat(iso8601_date)
    assert datetime.datetime.now(tz=datetime.UTC) - dt < datetime.timedelta(seconds=1)
    assert parsed["level"] == "INFO"
    assert parsed["source"].startswith("test_setup.test_pre_v030_format:")
    assert parsed["message"] == "Hello"
    assert "target=World" in parsed["data"].split()
    assert "_source=" not in parsed["data"]
