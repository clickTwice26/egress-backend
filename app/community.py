"""Community domain rules: reactions, cursors and body limits.

Pure logic — no database, no request objects — so the router stays thin and
these rules can be reasoned about (and tested) on their own.
"""

import base64
import binascii
import re
import unicodedata
from dataclasses import dataclass
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


@dataclass(frozen=True)
class TopicSpec:
    slug: str
    label: str
    description: str
    position: int


# The topics a post can be filed under. A fixed, curated list rather than
# free-form categories: five or six good buckets get used, forty do not, and a
# reader scanning the filter row can hold this many in their head.
DEFAULT_TOPICS: tuple[TopicSpec, ...] = (
    TopicSpec("general", "General", "Anything else about preparing for the test.", 10),
    TopicSpec("listening", "Listening", "Sections, accents, note completion.", 20),
    TopicSpec("reading", "Reading", "Passages, timing, question types.", 30),
    TopicSpec("writing", "Writing", "Task 1 and Task 2, feedback and models.", 40),
    TopicSpec("speaking", "Speaking", "Parts 1 to 3, fluency, partners.", 50),
    TopicSpec("test-day", "Test day", "Booking, centres, nerves, results.", 60),
    TopicSpec("resources", "Resources", "Books, sites and material worth the time.", 70),
)

DEFAULT_TOPIC_SLUG = "general"


# Where a hashtag may begin. The lookbehind keeps '#' inside a URL fragment, a
# repeated '##', or an identifier like 'C#5' from starting one.
_TAG_START = re.compile(r"(?<![\w#/])#")

MAX_TAGS_PER_POST = 10
MAX_TAG_LENGTH = 50


def _is_tag_character(character: str) -> bool:
    """Whether a character continues a hashtag.

    `\\w` is not enough. In Bengali, Devanagari and much of South and South-East
    Asia a word is letters *plus* combining vowel signs, which `\\w` excludes —
    so '#বাংলা' would be harvested as '#ব', silently truncating tags for a large
    part of the audience. Marks are admitted explicitly.
    """
    return character.isalnum() or character == "_" or unicodedata.category(character) in {"Mn", "Mc"}


def extract_tags(body: str) -> list[str]:
    """The hashtags in a post body, normalised and deduplicated.

    Extraction happens on the server because the server owns the stored body:
    a client that forgot to send its tags, or invented some the text does not
    contain, would otherwise put the index out of step with what people read.

    A tag must contain a letter. Without that rule '#1' and '#2' in a numbered
    list become tags, and the trending list fills up with ordinals.
    """
    seen: dict[str, None] = {}

    for match in _TAG_START.finditer(body):
        characters: list[str] = []
        for character in body[match.end() : match.end() + MAX_TAG_LENGTH]:
            if not _is_tag_character(character):
                break
            characters.append(character)

        tag = "".join(characters).casefold()
        if not tag or not any(character.isalpha() for character in tag):
            continue

        seen.setdefault(tag)
        if len(seen) >= MAX_TAGS_PER_POST:
            break

    return list(seen)


def normalise_tag(tag: str) -> str:
    """The stored form of a tag, for lookups coming in from a URL."""
    return tag.lstrip("#").casefold()[:MAX_TAG_LENGTH]


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
