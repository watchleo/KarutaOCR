import unittest
from unittest import mock

from evaluator import choose_card, explain_choice, parse_evaluator_message


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

    # ---- rule 1: prints 1-9 always grabbed, ranked by wishlist count ----

    def test_sp_ranked_by_wishlist_not_lower_print(self) -> None:
        """Rule 1 example: print #7 with 500 wishes beats print #2 with 100."""
        evaluation = parse_evaluator_message(
            "<:no_1:1> `♡` `100` · `#2` · **Two** · Series\n"
            "<:no_2:2> `♡` `500` · `#7` · **Seven** · Series"
        )

        self.assertIsNotNone(evaluation)
        assert evaluation is not None
        chosen, reason = explain_choice(evaluation)
        self.assertEqual((chosen.slot, chosen.name), (2, "Seven"))
        self.assertEqual(reason, "single print (1-9)")

    def test_sp_with_zero_wishes_beats_every_higher_range(self) -> None:
        """Range first: a #5 print with no wishes outranks a heavily wished
        mid print and a heavily wished high print."""
        evaluation = parse_evaluator_message(
            "<:no_1:1> `♡` `500` · `#5000` · **Bulk** · Series\n"
            "<:no_2:2> `♡` `0` · `#5` · **Single** · Series\n"
            "<:no_3:3> `♡` `50` · `#800` · **Mid** · Series"
        )

        self.assertIsNotNone(evaluation)
        assert evaluation is not None
        chosen = choose_card(evaluation)
        self.assertIsNotNone(chosen)
        assert chosen is not None
        self.assertEqual((chosen.slot, chosen.name), (2, "Single"))

    # ---- rule 2: prints 10-99 always grabbed, ranked by wishlist count ----

    def test_lp_ranked_by_wishlist_not_lower_print(self) -> None:
        """Rule 2 example: print #99 with 40 wishes beats print #11 with 20."""
        evaluation = parse_evaluator_message(
            "<:no_1:1> `♡` `20` · `#11` · **Eleven** · Series\n"
            "<:no_2:2> `♡` `40` · `#99` · **NinetyNine** · Series"
        )

        self.assertIsNotNone(evaluation)
        assert evaluation is not None
        chosen, reason = explain_choice(evaluation)
        self.assertEqual((chosen.slot, chosen.name), (2, "NinetyNine"))
        self.assertEqual(reason, "low print (10-99)")

    # ---- rule 3: prints 100-999 prefer 7+ wishes ----

    def test_mp_with_7plus_wishes_beats_hp_with_more_wishes(self) -> None:
        """Meta-rule: a qualifying mid print (#500, ♡8) is never beaten by a
        lower-priority high print (#5000, ♡500) just because of wishes."""
        evaluation = parse_evaluator_message(
            "<:no_1:1> `♡` `500` · `#5000` · **Bulk** · Series\n"
            "<:no_2:2> `♡` `8` · `#500` · **Mid** · Series"
        )

        self.assertIsNotNone(evaluation)
        assert evaluation is not None
        chosen, reason = explain_choice(evaluation)
        self.assertEqual((chosen.slot, chosen.name), (2, "Mid"))
        self.assertEqual(reason, "mid print (100-999), wishlist 7+")

    def test_mp_low_wishlist_cards_are_ranked_by_wishes_as_last_resort(self) -> None:
        """No card reaches 7 wishes: the mid-print last resort still ranks by
        wishlist count (#800, ♡5 over #150, ♡3)."""
        evaluation = parse_evaluator_message(
            "<:no_1:1> `♡` `3` · `#150` · **LowCount** · Series\n"
            "<:no_2:2> `♡` `5` · `#800` · **HighCount** · Series"
        )

        self.assertIsNotNone(evaluation)
        assert evaluation is not None
        chosen, reason = explain_choice(evaluation)
        self.assertEqual((chosen.slot, chosen.name), (2, "HighCount"))
        self.assertEqual(reason, "mid print (100-999), low wishlist (last resort)")

    def test_last_resort_disabled_skips_low_wishlist_drops(self) -> None:
        evaluation = parse_evaluator_message(
            "<:no_1:1> `♡` `3` · `#150` · **First** · Series\n"
            "<:no_2:2> `♡` `2` · `#800` · **Second** · Series"
        )

        self.assertIsNotNone(evaluation)
        assert evaluation is not None
        with mock.patch("evaluator.GRAB_LAST_RESORT", False):
            self.assertIsNone(choose_card(evaluation))

    def test_last_resort_disabled_still_grabs_7plus_cards(self) -> None:
        """The 7+ rules are unconditional: disabling the last resort must not
        skip a qualifying high-print card."""
        evaluation = parse_evaluator_message(
            "<:no_1:1> `♡` `27` · `#2792` · **Higher** · Series\n"
            "<:no_2:2> `♡` `1` · `#1000` · **Next** · Series"
        )

        self.assertIsNotNone(evaluation)
        assert evaluation is not None
        with mock.patch("evaluator.GRAB_LAST_RESORT", False):
            chosen = choose_card(evaluation)
            self.assertIsNotNone(chosen)
            assert chosen is not None
            self.assertEqual((chosen.slot, chosen.name), (1, "Higher"))

    # ---- rule 4: prints 1000+ only with 7+ wishes ----

    def test_hp_with_7plus_wishes_is_grabbed(self) -> None:
        """The 2026-11-06 drop with Yuki Mitsuya at ♡10: the only qualifying
        card (high print, 7+ wishes) is grabbed even against ♡1 bulk."""
        evaluation = parse_evaluator_message(
            "<:no_1:1> `♡` `1` · `#85420` · **Rick** · Series\n"
            "<:no_2:2> `♡` `10` · `#44190` · **Yuki Mitsuya** · ODDTAXI\n"
            "<:no_3:3> `♡` `1` · `#85487` · **Moeka China** · Series"
        )

        self.assertIsNotNone(evaluation)
        assert evaluation is not None
        chosen, reason = explain_choice(evaluation)
        self.assertEqual((chosen.slot, chosen.name), (2, "Yuki Mitsuya"))
        self.assertEqual(reason, "high print (1000+), wishlist 7+")

    def test_hp_with_fewer_than_7_wishes_is_never_grabbed(self) -> None:
        """Production regression (2026-11-06 05:17 drop): three high prints
        with ♡1 each.  Rule 4 forbids grabbing any of them - the correct
        action is NO grab at all, not 'the rarest of the ties'."""
        message = "\n".join(
            (
                "<:yel_flower:1382527541130170538> <:no_1:1382526208822083727> `♡` `1` · `#85420` · `◈1` · **Rick** · Final Fantasy VII: On the Way to a Smile, Episode: Denzel",
                "<:yel_flower:1382527541130170538> <:no_2:1382526232914296912> `♡` `1` · `#44190` · `◈2` · **Yuki Mitsuya** · ODDTAXI",
                "<:yel_flower:1382527541130170538> <:no_3:1382526252312825887> `♡` `1` · `#85487` · `◈1` · **Moeka China** · High School Fleet",
                "Drop expires <t:1793935115:R>.",
                "Drop time: 91ms search time: 0.02ms",
            )
        )

        evaluation = parse_evaluator_message(message)

        self.assertIsNotNone(evaluation)
        assert evaluation is not None
        self.assertEqual(len(evaluation.cards), 3)
        self.assertIsNone(choose_card(evaluation))

    def test_unknown_print_low_wishlist_beats_hp_low_wishlist(self) -> None:
        """A high print with ♡2 is never grabbed; a print-less card with the
        same count is still taken as the last resort."""
        evaluation = parse_evaluator_message(
            "<:no_1:1> `♡` `2` · `#90000` · **Known** · Series\n"
            "<:no_2:2> `♡` `2` · **Unknown** · Series"
        )

        self.assertIsNotNone(evaluation)
        assert evaluation is not None
        chosen = choose_card(evaluation)
        self.assertIsNotNone(chosen)
        assert chosen is not None
        self.assertEqual((chosen.slot, chosen.name), (2, "Unknown"))

    def test_equal_wishes_and_print_falls_back_to_lowest_slot(self) -> None:
        evaluation = parse_evaluator_message(
            "<:no_2:2> `♡` `8` · `#500` · **Later** · Series\n"
            "<:no_1:1> `♡` `8` · `#500` · **Earlier** · Series"
        )

        self.assertIsNotNone(evaluation)
        assert evaluation is not None
        chosen = choose_card(evaluation)
        self.assertIsNotNone(chosen)
        assert chosen is not None
        self.assertEqual((chosen.slot, chosen.name), (1, "Earlier"))

    # ---- no print info at all: pure wishlist ladder ----

    def test_no_print_info_prefers_7plus_then_any_positive(self) -> None:
        evaluation = parse_evaluator_message(
            "<:no_1:1> `♡` `3` · **Weak** · Series\n"
            "<:no_2:2> `♡` `10` · **Strong** · Series"
        )

        self.assertIsNotNone(evaluation)
        assert evaluation is not None
        chosen, reason = explain_choice(evaluation)
        self.assertEqual((chosen.slot, chosen.name), (2, "Strong"))
        self.assertEqual(reason, "no print info, wishlist 7+")

        only_weak = parse_evaluator_message(
            "<:no_1:1> `♡` `3` · **Weak** · Series\n"
            "<:no_2:2> `♡` `1` · **Weaker** · Series"
        )
        assert only_weak is not None
        chosen, reason = explain_choice(only_weak)
        self.assertEqual((chosen.slot, chosen.name), (1, "Weak"))
        self.assertEqual(reason, "wishlist only (no print info)")

    def test_single_print_beats_mid_print_regardless_of_wishes(self) -> None:
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

    def test_hp_qualifying_card_wins_over_non_qualifying_one(self) -> None:
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
        # All prints are 1000+ and every wish count is below 7: rule 4
        # forbids grabbing any of them.
        self.assertIsNone(choose_card(evaluation))

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
        # All prints are 1000+ and every wish count is below 7: rule 4
        # forbids grabbing any of them.
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
