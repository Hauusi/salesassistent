"""Column-length introspection, so length caps live in exactly one place.

Several columns are bounded (`String(n)`) because the underlying protocol
bounds them - an RFC 5321 address is at most 320 characters, an RFC 5322
header line at most 998. Mail arriving from the outside world does not
respect those bounds: a display name of several kilobytes is a routine
spam pattern, and a folded Subject header can exceed 998 easily.

Writing such a value used to raise StringDataRightTruncation from
Postgres, which aborted the whole poll batch (see app/workers/tasks.py).

Rather than repeating `[:998]`-style magic numbers next to every write,
`fit` reads the declared length off the mapped column itself. The cap can
then only ever disagree with the schema if the schema changed, and a
column widened in a migration needs no second edit here.
"""
from __future__ import annotations

from typing import Any, TypeVar, overload

from sqlalchemy import inspect

T = TypeVar("T")

_ELLIPSIS = "…"


def column_max_length(model: type[Any], attribute: str) -> int | None:
    """The declared max length of a mapped string column, or None if the
    column is unbounded (Text) or not a string at all."""
    column = inspect(model).columns[attribute]
    return getattr(column.type, "length", None)


@overload
def fit(value: None, model: type[Any], attribute: str) -> None: ...
@overload
def fit(value: str, model: type[Any], attribute: str) -> str: ...


def fit(value: str | None, model: type[Any], attribute: str) -> str | None:
    """Truncates `value` to fit the column it is about to be written to.

    Returns None unchanged, so it composes with nullable columns. Overlong
    values are marked with an ellipsis so a truncated value is visibly
    truncated to a human reading it later, rather than silently looking
    like the sender's own text.
    """
    if value is None:
        return None

    max_length = column_max_length(model, attribute)
    if max_length is None or len(value) <= max_length:
        return value

    return value[: max_length - len(_ELLIPSIS)].rstrip() + _ELLIPSIS
