#!/usr/bin/env python3
"""Enforce a maximum line count per Python file.

Runs from the repo root. Only git-tracked ``*.py`` files are checked, so
untracked dev-overlay files (e.g. symlinks under ``mcp_server_odoo/admin/``)
are excluded automatically. Symlinks and missing files are skipped too.

Usage: python scripts/check_max_lines.py
"""

from __future__ import annotations

import os
import subprocess
import sys

MAX_LINES = 500


def _tracked_python_files() -> list[str]:
    """Return git-tracked .py paths relative to the repo root."""
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", "*.py"],
        check=True,
        capture_output=True,
    ).stdout
    return [p.decode("utf-8") for p in out.split(b"\0") if p]


def find_violations() -> list[tuple[str, int]]:
    """Return (path, line_count) for tracked files exceeding MAX_LINES."""
    violations: list[tuple[str, int]] = []
    for path in _tracked_python_files():
        if path.startswith("mcp_server_odoo/admin/"):
            continue  # private admin overlay
        if os.path.islink(path):
            continue  # dev overlay can symlink files (e.g. usage.py)
        if not os.path.isfile(path):
            continue  # tracked but deleted on disk
        with open(path, "rb") as f:
            n = sum(1 for _ in f)
        if n > MAX_LINES:
            violations.append((path, n))
    return violations


def main() -> int:
    violations = find_violations()
    if violations:
        for path, n in sorted(violations):
            print(f"{path}: {n} lines (limit {MAX_LINES})")
        return 1
    print(f"OK: all tracked Python files are within {MAX_LINES} lines.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
