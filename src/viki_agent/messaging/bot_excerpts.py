"""Episode excerpts: the words of a Bot's finished turns, read from Claude Code's transcripts only
while a learning run builds its prompt, and kept nowhere.

An excerpt holds, in order, the owner's messages (`Owner:`), the Bot's text (`Bot:`) and the names
of the tools it started (`Tools:`), from the transcript entries stamped within the episode's
start and end, give or take WINDOW_S. Tool results, thinking and attachments are left out. A
transcript is read through the catalog's bounded reader (`catalog._read_entries`): one whole file
up to its size limit, else its newest window, open-read-close. `owner_text` is the owner's own
words in a main chat or task thread, the only place a profile fact may quote; a Bot-thread or
routine turn's prompt is composed by the app, so it has none.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from viki_agent.automation.routines import parse_stamp
from viki_agent.catalog import _read_entries
from viki_agent.messaging.bot_episodes import Episode

log = logging.getLogger(__name__)

WINDOW_S = 5
MAX_LINE_CHARS = 1500
MAX_EXCERPT_CHARS = 6000


@dataclass(frozen=True)
class Excerpt:
    label: str  # E1, E2, ... in the order the prompt lists them
    episode: Episode
    text: str
    owner_text: str


def _when(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None


def _clip(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= MAX_LINE_CHARS else flat[: MAX_LINE_CHARS - 1] + "…"


def _texts(content: object) -> list[str]:
    if isinstance(content, str):
        return [content]
    if not isinstance(content, list):
        return []
    return [
        block["text"]
        for block in content
        if isinstance(block, dict)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
    ]


def _tools(content: object) -> list[str]:
    if not isinstance(content, list):
        return []
    return [
        block["name"]
        for block in content
        if isinstance(block, dict)
        and block.get("type") == "tool_use"
        and isinstance(block.get("name"), str)
    ]


def lines_for(entries: Sequence[dict[str, Any]], start: datetime, end: datetime) -> list[str]:
    """The excerpt lines of the entries stamped within [start - WINDOW_S, end + WINDOW_S]."""
    low, high = start - timedelta(seconds=WINDOW_S), end + timedelta(seconds=WINDOW_S)
    lines: list[str] = []
    for entry in entries:
        moment = _when(entry.get("timestamp"))
        if moment is None or not low <= moment <= high or entry.get("isSidechain"):
            continue
        message = entry.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if entry.get("type") == "user":
            if entry.get("isMeta") or "toolUseResult" in entry:
                continue
            lines.extend(f"Owner: {_clip(text)}" for text in _texts(content) if text.strip())
        elif entry.get("type") == "assistant":
            lines.extend(f"Bot: {_clip(text)}" for text in _texts(content) if text.strip())
            names = _tools(content)
            if names:
                lines.append("Tools: " + ", ".join(names))
    return lines


def _fit(lines: list[str]) -> str:
    """The lines joined, the middle dropped once they pass MAX_EXCERPT_CHARS: the request at the
    start and the result at the end are what a lesson is learned from."""
    text = "\n".join(lines)
    if len(text) <= MAX_EXCERPT_CHARS:
        return text
    head: list[str] = []
    tail: list[str] = []
    size = len("[…]") + 2
    for index in range(len(lines)):
        for side, line in ((head, lines[index]), (tail, lines[-1 - index])):
            if size + len(line) + 1 > MAX_EXCERPT_CHARS:
                return "\n".join([*head, "[…]", *reversed(tail)])
            side.append(line)
            size += len(line) + 1
    return text


def read_excerpts(
    paths_for: Callable[[list[str]], dict[str, Path]], episodes: Sequence[Episode]
) -> list[Excerpt]:
    """One excerpt per episode whose transcript reads and holds its turn, labelled in order."""
    paths = paths_for(sorted({episode.session_id for episode in episodes}))
    cache: dict[str, list[dict[str, Any]]] = {}
    excerpts: list[Excerpt] = []
    for episode in episodes:
        path = paths.get(episode.session_id)
        if path is None:
            continue
        if episode.session_id not in cache:
            try:
                cache[episode.session_id] = _read_entries(path)
            except OSError:
                cache[episode.session_id] = []
        try:
            start, end = parse_stamp(episode.started_at), parse_stamp(episode.finished_at)
        except ValueError:
            continue
        lines = lines_for(cache[episode.session_id], start, end)
        if not lines:
            continue
        text = _fit(lines)
        owner = (
            "\n".join(line[len("Owner: ") :] for line in lines if line.startswith("Owner: "))
            if episode.kind in ("main", "task")
            else ""
        )
        excerpts.append(Excerpt(f"E{len(excerpts) + 1}", episode, text, owner))
    return excerpts
