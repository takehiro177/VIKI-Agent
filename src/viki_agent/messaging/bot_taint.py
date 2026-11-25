"""Which tool calls taint a Bot session: text from outside the vault and its owner.

A Bot session is tainted once it runs WebFetch or WebSearch (`web`) or any MCP tool (`mcp`):
their results are text nobody here wrote, and it can carry instructions. Taint is kept per
session (`bot_session_taint`), reaches every member of the Bot thread a session takes part in,
and only the owner clears it. A tainted session's turns stay out of what reflect and dream runs
learn from (`bot_learning`), and the write fence (`bot_fence`) refuses its writes to instruction,
skill and memory files. A shell command can fetch the web too; nothing here judges Bash.
"""

from __future__ import annotations

from collections.abc import Iterable

WEB_TOOLS = frozenset({"WebFetch", "WebSearch"})
SOURCES = ("web", "mcp")


def taint_source(tool: object) -> str | None:
    """The taint a call to `tool` adds, or None for a tool that reads only the owner's files."""
    if not isinstance(tool, str):
        return None
    if tool in WEB_TOOLS:
        return "web"
    if tool.startswith("mcp__"):
        return "mcp"
    return None


def join_sources(sources: Iterable[str]) -> str:
    """The stored spelling of a set of sources: `web`, `mcp` or `web,mcp`."""
    kept = set(sources)
    if not kept or not kept <= set(SOURCES):
        raise ValueError("invalid taint sources")
    return ",".join(source for source in SOURCES if source in kept)
