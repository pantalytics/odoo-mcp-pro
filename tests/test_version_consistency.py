"""The package version is declared in three places (``__version__``,
``server.SERVER_VERSION``, and ``pyproject.toml``). They must stay in
lockstep. A footer once showed a stale version because ``SERVER_VERSION``
was bumped separately and lagged; these tests stop that from recurring.
"""

import pathlib
import re

from mcp_server_odoo import __version__
from mcp_server_odoo.server import SERVER_VERSION


def test_server_version_matches_package_version():
    assert SERVER_VERSION == __version__


def test_pyproject_version_matches_package_version():
    root = pathlib.Path(__file__).resolve().parent.parent
    text = (root / "pyproject.toml").read_text()
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert match, "no version found in pyproject.toml"
    assert match.group(1) == __version__
