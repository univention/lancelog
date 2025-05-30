import sys
from importlib.metadata import version

from _pytest.capture import CaptureFixture

from lancelog import setup_logging


def test_debug_version_msg(capsys: CaptureFixture) -> None:
    # have to use stderr, because of https://github.com/pytest-dev/pytest/issues/5997
    setup_logging(sink=sys.stderr, level="DEBUG")
    captured = capsys.readouterr()
    assert "Configured logging" in captured.err
    assert f"lancelog_version={version('lancelog')}" in captured.err
