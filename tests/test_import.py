# SPDX-License-Identifier: AGPL-3.0-only

import sys


def test_import() -> None:
    assert "lancelog" in sys.modules
