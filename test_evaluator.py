import unittest

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

    def test_skips_printed_cards_when_all_print_numbers_exceed_999(self) -> None:
        evaluation = parse_evaluator_message(
            "<:no_1:1> `♡` `27` · `#2792` · `◈3` · **Higher** · Series\n"
            "<:no_2:2> `♡` `1` · `#1000` · `◈1` · **Next** · Series"
        )

        self.assertIsNotNone(evaluation)
        assert evaluation is not None
        self.assertIsNone(choose_card(evaluation))

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
