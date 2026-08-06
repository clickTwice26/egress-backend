"""Community domain rules: reactions, cursors and body limits.

Pure logic — no database, no request objects — so the router stays thin and
these rules can be reasoned about (and tested) on their own.
"""

import base64
import binascii
from datetime import datetime
from typing import Literal

# The reaction set. Deliberately small: five options are scannable in one row on
# a phone, and each one means something different for a study community.
REACTION_KINDS: tuple[str, ...] = ("like", "love", "insightful", "celebrate", "curious")

TargetType = Literal["post", "comment"]
TARGET_TYPES: tuple[str, ...] = ("post", "comment")

# Long enough for a considered answer, short enough that the feed stays a feed.
MAX_POST_BODY = 5000
MAX_COMMENT_BODY = 2000

# Page sizes. The default fills a phone screen twice over; the ceiling stops a
# client asking for the whole table in one call.
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 50


class CursorError(ValueError):
    """The client sent a cursor that did not come from us."""


def encode_cursor(created_at: datetime, row_id: str) -> str:
    """Opaque keyset cursor.

    Keyset, not offset: `OFFSET 2000` makes the database walk 2000 rows it will
    throw away, and a post written mid-scroll shifts every later page by one, so
    the reader sees an item twice or not at all. A cursor naming the last row
    seen has neither problem, and stays O(log n) at any depth.

    Base64 rather than raw text so it reads as an opaque token: nothing in the
    client should be tempted to parse or fabricate one.
    """
    raw = f"{created_at.isoformat()}|{row_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    padding = "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(cursor + padding).decode()
        stamp, _, row_id = raw.partition("|")
        if not row_id:
            raise CursorError("Malformed cursor.")
        return datetime.fromisoformat(stamp), row_id
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise CursorError("Malformed cursor.") from exc


def clamp_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_PAGE_SIZE
    return max(1, min(limit, MAX_PAGE_SIZE))


def can_edit(author_id: str, viewer_id: str) -> bool:
    """Editing is the author's alone — a moderator removes, never rewrites."""
    return author_id == viewer_id


def can_delete(author_id: str, viewer_id: str, viewer_weight: int, moderate_weight: int) -> bool:
    """Authors delete their own; anyone heavy enough to moderate deletes any.

    The second half is a weight comparison, so a role added later at or above
    the moderation bar can moderate without this function changing.
    """
    return author_id == viewer_id or viewer_weight >= moderate_weight
