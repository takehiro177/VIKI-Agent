"""A lesson's claim grammar: pure, no I/O.

A lesson is an ordinary memory record (`memory_record`) whose claim is exactly these lines, in
this order, each a key, one space and a value:

    Trigger: <when the lesson applies>
    Action: <the one thing to do>
    Example: "<a verbatim quote from the work it was learned from>"   (one or two lines)

A claim of any other shape is not a lesson, whatever else it says: it stays a fact. Values are
one line of printable text without surrounding spaces; an example is quoted with straight double
quotes that the quote itself may not contain.
"""

from __future__ import annotations

from dataclasses import dataclass

MAX_TRIGGER_CHARS = 300
MAX_ACTION_CHARS = 500
MAX_EXAMPLE_CHARS = 400
MAX_EXAMPLES = 2


@dataclass(frozen=True)
class Lesson:
    trigger: str
    action: str
    examples: tuple[str, ...]


def _value(text: str, maximum: int) -> bool:
    return (
        0 < len(text) <= maximum
        and text == text.strip()
        and text.isprintable()
        and " " not in text
        and " " not in text
    )


def _example(text: str) -> bool:
    return _value(text, MAX_EXAMPLE_CHARS) and '"' not in text


def parse(claim: str) -> Lesson | None:
    """The lesson `claim` spells, or None for any claim that is not exactly one."""
    lines = claim.split("\n")
    if not 3 <= len(lines) <= 2 + MAX_EXAMPLES:
        return None
    head, act, *rest = lines
    if not head.startswith("Trigger: ") or not act.startswith("Action: "):
        return None
    trigger, action = head[len("Trigger: ") :], act[len("Action: ") :]
    if not _value(trigger, MAX_TRIGGER_CHARS) or not _value(action, MAX_ACTION_CHARS):
        return None
    examples: list[str] = []
    for line in rest:
        if not (line.startswith('Example: "') and line.endswith('"') and len(line) > 11):
            return None
        quote = line[len('Example: "') : -1]
        if not _example(quote):
            return None
        examples.append(quote)
    return Lesson(trigger, action, tuple(examples))


def compose(lesson: Lesson) -> str:
    """The claim for `lesson`, or ValueError when it would not parse back to the same lesson."""
    lines = [f"Trigger: {lesson.trigger}", f"Action: {lesson.action}"]
    lines.extend(f'Example: "{quote}"' for quote in lesson.examples)
    claim = "\n".join(lines)
    if parse(claim) != lesson:
        raise ValueError("not_a_lesson")
    return claim


def normalize_quote(text: str) -> str:
    """`text` as one example line: whitespace runs become one space, double quotes become single
    ones and the result is cut to MAX_EXAMPLE_CHARS."""
    flat = " ".join(text.split()).replace('"', "'")
    return flat[:MAX_EXAMPLE_CHARS].strip()
