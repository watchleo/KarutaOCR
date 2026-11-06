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

MAX_TARGET_PRINT_NUMBER = 999  # deprecated: kept only for old imports

# ---- Smart-grab priority rules --------------------------------------------
# The print-number RANGE decides first; the wishlist count only ranks cards
# inside the same range.  A lower print number never beats a higher print
# number in the same range just by being lower.
#
#   1. prints 1-9     (SP)  always grabbed; ranked by highest wishlist count
#   2. prints 10-99   (LP)  always grabbed; ranked by highest wishlist count
#   3. prints 100-999 (MP)  grabbed with HIGH_PRINT_WISHLIST_THRESHOLD (7+)
#                           wishes, highest count first; 1-6 wishes only as a
#                           last resort when nothing else qualifies
#   4. prints 1000+   (HP)  grabbed ONLY with 7+ wishes - never fewer
#
# When no card qualifies, GRAB_LAST_RESORT decides whether the best
# remaining low-wishlist card is still taken (True) or the drop is skipped
# (False).  Cards whose print the evaluator did not show follow a pure
# wishlist ladder (7+ preferred, then any positive count) and only compete
# with printed cards in the last-resort band.
HIGH_PRINT_WISHLIST_THRESHOLD = 7
GRAB_LAST_RESORT = True

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


def _print_band(print_number: int) -> int:
    """0 = 1-9 (SP), 1 = 10-99 (LP), 2 = 100-999 (MP), 3 = 1000+ (HP)."""
    if print_number <= 9:
        return 0
    if print_number <= 99:
        return 1
    if print_number <= 999:
        return 2
    return 3


def _best_of(cards: list[EvaluatedCard]) -> EvaluatedCard:
    """Highest wishlist count, then lowest print number, then lowest slot."""
    return min(
        cards,
        key=lambda card: (
            -card.wishlist_count,
            card.print_number if card.print_number is not None else float("inf"),
            card.slot,
        ),
    )


def explain_choice(evaluation: Evaluation) -> tuple[EvaluatedCard | None, str]:
    """Apply the tiered grabbing rules; return the card and the winning rule.

    Ladder (first non-empty band wins, a lower-priority range never beats a
    qualifying card from a higher-priority one):
      1. prints 1-9, any wishes            -> "single print (1-9)"
      2. prints 10-99, any wishes          -> "low print (10-99)"
      3. prints 100-999 with 7+ wishes     -> "mid print (100-999), wishlist 7+"
      4. prints 1000+ with 7+ wishes       -> "high print (1000+), wishlist 7+"
      5. no print info with 7+ wishes      -> "no print info, wishlist 7+"
      6. last resort (GRAB_LAST_RESORT): the best card with 1-6 wishes among
         mid prints and print-less cards
    When no card shows a print number at all, a pure wishlist ladder applies
    (7+ preferred, then any positive count).  Returns (None, "") when the
    drop must not be grabbed at all.
    """
    threshold = HIGH_PRINT_WISHLIST_THRESHOLD
    printed = [card for card in evaluation.cards if card.print_number is not None]
    unknown = [card for card in evaluation.cards if card.print_number is None]
    bands: list[list[EvaluatedCard]] = ([], [], [], [])
    for card in printed:
        bands[_print_band(card.print_number)].append(card)

    if bands[0]:
        return _best_of(bands[0]), "single print (1-9)"
    if bands[1]:
        return _best_of(bands[1]), "low print (10-99)"
    mp_good = [card for card in bands[2] if card.wishlist_count >= threshold]
    if mp_good:
        return _best_of(mp_good), f"mid print (100-999), wishlist {threshold}+"
    hp_good = [card for card in bands[3] if card.wishlist_count >= threshold]
    if hp_good:
        return _best_of(hp_good), f"high print (1000+), wishlist {threshold}+"
    unknown_good = [card for card in unknown if card.wishlist_count >= threshold]
    if unknown_good:
        return _best_of(unknown_good), f"no print info, wishlist {threshold}+"

    if not printed:
        # No print numbers anywhere: rank by wishlist count alone.
        if GRAB_LAST_RESORT:
            weak = [card for card in unknown if card.wishlist_count > 0]
            if weak:
                return _best_of(weak), "wishlist only (no print info)"
        return None, ""

    if GRAB_LAST_RESORT:
        mp_weak = [card for card in bands[2] if card.wishlist_count > 0]
        if mp_weak:
            return _best_of(mp_weak), "mid print (100-999), low wishlist (last resort)"
        unknown_weak = [card for card in unknown if card.wishlist_count > 0]
        if unknown_weak:
            return _best_of(unknown_weak), "no print info, low wishlist (last resort)"
    return None, ""


def choose_card(evaluation: Evaluation) -> EvaluatedCard | None:
    """The chosen card under the tiered grabbing rules (see explain_choice)."""
    card, _reason = explain_choice(evaluation)
    return card
