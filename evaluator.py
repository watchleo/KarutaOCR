from __future__ import annotations

import re
from dataclasses import dataclass


_CARD_LINE = re.compile(
    r"(?:<a?:[\w~]+:\d+>\s*)*"
    r"<a?:no_(?P<slot>\d+):\d+>\s*`♡`\s*`?(?P<count>\d+)`?"
    r"(?:\s*·\s*`?#(?P<print_number>\d+)`?)?"
    r"(?:\s*·\s*`?◈\d+`?)?"
    r"\s*·\s*\*\*(?P<name>.+?)\*\*\s*·\s*(?P<series>.+?)\s*$"
)
_EXPIRATION = re.compile(r"<t:(\d+)(?::[tTdDfFR])?>")
PRINT_PRIORITY_RANGES = ((1, 9), (10, 99), (100, 999))
MIN_WISHLIST_FOR_PRINTS_100_TO_999 = 1
MIN_WISHLIST_FOR_PRINTS_1000_PLUS = 7


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


def parse_evaluator_message(content: str) -> Evaluation | None:
    """Parse numbered wishlist entries and the optional Discord expiry timestamp."""
    cards: list[EvaluatedCard] = []
    for line in content.splitlines():
        match = _CARD_LINE.search(line.strip())
        if match is None:
            continue

        slot = int(match.group("slot"))
        name = match.group("name").strip()
        series = match.group("series").strip()
        if not 1 <= slot <= 4 or not name or not series:
            continue

        cards.append(
            EvaluatedCard(
                slot=slot,
                wishlist_count=int(match.group("count")),
                name=name,
                series=series,
                print_number=(
                    int(match.group("print_number"))
                    if match.group("print_number")
                    else None
                ),
            )
        )

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
    """Choose by print-number band, then wishlist count within that band."""
    numbered_cards = [
        card for card in evaluation.cards if card.print_number is not None
    ]
    if not numbered_cards:
        eligible = [card for card in evaluation.cards if card.wishlist_count > 0]
        if not eligible:
            return None
        return max(eligible, key=lambda card: (card.wishlist_count, -card.slot))

    for lower, upper in PRINT_PRIORITY_RANGES:
        eligible = [
            card
            for card in numbered_cards
            if card.print_number is not None
            and lower <= card.print_number <= upper
            and (
                upper < 100
                or card.wishlist_count >= MIN_WISHLIST_FOR_PRINTS_100_TO_999
            )
        ]
        if eligible:
            return max(eligible, key=lambda card: (card.wishlist_count, -card.slot))

    high_print_cards = [
        card
        for card in numbered_cards
        if card.print_number is not None
        and card.print_number >= 1000
        and card.wishlist_count >= MIN_WISHLIST_FOR_PRINTS_1000_PLUS
    ]
    if high_print_cards:
        return max(high_print_cards, key=lambda card: (card.wishlist_count, -card.slot))

    return None
