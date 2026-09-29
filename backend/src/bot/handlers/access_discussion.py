"""Lossless, length-bounded pages for discussions of access requests."""

from collections.abc import Mapping, Sequence
from typing import Any


def discussion_pages(
    messages: Sequence[Mapping[str, Any]],
    *,
    actor_id: str,
    applicant_id: str,
    max_chars: int = 2800,
) -> list[str]:
    """Keep every message in order, including messages longer than one page."""
    if max_chars < 100:
        raise ValueError("Страница обсуждения слишком короткая")
    pages: list[str] = []
    current = ""
    for number, message in enumerate(messages, start=1):
        author_id = str(message["author_user_id"])
        author = (
            "Вы"
            if author_id == actor_id
            else "Заявитель" if author_id == applicant_id else "Другая сторона"
        )
        remaining = str(message["text"])
        first = True
        while first or remaining:
            prefix = f"{number}. {author}{'' if first else ' (продолжение)'}: "
            capacity = max_chars - len(prefix)
            if capacity < 1:
                raise ValueError("Слишком длинная подпись автора")
            fragment = prefix + remaining[:capacity]
            remaining = remaining[capacity:]
            if current and len(current) + 2 + len(fragment) > max_chars:
                pages.append(current)
                current = ""
            current = f"{current}\n\n{fragment}" if current else fragment
            first = False
    if current:
        pages.append(current)
    return pages


def page_number(raw: str) -> int:
    """One-based page from a text command or button payload."""
    try:
        number = int(raw)
    except (TypeError, ValueError) as error:
        raise ValueError(
            "Номер страницы должен быть положительным целым числом"
        ) from error
    if number < 1:
        raise ValueError("Номер страницы должен быть положительным целым числом")
    return number
