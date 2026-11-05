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

    def test_prioritizes_best_print_range_then_highest_wishlist_count(self) -> None:
        evaluation = parse_evaluator_message(
            "<:no_1:1> `♡` `2` · `#10` · `◈3` · **Lower wishes** · Series\n"
            "<:no_2:2> `♡` `10` · `#90` · `◈2` · **Higher wishes** · Series\n"
            "<:no_3:3> `♡` `68` · `#1000` · `◈1` · **Higher range** · Series"
        )

        self.assertIsNotNone(evaluation)
        assert evaluation is not None
        self.assertEqual(
            [card.print_number for card in evaluation.cards],
            [10, 90, 1000],
        )
        chosen = choose_card(evaluation)
        self.assertIsNotNone(chosen)
        assert chosen is not None
        self.assertEqual((chosen.slot, chosen.name), (2, "Higher wishes"))

    def test_prints_1_to_9_are_eligible_even_without_wishlists(self) -> None:
        evaluation = parse_evaluator_message(
            "<:no_1:1> `♡` `0` · `#9` · `◈3` · **High wish count** · Series\n"
            "<:no_2:2> `♡` `0` · `#1` · `◈1` · **Low print** · Series\n"
            "<:no_3:3> `♡` `68` · `#100` · `◈1` · **Lower priority** · Series"
        )

        self.assertIsNotNone(evaluation)
        assert evaluation is not None
        chosen = choose_card(evaluation)
        self.assertIsNotNone(chosen)
        assert chosen is not None
        self.assertEqual((chosen.slot, chosen.name), (1, "High wish count"))

    def test_prints_10_to_99_beat_higher_ranges_and_use_wishlist_count(self) -> None:
        evaluation = parse_evaluator_message(
            "<:no_1:1> `♡` `1` · `#10` · `◈1` · **First** · Series\n"
            "<:no_2:2> `♡` `8` · `#99` · `◈1` · **Most wished** · Series\n"
            "<:no_3:3> `♡` `68` · `#100` · `◈1` · **Higher range** · Series"
        )

        self.assertIsNotNone(evaluation)
        assert evaluation is not None
        chosen = choose_card(evaluation)
        self.assertIsNotNone(chosen)
        assert chosen is not None
        self.assertEqual((chosen.slot, chosen.name), (2, "Most wished"))

    def test_prints_100_to_999_require_wishlist_and_pick_highest_count(self) -> None:
        evaluation = parse_evaluator_message(
            "<:no_1:1> `♡` `0` · `#100` · `◈1` · **No wishes** · Series\n"
            "<:no_2:2> `♡` `3` · `#999` · `◈1` · **Some wishes** · Series\n"
            "<:no_3:3> `♡` `7` · `#1000` · `◈1` · **Higher range** · Series"
        )

        self.assertIsNotNone(evaluation)
        assert evaluation is not None
        chosen = choose_card(evaluation)
        self.assertIsNotNone(chosen)
        assert chosen is not None
        self.assertEqual((chosen.slot, chosen.name), (2, "Some wishes"))

    def test_prints_1000_plus_require_seven_wishes(self) -> None:
        below_threshold = parse_evaluator_message(
            "<:no_1:1> `♡` `6` · `#1000` · `◈1` · **Below threshold** · Series\n"
            "<:no_2:2> `♡` `2` · `#10000` · `◈1` · **Also below** · Series"
        )
        at_threshold = parse_evaluator_message(
            "<:no_1:1> `♡` `6` · `#1000` · `◈1` · **Below threshold** · Series\n"
            "<:no_2:2> `♡` `7` · `#10000` · `◈1` · **At threshold** · Series"
        )

        self.assertIsNotNone(below_threshold)
        self.assertIsNotNone(at_threshold)
        assert below_threshold is not None
        assert at_threshold is not None
        self.assertIsNone(choose_card(below_threshold))
        chosen = choose_card(at_threshold)
        self.assertIsNotNone(chosen)
        assert chosen is not None
        self.assertEqual((chosen.slot, chosen.name), (2, "At threshold"))

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
