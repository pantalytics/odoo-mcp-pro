"""Expose Odoo workflow skills as MCP resources.

Each skill is a directory under <repo>/skills/ with a SKILL.md file and
optional companion references. Skills teach Claude (or any MCP client)
how to accomplish a domain task: selling, buying, inventory, importing, etc.

Registered as MCP resources under the `skill://` URI scheme, so clients
can list and fetch them on demand without a live Odoo connection.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Pattern, Tuple

from mcp.server.fastmcp import FastMCP
from mcp.types import Annotations, ToolAnnotations

from .logging_config import get_logger

logger = get_logger(__name__)


def _find_skills_dir() -> Optional[Path]:
    """Locate the skills/ directory.

    Search order:
    1. <repo_root>/skills — source checkout
    2. <package_dir>/_skill_data — shipped inside the wheel (pyproject force-include)
    3. <package_dir>/skills — legacy fallback
    """
    pkg_dir = Path(__file__).parent
    for candidate in (
        pkg_dir.parent / "skills",
        pkg_dir / "_skill_data",
        pkg_dir / "skills",
    ):
        if candidate.is_dir():
            return candidate
    return None


def _parse_frontmatter(text: str) -> Tuple[Dict[str, str], str]:
    """Extract flat YAML frontmatter. Returns (meta, body).

    Intentionally minimal — no external YAML dep. Only flat `key: value`
    lines are parsed; list values are kept as raw strings.
    """
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text
    raw, body = text[4:end], text[end + 5 :]
    meta: Dict[str, str] = {}
    for line in raw.splitlines():
        if ":" not in line or line.lstrip().startswith("#"):
            continue
        k, _, v = line.partition(":")
        meta[k.strip()] = v.strip()
    return meta, body


def _parse_triggers(raw: str) -> List[str]:
    """Parse a `triggers: [a, b, c]` frontmatter value into a list.

    Accepts the inline YAML-list form only (what our skills use). Empty
    or malformed values return []. Tokens are lowercased.
    """
    s = raw.strip()
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1]
    return [t.strip().strip("'\"").lower() for t in s.split(",") if t.strip()]


def discover_skills() -> List[Dict[str, object]]:
    """Return [{name, description, path, triggers}] for every SKILL.md found."""
    skills_dir = _find_skills_dir()
    if not skills_dir:
        logger.warning("No skills/ directory found; skill resources disabled")
        return []
    out: List[Dict[str, object]] = []
    for child in sorted(skills_dir.iterdir()):
        skill_md = child / "SKILL.md"
        if not skill_md.is_file():
            continue
        try:
            text = skill_md.read_text(encoding="utf-8")
        except OSError as e:
            logger.warning(f"Could not read {skill_md}: {e}")
            continue
        meta, _ = _parse_frontmatter(text)
        name = meta.get("name") or child.name
        description = meta.get("description") or f"Odoo workflow skill: {name}"
        triggers = _parse_triggers(meta.get("triggers", ""))
        out.append(
            {
                "name": name,
                "description": description,
                "path": str(skill_md),
                "triggers": triggers,
            }
        )
    return out


def _companion_files(skill_dir: Path) -> List[Path]:
    """Every *.md under skill_dir except SKILL.md itself, recursive."""
    return sorted(p for p in skill_dir.rglob("*.md") if p.name != "SKILL.md" and p.is_file())


def _make_reader(path: Path, fn_name: str):
    """Build a zero-arg async reader bound to `path`.

    FastMCP's resource decorator requires the handler signature to match the
    URI template — concrete URIs (no `{param}`) need a no-arg function. Using
    a default-arg closure is interpreted as a parameter, so we use a factory.
    The `fn_name` is surfaced via `__name__` so resources list with a real name
    instead of all showing as `_read`.
    """

    async def _read() -> str:
        return path.read_text(encoding="utf-8")

    _read.__name__ = fn_name
    return _read


def register_skills(app: FastMCP) -> int:
    """Register skills as MCP resources AND tools.

    Resources (skill://{name}) are discoverable by MCP clients that
    surface them in their UI (browse/@-mention). Claude.ai's web client
    currently only surfaces resources to users, not to the model, so
    we also expose `find_skill` and `get_skill` as tools so the model
    can pull a skill into context on its own when a workflow needs it.

    Safe to call at server init — no Odoo connection required.
    Returns the number of skills registered (entry points only).
    """
    skills = discover_skills()
    if not skills:
        return 0

    # Build lookup maps for the tool handlers
    skill_by_name: Dict[str, Path] = {str(s["name"]): Path(str(s["path"])) for s in skills}
    # For find_skill matching: pre-compile one regex per skill matching
    # any of its triggers at a word-start boundary (so "offerte" matches
    # "offertes", but trigger "po" does NOT match inside "importeer").
    # Fall back to the skill name if no triggers are in the frontmatter.
    trigger_map: List[Tuple[str, Pattern[str]]] = []
    for s in skills:
        name = str(s["name"])
        triggers = list(s.get("triggers") or [])
        if not triggers:
            # Fallback: split name on dashes, keep only pieces >= 4 chars
            # so noise like "pro" in "import-pro" doesn't match "product".
            triggers = [t for t in name.lower().split("-") if len(t) >= 4]
        if not triggers:
            continue
        # \b at start only — lets "offerte" match "offertes" but blocks
        # mid-word hits like "po" inside "importeer".
        pattern = re.compile(
            r"\b(?:" + "|".join(re.escape(t) for t in triggers) + r")",
            re.IGNORECASE,
        )
        trigger_map.append((name, pattern))

    companion_count = 0
    for skill in skills:
        name = skill["name"]
        skill_md = Path(skill["path"])
        skill_dir = skill_md.parent

        app.resource(
            f"skill://{name}",
            title=f"Skill: {name}",
            description=skill["description"],
            annotations=Annotations(audience=["assistant"], priority=0.6),
        )(_make_reader(skill_md, f"skill_{name}"))

        for companion in _companion_files(skill_dir):
            rel = companion.relative_to(skill_dir).as_posix()
            slug = rel.replace("/", "_").replace(".", "_")
            app.resource(
                f"skill://{name}/{rel}",
                title=f"Skill: {name} / {rel}",
                description=f"Companion reference for skill `{name}`",
                annotations=Annotations(audience=["assistant"], priority=0.4),
            )(_make_reader(companion, f"skill_{name}_{slug}"))
            companion_count += 1

    # --- Tool surface: make skills discoverable to the model itself ---

    @app.tool(
        title="Find Relevant Odoo Skill",
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def find_skill(question: str) -> Dict[str, object]:
        """Return the Odoo skill most relevant to a question, with its content.

        Prefer this tool — it picks the right skill by keyword match
        and returns its full markdown guide in one call. Use when
        you're about to execute a domain workflow (selling, buying,
        inventory, etc.) and want the reference material first.

        If `find_skill` returns nothing, fall back to `get_skill(name)`
        with a guessed name.

        Args:
            question: The user's question or task description, any language.

        Returns:
            {name, description, content} of the best match, or an
            empty dict if nothing matched. If multiple skills matched,
            `alternatives` lists other candidate names.
        """
        scored: List[Tuple[int, str]] = []
        for name, pattern in trigger_map:
            score = len(pattern.findall(question))
            if score > 0:
                scored.append((score, name))
        if not scored:
            return {}
        scored.sort(key=lambda s: (-s[0], s[1]))
        top_name = scored[0][1]
        top = next(s for s in skills if s["name"] == top_name)
        path = Path(str(top["path"]))
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as e:
            logger.warning(f"find_skill({top_name!r}) read failed: {e}")
            content = ""
        result: Dict[str, object] = {
            "name": top_name,
            "description": str(top["description"]),
            "content": content,
        }
        if len(scored) > 1:
            result["alternatives"] = [n for _, n in scored[1:4]]
        return result

    @app.tool(
        title="Get Odoo Skill",
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def get_skill(name: str) -> str:
        """Read an Odoo workflow skill by name.

        Returns the full SKILL.md content — load this as context before
        executing a multi-step workflow in that domain.

        Args:
            name: Skill name as returned by `find_skill` (e.g. "selling").

        Returns:
            The markdown body of the SKILL.md file. Empty string if
            the skill name is not known.
        """
        path = skill_by_name.get(name)
        if path is None:
            return ""
        try:
            return path.read_text(encoding="utf-8")
        except OSError as e:
            logger.warning(f"get_skill({name!r}) read failed: {e}")
            return ""

    logger.info(
        f"Registered {len(skills)} skill resources "
        f"({companion_count} companion files) "
        f"+ 2 tools (find_skill, get_skill)"
    )
    return len(skills)
