import unittest
from unittest import mock

from evaluator import choose_card, parse_evaluator_message


class EvaluatorMessageTests(unittest.TestCase):
    def test_parses_numbered_entries_and_expiration(self) -> None:
        message = "\n".join(
            (
                "<:yel_flower:1382527541130170538> <:no_1:1382526208822083727> "
                "`♡` `2` · **Hogan** · Pluto",
                "<:yel_flower:1382527541130170538> <:no_2:1382526232914296912> "
                "`♡` `4` · **Albert Elfrieden** · How a Realist Hero Rebuilt the Kingdom",
                "Drop expires <t:1793755181:R>.",
            )
        )

        evaluation = parse_evaluator_message(message)

        self.assertIsNotNone(evaluation)
        assert evaluation is not None
        self.assertEqual(evaluation.expires_at, 1793755181)
        self.assertEqual(
            [(card.slot, card.wishlist_count, card.name, card.series) for card in evaluation.cards],
            [
                (1, 2, "Hogan", "Pluto"),
                (2, 4, "Albert Elfrieden", "How a Realist Hero Rebuilt the Kingdom"),
            ],
        )

    def test_chooses_highest_count_with_lowest_slot_tie_break(self) -> None:
        evaluation = parse_evaluator_message(
            "<:no_3:3> `♡` `5` · **Third** · Series\n"
            "<:no_2:2> `♡` `5` · **Second** · Series\n"
            "<:no_1:1> `♡` `1` · **First** · Series"
        )

        self.assertIsNotNone(evaluation)
        assert evaluation is not None
        chosen = choose_card(evaluation)
        self.assertIsNotNone(chosen)
        assert chosen is not None
        self.assertEqual((chosen.slot, chosen.name), (2, "Second"))

    def test_prefers_lowest_print_number_at_or_below_999(self) -> None:
        evaluation = parse_evaluator_message(
            "<:no_1:1> `♡` `27` · `#2792` · `◈3` · **Higher** · Series\n"
            "<:no_2:2> `♡` `0` · `#1` · `◈2` · **Lowest** · Series\n"
            "<:no_3:3> `♡` `1` · `#999` · `◈1` · **Boundary** · Series"
        )

        self.assertIsNotNone(evaluation)
        assert evaluation is not None
        self.assertEqual(
            [card.print_number for card in evaluation.cards],
            [2792, 1, 999],
        )
        chosen = choose_card(evaluation)
        self.assertIsNotNone(chosen)
        assert chosen is not None
        self.assertEqual((chosen.slot, chosen.name), (2, "Lowest"))

    def test_falls_back_to_wishlist_when_all_print_numbers_exceed_999(self) -> None:
        evaluation = parse_evaluator_message(
            "<:no_1:1> `♡` `27` · `#2792` · `◈3` · **Higher** · Series\n"
            "<:no_2:2> `♡` `1` · `#1000` · `◈1` · **Next** · Series"
        )

        self.assertIsNotNone(evaluation)
        assert evaluation is not None
        chosen = choose_card(evaluation)
        self.assertIsNotNone(chosen)
        assert chosen is not None
        self.assertEqual((chosen.slot, chosen.name), (1, "Higher"))

    def test_strict_mode_skips_when_all_print_numbers_exceed_999(self) -> None:
        evaluation = parse_evaluator_message(
            "<:no_1:1> `♡` `27` · `#2792` · `◈3` · **Higher** · Series\n"
            "<:no_2:2> `♡` `1` · `#1000` · `◈1` · **Next** · Series"
        )

        self.assertIsNotNone(evaluation)
        assert evaluation is not None
        with mock.patch("evaluator.FALLBACK_TO_WISHLIST", False):
            self.assertIsNone(choose_card(evaluation))

    def test_skips_when_all_prints_high_and_all_counts_zero(self) -> None:
        evaluation = parse_evaluator_message(
            "<:no_1:1> `♡` `0` · `#2792` · **First** · Series\n"
            "<:no_2:2> `♡` `0` · `#1000` · **Second** · Series"
        )

        self.assertIsNotNone(evaluation)
        assert evaluation is not None
        self.assertIsNone(choose_card(evaluation))

    def test_parses_real_cardcompanion_drop_with_footer_lines(self) -> None:
        """Exact message observed in production, including -# subtext lines."""
        message = "\n".join(
            (
                "<:yel_flower:1382527541130170538> <:no_1:1382526208822083727> `♡` `0` · `#85233` · `◈1` · **Kouichiro Kase** · Knight Hunters",
                "<:yel_flower:1382527541130170538> <:no_2:1382526232914296912> `♡` `2` · `#85260` · `◈1` · **Shuu Maiko** · Nisekoi",
                "<:yel_flower:1382527541130170538> <:no_3:1382526252312825887> `♡` `1` · `#13514` · `◈1` · **VISCO** · Marvel Future Avengers",
                "Drop expires <t:1793921154:R>.",
                "-# Drop time: 125ms search time: 0.02ms",
                "-# Tip: Remove characters from your wishlist by using `c!wishremove`",
            )
        )

        evaluation = parse_evaluator_message(message)

        self.assertIsNotNone(evaluation)
        assert evaluation is not None
        self.assertEqual(evaluation.expires_at, 1793921154)
        self.assertEqual(
            [
                (card.slot, card.wishlist_count, card.print_number, card.name)
                for card in evaluation.cards
            ],
            [
                (1, 0, 85233, "Kouichiro Kase"),
                (2, 2, 85260, "Shuu Maiko"),
                (3, 1, 13514, "VISCO"),
            ],
        )
        chosen = choose_card(evaluation)
        self.assertIsNotNone(chosen)
        assert chosen is not None
        self.assertEqual((chosen.slot, chosen.name), (2, "Shuu Maiko"))

    def test_parses_real_cardcompanion_drop_with_rotating_tip(self) -> None:
        """A second production capture: different tip line, '!' in series name."""
        message = "\n".join(
            (
                "<:yel_flower:1382527541130170538> <:no_1:1382526208822083727> `♡` `0` · `#85143` · `◈1` · **Queen Mirage** · Happiness Charge Precure!",
                "<:yel_flower:1382527541130170538> <:no_2:1382526232914296912> `♡` `1` · `#18481` · `◈2` · **Shou Daliuwan** · Scissor Seven",
                "<:yel_flower:1382527541130170538> <:no_3:1382526252312825887> `♡` `0` · `#85334` · `◈1` · **Gingar** · Ozanari Dungeon",
                "Drop expires <t:1793927620:R>.",
                "-# Drop time: 125ms search time: 0.02ms",
                "-# Tip: Want drop embeds to only show for good cards? Configure a channel override in the **General & Misc Config** category of `/config karuta view`",
            )
        )

        evaluation = parse_evaluator_message(message)

        self.assertIsNotNone(evaluation)
        assert evaluation is not None
        self.assertEqual(evaluation.expires_at, 1793927620)
        self.assertEqual(
            [
                (card.slot, card.wishlist_count, card.print_number, card.name, card.series)
                for card in evaluation.cards
            ],
            [
                (1, 0, 85143, "Queen Mirage", "Happiness Charge Precure!"),
                (2, 1, 18481, "Shou Daliuwan", "Scissor Seven"),
                (3, 0, 85334, "Gingar", "Ozanari Dungeon"),
            ],
        )
        # All prints exceed 999, so the wishlist fallback picks the only
        # wished card: Shou Daliuwan (slot 2).
        chosen = choose_card(evaluation)
        self.assertIsNotNone(chosen)
        assert chosen is not None
        self.assertEqual((chosen.slot, chosen.name), (2, "Shou Daliuwan"))

    def test_skips_when_all_wishlist_counts_are_zero(self) -> None:
        evaluation = parse_evaluator_message(
            "<:no_1:1> `♡` `0` · **First** · Series\n"
            "<:no_2:2> `♡` `0` · **Second** · Series"
        )

        self.assertIsNotNone(evaluation)
        assert evaluation is not None
        self.assertIsNone(choose_card(evaluation))

    def test_rejects_duplicate_and_out_of_range_slots(self) -> None:
        self.assertIsNone(
            parse_evaluator_message(
                "<:no_1:1> `♡` `1` · **First** · Series\n"
                "<:no_1:2> `♡` `2` · **Duplicate** · Series"
            )
        )
        self.assertIsNone(
            parse_evaluator_message("<:no_5:5> `♡` `1` · **Fifth** · Series")
        )

    def test_returns_none_for_unrelated_message(self) -> None:
        self.assertIsNone(parse_evaluator_message("Drop expires <t:1793755181:R>."))


if __name__ == "__main__":
    unittest.main()
