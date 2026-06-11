"""Repository hygiene checks.

Keeps the repo within its structural rules: max line count per Python file
(see scripts/check_max_lines.py, also enforced in CI) and files removed in
the open-core split staying removed.
"""

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

REMOVED_FILES = [
    "forum-post.md",
    "docs/logo-test.html",
    "docs/ai-grid.html",
    "TODO_auto_detect_api_version.md",
    "mcp_server_odoo/oauth.py",
    "mcp_server_odoo/registry.py",
]


class TestRepoHygiene:
    """Structural checks on the repository itself."""

    def test_max_lines_per_python_file(self):
        """Every tracked Python file stays within the 500-line limit."""
        if not (REPO_ROOT / ".git").exists():
            pytest.skip("not running from a git checkout")
        result = subprocess.run(
            [sys.executable, "scripts/check_max_lines.py"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"check_max_lines.py failed:\n{result.stdout}{result.stderr}"

    def test_removed_files_stay_removed(self):
        """Files deleted in the open-core split must not come back."""
        resurrected = [p for p in REMOVED_FILES if (REPO_ROOT / p).exists()]
        assert not resurrected, f"Files should stay deleted: {resurrected}"
