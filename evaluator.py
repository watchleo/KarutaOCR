"""Parse wishlist-evaluator messages for Karuta drops.

The original parser accepted exactly one layout (a Card Companion custom
template that used <:no_1:> slot emojis, backticked heart/count pairs,
middle-dot separators and a bold character name).  Real evaluators differ:
Keqing numbers lines ("1. `♡1,448`**Rem** · Series`#6,524`"), some use keycap
emoji, bullet separators, comma thousands, plain names, or trailing print
numbers.  This version extracts the fields position-independently so all of
those layouts parse, while keeping the public API identical.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MAX_TARGET_PRINT_NUMBER = 999

# When every print number on a drop exceeds MAX_TARGET_PRINT_NUMBER, fall
# back to wishlist counts instead of skipping the drop entirely. Set to
# False to restore the original strict skip behaviour.
FALLBACK_TO_WISHLIST = True

# Separators seen between the segments of an evaluator line.
_SEP = r"[·•∙⋅]"

# A slot is marked in one of three ways in the wild:
#   <:no_1:123456>   custom emoji whose name is no_1..no_4 (Card Companion)
#   1️⃣               keycap emoji (with or without the variation selector)
#   "1." / "1)"      leading list number (Keqing and similar bots)
_SLOT_PATTERNS = (
    re.compile(r"<a?:no_(?P<slot>[1-4]):\d+>"),
    re.compile(r"(?P<slot>[1-4])\ufe0f?\u20e3"),
    re.compile(r"^\s*(?P<slot>[1-4])\s*[.)]\s"),
)

# Wishlist count after a heart.  Backticks and spaces are optional on either
# side, so `♡` `2`, `♡2`, `♡ 2`, `♡1,448` and `♡` `1,448` all work.  A
# trailing keyword form ("830 wishes") is accepted as a fallback.
_COUNT_PATTERNS = (
    re.compile(r"[♡♥❤]\ufe0f?\s*`?\s*`?\s*(?P<count>\d[\d,]*)\s*`?\s*`?"),
    re.compile(r"(?P<count>\d[\d,]*)\s*wish", re.I),
)

# Print number ("#123", "`#84,774`") anywhere on the line.  A "#" directly
# preceded by "<" or "/" is a channel mention or URL fragment, not a print.
_PRINT = re.compile(r"(?<![</])#\s*`?\s*(?P<value>\d[\d,]*)\s*`?")

# Quantity marker ("◈2"), extracted only so it can be ignored.
_QUANTITY = re.compile(r"◈\s*`?\s*(?P<value>\d[\d,]*)\s*`?")

_BOLD_NAME = re.compile(r"\*\*(?P<name>[^*]+?)\*\*")
_EXPIRATION = re.compile(r"<t:(\d+)(?::[tTdDfFR])?>")

_HEART = re.compile(r"[♡♥❤]")
_NOISE = re.compile(r"<a?:[\w~]+:\d+>|[1-4]\ufe0f?\u20e3|[\u200b\u200d\ufe0f]")


@dataclass(frozen=True)
class EvaluatedCard:
    slot: int
    wishlist_count: int
    name: str
    series: str
    print_number: int | None = None


@dataclass(frozen=True)
class Evaluation:
    cards: tuple[EvaluatedCard, ...]
    expires_at: int | None


def _clean(text: str) -> str:
    """Strip emojis, markdown and zero-width characters; collapse spaces."""
    text = _NOISE.sub(" ", text)
    text = text.replace("`", " ").replace("*", " ")
    return re.sub(r"\s+", " ", text).strip(" \t·•∙⋅")


def _remove_spans(line: str, matches: tuple[re.Match, ...]) -> str:
    cuts = sorted(match.span() for match in matches if match is not None)
    remainder = ""
    cursor = 0
    for start, end in cuts:
        remainder += line[cursor:start] + " "
        cursor = end
    remainder += line[cursor:]
    return remainder


def _parse_line(line: str) -> EvaluatedCard | None:
    line = line.strip()
    if not line:
        return None
    if _HEART.search(line) is None and "wish" not in line.lower():
        return None

    slot_match = None
    for pattern in _SLOT_PATTERNS:
        slot_match = pattern.search(line)
        if slot_match is not None:
            break
    if slot_match is None:
        return None

    count_match = None
    for pattern in _COUNT_PATTERNS:
        count_match = pattern.search(line)
        if count_match is not None:
            break
    if count_match is None:
        return None

    print_match = _PRINT.search(line)
    quantity_match = _QUANTITY.search(line)

    remainder = _remove_spans(
        line, (slot_match, count_match, print_match, quantity_match)
    )
    parts = [_clean(part) for part in re.split(_SEP, remainder)]
    parts = [part for part in parts if part]
    if not parts:
        return None

    bold = _BOLD_NAME.search(remainder)
    if bold is not None:
        name = _clean(bold.group("name"))
        series = ""
        for index, part in enumerate(parts):
            if name and name in part:
                for later in parts[index + 1 :]:
                    series = later
                    break
                break
    else:
        name = parts[0]
        series = parts[1] if len(parts) > 1 else ""

    slot = int(slot_match.group("slot"))
    if not 1 <= slot <= 4 or not name or not series:
        return None

    return EvaluatedCard(
        slot=slot,
        wishlist_count=int(count_match.group("count").replace(",", "")),
        name=name,
        series=series,
        print_number=(
            int(print_match.group("value").replace(",", ""))
            if print_match
            else None
        ),
    )


def parse_evaluator_message(content: str) -> Evaluation | None:
    """Parse numbered wishlist entries and the optional Discord expiry timestamp."""
    cards: list[EvaluatedCard] = []
    for line in content.splitlines():
        card = _parse_line(line)
        if card is not None:
            cards.append(card)

    if not cards:
        return None

    slots = [card.slot for card in cards]
    if len(slots) != len(set(slots)):
        return None

    expiration = _EXPIRATION.search(content)
    return Evaluation(
        cards=tuple(cards),
        expires_at=int(expiration.group(1)) if expiration else None,
    )


def choose_card(evaluation: Evaluation) -> EvaluatedCard | None:
    """Prefer the lowest print number up to 999; otherwise use wishlist counts.

    If prints are present but every one of them exceeds the limit, fall back
    to the highest positive wishlist count (unless FALLBACK_TO_WISHLIST is
    False, which restores the original "skip the drop" behaviour).
    """
    numbered_cards = [
        card for card in evaluation.cards if card.print_number is not None
    ]
    if numbered_cards:
        eligible = [
            card
            for card in numbered_cards
            if card.print_number is not None
            and card.print_number <= MAX_TARGET_PRINT_NUMBER
        ]
        if eligible:
            return min(eligible, key=lambda card: (card.print_number, card.slot))
        if not FALLBACK_TO_WISHLIST:
            return None

    eligible = [card for card in evaluation.cards if card.wishlist_count > 0]
    if not eligible:
        return None
    return max(eligible, key=lambda card: (card.wishlist_count, -card.slot))
