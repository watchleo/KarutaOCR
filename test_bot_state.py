import asyncio
import discord
from datetime import datetime, timedelta, timezone
import os
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from bot import (
    IS_COMPONENTS_V2_FLAG,
    KarutaClient,
    PendingDrop,
    _acquire_guard,
    _first_non_number_emoji,
    _first_non_number_emoji_raw,
    _is_image_message,
    _looks_like_evaluator_output,
    _text_from_raw,
    load_config,
)
from stealth import CaptchaRequired, console_captcha_handler


def _analysis_content(expires_in: int = 50) -> str:
    """A real CardCompanion-style analysis with a controllable expiry."""
    expiry = int(time.time() + expires_in)
    return (
        "<:yel_flower:1382527541130170538> <:no_1:1382526208822083727> `♡` `0` · `#85143` · `◈1` · **Queen Mirage** · Happiness Charge Precure!\n"
        "<:yel_flower:1382527541130170538> <:no_2:1382526232914296912> `♡` `1` · `#18481` · `◈2` · **Shou Daliuwan** · Scissor Seven\n"
        "<:yel_flower:1382527541130170538> <:no_3:1382526252312825887> `♡` `0` · `#85334` · `◈1` · **Gingar** · Ozanari Dungeon\n"
        f"Drop expires <t:{expiry}:R>.\n"
        "-# Drop time: 125ms search time: 0.02ms\n"
        "-# Tip: Want drop embeds to only show for good cards? Configure a channel override in the **General & Misc Config** category of `/config karuta view`"
    )


class BotStateTests(unittest.TestCase):
    def test_save_state_logs_write_errors_without_raising(self) -> None:
        error = OSError("disk full")
        store = SimpleNamespace(load=Mock(return_value={}), save=Mock(side_effect=error))
        client = SimpleNamespace(store=store, last_command_at=123.0)

        with patch("bot.log.error") as log_error:
            KarutaClient._save_state(client)

        log_error.assert_called_once_with("State write failed: %s", error)

    def test_scheduler_callback_logs_task_errors(self) -> None:
        error = RuntimeError("scheduler failed")
        task = SimpleNamespace(
            cancelled=Mock(return_value=False),
            exception=Mock(return_value=error),
        )

        with patch("bot.log.error") as log_error:
            KarutaClient._log_scheduler_result(task)

        log_error.assert_called_once_with("Scheduler died: %r", error)

    def test_scheduler_callback_ignores_cancellation(self) -> None:
        task = SimpleNamespace(cancelled=Mock(return_value=True))

        with patch("bot.log.error") as log_error:
            KarutaClient._log_scheduler_result(task)

        log_error.assert_not_called()

    def test_acquire_guard_retries_until_lock_is_available(self) -> None:
        guard = SimpleNamespace(acquire=Mock(side_effect=[False, False, True]))

        with patch("bot.time.sleep") as sleep:
            _acquire_guard(guard)

        self.assertEqual(guard.acquire.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_ready_resets_connection_backoff(self) -> None:
        client = SimpleNamespace(
            user="test-user",
            _ready_count=0,
            pending_drops={1: object()},
            _seen_announcements=[1],
            backoff=SimpleNamespace(reset=Mock()),
            _spawn_scheduler=Mock(),
            _presence=SimpleNamespace(start=Mock()),
        )

        asyncio.run(KarutaClient.on_ready(client))

        client.backoff.reset.assert_called_once_with()
        client._spawn_scheduler.assert_called_once_with()
        client._presence.start.assert_called_once_with()
        self.assertEqual(client.pending_drops, {})
        self.assertEqual(client._seen_announcements, [])

    def test_load_config_names_invalid_channel_value(self) -> None:
        with (
            patch("bot._load_config_file"),
            patch.dict(
                os.environ,
                {
                    "DISCORD_USER_TOKEN": "test-token",
                    "TARGET_CHANNELS": "123,not-a-channel",
                    "DROP_BOT_ID": "456",
                },
                clear=True,
            ),
        ):
            with self.assertRaisesRegex(
                ValueError, "TARGET_CHANNELS.*'not-a-channel'"
            ):
                load_config()

    def test_chunking_is_disabled_by_default(self) -> None:
        with (
            patch("bot._load_config_file"),
            patch.dict(
                os.environ,
                {
                    "DISCORD_USER_TOKEN": "test-token",
                    "TARGET_CHANNELS": "123",
                    "DROP_BOT_ID": "456",
                },
                clear=True,
            ),
        ):
            self.assertFalse(load_config().chunk_guilds)

    def test_empty_panel_values_fall_back_to_dotenv(self) -> None:
        file_values = {
            "DISCORD_USER_TOKEN": "file-token",
            "TARGET_CHANNELS": "123",
            "DROP_BOT_ID": "456",
        }
        with (
            patch("bot.dotenv_values", return_value=file_values),
            patch.dict(
                os.environ,
                {
                    "DISCORD_USER_TOKEN": "",
                    "TARGET_CHANNELS": "",
                    "DROP_BOT_ID": "",
                },
                clear=True,
            ),
        ):
            config = load_config()

        self.assertEqual(config.token, "file-token")
        self.assertEqual(config.target_channels, (123,))

    def test_nonempty_panel_values_override_dotenv(self) -> None:
        file_values = {
            "DISCORD_USER_TOKEN": "file-token",
            "TARGET_CHANNELS": "123",
            "DROP_BOT_ID": "456",
        }
        with (
            patch("bot.dotenv_values", return_value=file_values),
            patch.dict(
                os.environ,
                {
                    "DISCORD_USER_TOKEN": "panel-token",
                    "TARGET_CHANNELS": "",
                    "DROP_BOT_ID": "",
                },
                clear=True,
            ),
        ):
            config = load_config()

        self.assertEqual(config.token, "panel-token")
        self.assertEqual(config.target_channels, (123,))

    def test_captcha_handler_requires_configured_key_without_prompting(self) -> None:
        captcha = SimpleNamespace(service="hcaptcha", sitekey="site-key", rqdata="data")
        with (
            patch.dict(os.environ, {"CAPTCHA_KEY": ""}, clear=False),
            patch("stealth.CAPTCHA_RETRY_DELAY_SECONDS", 0.0),
            patch("stealth._captcha_uses", 0),
            patch("stealth.log.error") as log_error,
        ):
            with self.assertRaises(CaptchaRequired):
                asyncio.run(console_captcha_handler(captcha, None))
        log_error.assert_called()

    def test_captcha_handler_returns_configured_key(self) -> None:
        with (
            patch.dict(os.environ, {"CAPTCHA_KEY": "provided-response"}),
            patch("stealth._captcha_uses", 0),
        ):
            result = asyncio.run(console_captcha_handler(SimpleNamespace(), None))
        self.assertEqual(result, "provided-response")

    def test_captcha_handler_treats_second_use_as_stale(self) -> None:
        with (
            patch.dict(os.environ, {"CAPTCHA_KEY": "old"}),
            patch("stealth.CAPTCHA_RETRY_DELAY_SECONDS", 0.0),
            patch("stealth._captcha_uses", 1),
        ):
            with self.assertRaises(CaptchaRequired):
                asyncio.run(console_captcha_handler(SimpleNamespace(), None))

    def test_unparseable_evaluator_logs_message_text(self) -> None:
        channel_id = 123
        now = datetime.now(timezone.utc)
        pending = PendingDrop(
            announcement_id=1,
            expires_at=(now + timedelta(seconds=59)).timestamp(),
        )
        client = object.__new__(KarutaClient)
        client.config = SimpleNamespace(target_channels=(channel_id,))
        client.pending_drops = {channel_id: pending}
        message_text = "Unexpected ♡ evaluator format"
        embeds = [
            SimpleNamespace(
                title=None,
                description="",
                fields=[],
                footer=None,
            )
        ]
        evaluator_message = SimpleNamespace(
            id=789,
            author=SimpleNamespace(id=456),
            channel=SimpleNamespace(id=channel_id),
            created_at=now,
            content=message_text,
            embeds=embeds,
        )

        with patch("bot.log.warning") as log_warning:
            asyncio.run(
                KarutaClient._handle_evaluator_message(client, evaluator_message)
            )

        log_warning.assert_called_once_with(
            "Evaluator output looks like an analysis but had no parseable "
            "entries (author_id=%s message_id=%s embeds=%d): %s",
            456,
            789,
            1,
            repr(message_text)[:400],
        )
        self.assertIsNone(pending.evaluation)

    def test_notice_evaluator_message_is_not_a_warning(self) -> None:
        channel_id = 123
        now = datetime.now(timezone.utc)
        pending = PendingDrop(
            announcement_id=1,
            expires_at=(now + timedelta(seconds=59)).timestamp(),
        )
        client = object.__new__(KarutaClient)
        client.config = SimpleNamespace(target_channels=(channel_id,))
        client.pending_drops = {channel_id: pending}
        evaluator_message = SimpleNamespace(
            id=789,
            author=SimpleNamespace(id=456),
            channel=SimpleNamespace(id=channel_id),
            created_at=now,
            content="A drop ping went out to 3 users.",
            embeds=[],
        )

        with patch("bot.log.warning") as log_warning:
            asyncio.run(
                KarutaClient._handle_evaluator_message(client, evaluator_message)
            )

        log_warning.assert_not_called()
        self.assertIsNone(pending.evaluation)

    def test_empty_evaluator_message_is_refetched_until_parseable(self) -> None:
        channel_id = 123
        now = datetime.now(timezone.utc)
        pending = PendingDrop(
            announcement_id=1,
            expires_at=(now + timedelta(seconds=59)).timestamp(),
        )
        client = object.__new__(KarutaClient)
        client.config = SimpleNamespace(target_channels=(channel_id,))
        client.pending_drops = {channel_id: pending}
        client.http = SimpleNamespace(
            get_message=AsyncMock(
                return_value={
                    "id": 789,
                    "content": "<:no_1:1> `♡` `2` · **First** · Series",
                    "embeds": [],
                }
            )
        )
        placeholder = SimpleNamespace(
            id=789,
            author=SimpleNamespace(id=456),
            channel=SimpleNamespace(id=channel_id),
            created_at=now,
            content="",
            embeds=[],
        )

        async def exercise() -> None:
            with patch("bot.asyncio.sleep", new_callable=AsyncMock):
                await KarutaClient._handle_evaluator_message(client, placeholder)

        asyncio.run(exercise())

        client.http.get_message.assert_awaited_with(channel_id, 789)
        self.assertIsNotNone(pending.evaluation)
        self.assertEqual(pending.evaluation.cards[0].name, "First")
        self.assertFalse(pending.evaluation_processed)

    def test_stale_evaluator_edit_cannot_feed_current_drop(self) -> None:
        channel_id = 123
        now = datetime.now(timezone.utc)
        pending = PendingDrop(
            announcement_id=1,
            expires_at=(now + timedelta(seconds=59)).timestamp(),
            announced_at=now.timestamp(),
        )
        client = object.__new__(KarutaClient)
        client.config = SimpleNamespace(target_channels=(channel_id,))
        client.pending_drops = {channel_id: pending}
        stale_edit = SimpleNamespace(
            id=789,
            author=SimpleNamespace(id=456),
            channel=SimpleNamespace(id=channel_id),
            created_at=now - timedelta(hours=1),
            content="<:no_1:1> `♡` `2` · **First** · Series",
            embeds=[],
        )

        asyncio.run(KarutaClient._handle_evaluator_message(client, stale_edit))

        self.assertIsNone(pending.evaluation)

    def test_evaluator_edit_is_processed(self) -> None:
        channel_id = 123
        now = datetime.now(timezone.utc)
        pending = PendingDrop(
            announcement_id=1,
            expires_at=(now + timedelta(seconds=59)).timestamp(),
        )
        client = object.__new__(KarutaClient)
        client._connection = SimpleNamespace(user=SimpleNamespace(id=999))
        client.config = SimpleNamespace(
            target_channels=(channel_id,), evaluator_bot_id=456, drop_bot_id=646
        )
        client.pending_drops = {channel_id: pending}
        client._evaluator_tasks = {}
        client._process_pending_drop = AsyncMock()
        before = SimpleNamespace()
        initial = SimpleNamespace(
            id=789,
            author=SimpleNamespace(id=456),
            channel=SimpleNamespace(id=channel_id),
            created_at=now,
            content="",
            embeds=[],
        )
        after = SimpleNamespace(
            id=789,
            author=SimpleNamespace(id=456),
            channel=SimpleNamespace(id=channel_id),
            created_at=now,
            content="",
            embeds=[
                SimpleNamespace(
                    title=None,
                    description="<:no_1:1> `♡` `2` · **First** · Series",
                    fields=[],
                    footer=None,
                )
            ],
        )

        async def exercise() -> None:
            with patch("bot.asyncio.sleep", new_callable=AsyncMock) as sleep:
                await KarutaClient.on_message_edit(client, before, initial)
                await KarutaClient.on_message_edit(client, initial, after)
                self.assertIsNone(pending.evaluation)
                task = client._evaluator_tasks[after.id]
                await task
                sleep.assert_awaited_once_with(3.0)

        asyncio.run(exercise())

        self.assertIsNotNone(pending.evaluation)
        self.assertEqual(pending.evaluation.cards[0].name, "First")
        client._process_pending_drop.assert_awaited_once_with(channel_id, pending)

    def test_evaluator_arriving_before_card_image_is_processed_later(self) -> None:
        channel_id = 123
        now = datetime.now(timezone.utc)
        pending = PendingDrop(
            announcement_id=1,
            expires_at=(now + timedelta(seconds=59)).timestamp(),
        )
        claim = AsyncMock()
        client = object.__new__(KarutaClient)
        client.config = SimpleNamespace(target_channels=(channel_id,))
        client.pending_drops = {channel_id: pending}
        client._claim_card = claim
        evaluator_message = SimpleNamespace(
            channel=SimpleNamespace(id=channel_id),
            created_at=now,
            content="<:no_1:1> `♡` `2` · **First** · Series",
            embeds=[],
        )
        image_message = SimpleNamespace(
            id=2,
            channel=SimpleNamespace(id=channel_id),
            created_at=now + timedelta(seconds=1),
            attachments=[SimpleNamespace(content_type="image/png", filename="card.png")],
        )

        async def exercise() -> None:
            await KarutaClient._handle_evaluator_message(client, evaluator_message)
            self.assertIsNotNone(pending.evaluation)
            self.assertFalse(pending.evaluation_processed)
            await KarutaClient._attach_drop_image(client, image_message)

        asyncio.run(exercise())

        self.assertTrue(pending.evaluation_processed)
        claim.assert_awaited_once_with(image_message, 1, pending.expires_at)


    def test_evaluator_signature_detection(self) -> None:
        self.assertTrue(_looks_like_evaluator_output(_analysis_content()))
        # Keqing-style line: numbered, comma thousands, no backticked code.
        self.assertTrue(
            _looks_like_evaluator_output(
                "1. `♡1,448` **Rem** · Love Hina `#6,524`"
            )
        )
        self.assertFalse(_looks_like_evaluator_output("kd"))
        self.assertFalse(_looks_like_evaluator_output(""))
        self.assertFalse(
            _looks_like_evaluator_output(
                "I'm dropping 3 cards since we're active! Grab them quick!"
            )
        )
        # The rotating tip footer must not match on its own.
        self.assertFalse(
            _looks_like_evaluator_output(
                "-# Tip: Want drop embeds to only show for good cards? "
                "Configure a channel override in `/config karuta view`"
            )
        )

    def _watch_client(self, channel_id: int, pending: PendingDrop) -> object:
        client = object.__new__(KarutaClient)
        client._connection = SimpleNamespace(user=SimpleNamespace(id=999))
        client.config = SimpleNamespace(
            target_channels=(channel_id,),
            evaluator_bot_id=456,
            drop_bot_id=646,
        )
        client.pending_drops = {channel_id: pending}
        client._evaluator_tasks = {}
        client._hinted_foreign_evaluator = False
        client._process_pending_drop = AsyncMock()
        return client

    @staticmethod
    def _fake_message(
        message_id: int,
        author_id: int,
        channel_id: int,
        content: str,
        created_at: datetime,
        *,
        bot: bool = False,
        flags: int = 0,
        components: list | None = None,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            id=message_id,
            author=SimpleNamespace(id=author_id, bot=bot),
            channel=SimpleNamespace(id=channel_id),
            created_at=created_at,
            content=content,
            embeds=[],
            attachments=[],
            components=components or [],
            webhook_id=None,
            type=None,
            flags=SimpleNamespace(value=flags),
        )

    @staticmethod
    def _v2_raw_payload(message_id: int, expires_in: int = 50) -> dict:
        """A raw Components V2 payload shaped like CardCompanion's analysis.

        Text Display components (type 10) nested in a Section (9) inside a
        Container (17); content is forced empty, which is how discord.py-self
        versions without V2 support end up reading nothing.
        """
        expiry = int(time.time() + expires_in)
        return {
            "id": message_id,
            "content": "",
            "embeds": [],
            "flags": IS_COMPONENTS_V2_FLAG,
            "components": [
                {
                    "type": 17,
                    "components": [
                        {
                            "type": 9,
                            "components": [
                                {
                                    "type": 10,
                                    "content": (
                                        "<:yel_flower:1> <:no_1:1> `♡` `0` · `#85143` · `◈1` "
                                        "· **Queen Mirage** · Happiness Charge Precure!"
                                    ),
                                },
                                {
                                    "type": 10,
                                    "content": (
                                        "<:yel_flower:1> <:no_2:2> `♡` `1` · `#18481` · `◈2` "
                                        "· **Shou Daliuwan** · Scissor Seven"
                                    ),
                                },
                                {
                                    "type": 10,
                                    "content": (
                                        "<:yel_flower:1> <:no_3:3> `♡` `0` · `#85334` · `◈1` "
                                        "· **Gingar** · Ozanari Dungeon"
                                    ),
                                },
                            ],
                        },
                        {"type": 10, "content": f"Drop expires <t:{expiry}:R>."},
                        {"type": 14, "divider": True, "spacing": 1},
                        {
                            "type": 10,
                            "content": "-# Drop time: 125ms search time: 0.02ms",
                        },
                    ],
                }
            ],
        }

    def test_own_account_analysis_is_processed(self) -> None:
        """The core self-bot fix: the analysis is authored by THIS account."""
        channel_id = 123
        now = datetime.now(timezone.utc)
        pending = PendingDrop(
            announcement_id=1,
            expires_at=(now + timedelta(seconds=59)).timestamp(),
            announced_at=now.timestamp(),
        )
        client = self._watch_client(channel_id, pending)
        # Author 999 is the bot's own account (CardCompanion self-bot).
        message = self._fake_message(
            789, 999, channel_id, _analysis_content(), now
        )

        async def exercise() -> None:
            with patch("bot.asyncio.sleep", new_callable=AsyncMock):
                await KarutaClient.on_message(client, message)
                task = client._evaluator_tasks[message.id]
                await task

        asyncio.run(exercise())

        self.assertIsNotNone(pending.evaluation)
        self.assertEqual(
            [card.name for card in pending.evaluation.cards],
            ["Queen Mirage", "Shou Daliuwan", "Gingar"],
        )
        client._process_pending_drop.assert_awaited_once_with(channel_id, pending)

    def test_analysis_from_other_bot_is_processed(self) -> None:
        channel_id = 123
        now = datetime.now(timezone.utc)
        pending = PendingDrop(
            announcement_id=1,
            expires_at=(now + timedelta(seconds=59)).timestamp(),
            announced_at=now.timestamp(),
        )
        client = self._watch_client(channel_id, pending)
        # Author 777: neither this account nor the configured evaluator.
        message = self._fake_message(
            789, 777, channel_id, _analysis_content(), now, bot=True
        )

        async def exercise() -> None:
            with patch("bot.asyncio.sleep", new_callable=AsyncMock):
                await KarutaClient.on_message(client, message)
                task = client._evaluator_tasks[message.id]
                await task

        asyncio.run(exercise())

        self.assertIsNotNone(pending.evaluation)
        self.assertEqual(pending.evaluation.cards[1].name, "Shou Daliuwan")

    def test_plain_own_message_is_ignored(self) -> None:
        channel_id = 123
        now = datetime.now(timezone.utc)
        pending = PendingDrop(
            announcement_id=1,
            expires_at=(now + timedelta(seconds=59)).timestamp(),
        )
        client = self._watch_client(channel_id, pending)
        message = self._fake_message(789, 999, channel_id, "kd", now)

        async def exercise() -> None:
            await KarutaClient.on_message(client, message)

        asyncio.run(exercise())

        self.assertEqual(client._evaluator_tasks, {})
        self.assertIsNone(pending.evaluation)

    def test_empty_own_message_during_drop_is_refetched(self) -> None:
        """Ghost/placeholder messages get one REST re-fetch before giving up."""
        channel_id = 123
        now = datetime.now(timezone.utc)
        pending = PendingDrop(
            announcement_id=1,
            expires_at=(now + timedelta(seconds=59)).timestamp(),
        )
        client = self._watch_client(channel_id, pending)
        placeholder = self._fake_message(789, 999, channel_id, "", now)
        client.http = SimpleNamespace(
            get_message=AsyncMock(
                return_value={
                    "id": 789,
                    "content": _analysis_content(),
                    "embeds": [],
                }
            )
        )

        async def exercise() -> None:
            with patch("bot.asyncio.sleep", new_callable=AsyncMock):
                await KarutaClient.on_message(client, placeholder)
                task = client._evaluator_tasks[placeholder.id]
                await task

        asyncio.run(exercise())

        client.http.get_message.assert_awaited_once_with(channel_id, 789)
        self.assertIsNotNone(pending.evaluation)
        self.assertEqual(pending.evaluation.cards[0].name, "Queen Mirage")

    def test_stale_expiry_analysis_is_ignored(self) -> None:
        channel_id = 123
        now = datetime.now(timezone.utc)
        pending = PendingDrop(
            announcement_id=1,
            expires_at=(now + timedelta(seconds=59)).timestamp(),
            announced_at=now.timestamp(),
        )
        client = self._watch_client(channel_id, pending)
        # A quoted analysis from an earlier drop: expiry an hour in the past.
        message = self._fake_message(
            789, 999, channel_id, _analysis_content(expires_in=-3600), now
        )

        asyncio.run(
            KarutaClient._handle_evaluator_message(client, message)
        )

        self.assertIsNone(pending.evaluation)

    def test_fresh_expiry_allows_message_that_predates_announcement(self) -> None:
        """Evaluators that reuse one persistent message per channel."""
        channel_id = 123
        now = datetime.now(timezone.utc)
        pending = PendingDrop(
            announcement_id=1,
            expires_at=(now + timedelta(seconds=59)).timestamp(),
            announced_at=now.timestamp(),
        )
        client = self._watch_client(channel_id, pending)
        # Created ten minutes ago, but its expiry is for the current drop.
        message = self._fake_message(
            789,
            999,
            channel_id,
            _analysis_content(),
            now - timedelta(minutes=10),
        )

        asyncio.run(
            KarutaClient._handle_evaluator_message(client, message)
        )

        self.assertIsNotNone(pending.evaluation)
        self.assertEqual(pending.evaluation.cards[2].name, "Gingar")

    def test_raw_edit_from_own_account_is_fetched_and_processed(self) -> None:
        channel_id = 123
        now = datetime.now(timezone.utc)
        pending = PendingDrop(
            announcement_id=1,
            expires_at=(now + timedelta(seconds=59)).timestamp(),
        )
        client = self._watch_client(channel_id, pending)
        client.http = SimpleNamespace(
            get_message=AsyncMock(
                return_value={
                    "id": 789,
                    "content": _analysis_content(),
                    "embeds": [],
                }
            )
        )
        payload = SimpleNamespace(
            message_id=789,
            channel_id=channel_id,
            data={"author": {"id": "999"}},
        )

        async def exercise() -> None:
            with patch("bot.asyncio.sleep", new_callable=AsyncMock):
                await KarutaClient.on_raw_message_edit(client, payload)
                task = client._evaluator_tasks[789]
                await task

        asyncio.run(exercise())

        client.http.get_message.assert_awaited_once_with(channel_id, 789)
        self.assertIsNotNone(pending.evaluation)

    def test_raw_edit_from_other_user_without_content_is_skipped(self) -> None:
        channel_id = 123
        now = datetime.now(timezone.utc)
        pending = PendingDrop(
            announcement_id=1,
            expires_at=(now + timedelta(seconds=59)).timestamp(),
        )
        client = self._watch_client(channel_id, pending)
        fake_channel = SimpleNamespace(
            id=channel_id, fetch_message=AsyncMock()
        )
        client.get_channel = Mock(return_value=fake_channel)
        payload = SimpleNamespace(
            message_id=789,
            channel_id=channel_id,
            data={"author": {"id": "555"}, "content": ""},
        )

        asyncio.run(KarutaClient.on_raw_message_edit(client, payload))

        fake_channel.fetch_message.assert_not_awaited()
        self.assertEqual(client._evaluator_tasks, {})

    def test_text_from_raw_extracts_components_v2(self) -> None:
        raw = self._v2_raw_payload(789)

        text = _text_from_raw(raw)

        self.assertIn("**Queen Mirage**", text)
        self.assertIn("**Shou Daliuwan**", text)
        self.assertIn("Drop expires <t:", text)
        self.assertIn("-# Drop time:", text)
        self.assertTrue(_looks_like_evaluator_output(text))

        # Normal content and embeds still come through.
        self.assertEqual(
            _text_from_raw({"content": "hello", "components": []}),
            "hello",
        )
        self.assertEqual(
            _text_from_raw(
                {
                    "content": "",
                    "embeds": [{"title": "T", "description": "D"}],
                }
            ),
            "T\nD",
        )

    def test_v2_evaluator_message_is_read_via_raw_api(self) -> None:
        """The 01:12 production failure: a Components V2 analysis that the
        library parses as content='' and embeds=[] through every fetch."""
        channel_id = 123
        now = datetime.now(timezone.utc)
        pending = PendingDrop(
            announcement_id=1,
            expires_at=(now + timedelta(seconds=59)).timestamp(),
            announced_at=now.timestamp(),
            card_messages=[
                self._fake_message(2, 646, channel_id, "", now, bot=True)
            ],
        )
        client = self._watch_client(channel_id, pending)
        client.http = SimpleNamespace(
            get_message=AsyncMock(return_value=self._v2_raw_payload(789))
        )
        # What discord.py-self 2.1.0 hands over for a V2 message: the
        # component payload was silently dropped, content is empty.
        message = self._fake_message(
            789, 456, channel_id, "", now, bot=True, flags=IS_COMPONENTS_V2_FLAG
        )

        asyncio.run(KarutaClient._handle_evaluator_message(client, message))

        client.http.get_message.assert_awaited_once_with(channel_id, 789)
        self.assertIsNotNone(pending.evaluation)
        self.assertEqual(
            [card.name for card in pending.evaluation.cards],
            ["Queen Mirage", "Shou Daliuwan", "Gingar"],
        )
        client._process_pending_drop.assert_awaited_once_with(channel_id, pending)

    def test_v2_message_from_foreign_bot_is_scheduled_and_read(self) -> None:
        """V2 analysis from a bot that is NOT the configured evaluator."""
        channel_id = 123
        now = datetime.now(timezone.utc)
        pending = PendingDrop(
            announcement_id=1,
            expires_at=(now + timedelta(seconds=59)).timestamp(),
        )
        client = self._watch_client(channel_id, pending)
        client.http = SimpleNamespace(
            get_message=AsyncMock(return_value=self._v2_raw_payload(789))
        )
        message = self._fake_message(
            789, 777, channel_id, "", now, bot=True, flags=IS_COMPONENTS_V2_FLAG
        )

        async def exercise() -> None:
            with patch("bot.asyncio.sleep", new_callable=AsyncMock):
                await KarutaClient.on_message(client, message)
                task = client._evaluator_tasks[789]
                await task

        asyncio.run(exercise())

        self.assertIsNotNone(pending.evaluation)
        self.assertEqual(pending.evaluation.cards[1].name, "Shou Daliuwan")

    def test_raw_edit_with_v2_payload_is_processed(self) -> None:
        channel_id = 123
        now = datetime.now(timezone.utc)
        pending = PendingDrop(
            announcement_id=1,
            expires_at=(now + timedelta(seconds=59)).timestamp(),
        )
        client = self._watch_client(channel_id, pending)
        payload = SimpleNamespace(
            message_id=789,
            channel_id=channel_id,
            data={
                "author": {"id": "777"},
                "flags": IS_COMPONENTS_V2_FLAG,
                "components": self._v2_raw_payload(789)["components"],
            },
        )

        async def exercise() -> None:
            with patch("bot.asyncio.sleep", new_callable=AsyncMock):
                await KarutaClient.on_raw_message_edit(client, payload)
                task = client._evaluator_tasks[789]
                await task

        asyncio.run(exercise())

        self.assertIsNotNone(pending.evaluation)

    def test_raw_edit_from_v2_payload_needs_no_fetch(self) -> None:
        """A V2 edit settles straight from the gateway payload.

        The edit payload carries the Components V2 text itself, so no HTTP
        fetch (and no cached channel object) may be needed at all.
        """
        channel_id = 123
        message_id = (int(time.time() * 1000) - 1420070400000) << 22 | 4242
        now = datetime.now(timezone.utc)
        pending = PendingDrop(
            announcement_id=1,
            expires_at=(now + timedelta(seconds=59)).timestamp(),
            announced_at=now.timestamp(),
        )
        client = self._watch_client(channel_id, pending)
        # Deliberately no http attribute: nothing may be fetched.
        payload = SimpleNamespace(
            message_id=message_id,
            channel_id=channel_id,
            data={
                "author": {"id": "777", "bot": True},
                "flags": IS_COMPONENTS_V2_FLAG,
                "components": self._v2_raw_payload(message_id)["components"],
            },
        )

        async def exercise() -> None:
            with patch("bot.asyncio.sleep", new_callable=AsyncMock):
                await KarutaClient.on_raw_message_edit(client, payload)
                task = client._evaluator_tasks[message_id]
                await task

        asyncio.run(exercise())

        self.assertIsNotNone(pending.evaluation)
        self.assertEqual(pending.evaluation.cards[0].name, "Queen Mirage")

    def test_raw_fetch_falls_back_to_history_window(self) -> None:
        """http.get_message ("?limit=1&around=") can miss the newest message.

        When it raises, the deterministic ?before= history window must still
        return the payload so the V2 analysis is not lost.
        """
        channel_id = 123
        now = datetime.now(timezone.utc)
        pending = PendingDrop(
            announcement_id=1,
            expires_at=(now + timedelta(seconds=59)).timestamp(),
        )
        client = self._watch_client(channel_id, pending)
        target = self._v2_raw_payload(789)
        client.http = SimpleNamespace(
            get_message=AsyncMock(
                side_effect=discord.NotFound(
                    SimpleNamespace(status=404, reason="Not Found"),
                    "Unknown Message",
                )
            ),
            logs_from=AsyncMock(
                return_value=[{"id": 788, "content": "chat"}, target]
            ),
        )
        message = self._fake_message(
            789, 777, channel_id, "", now, bot=True, flags=IS_COMPONENTS_V2_FLAG
        )

        asyncio.run(KarutaClient._handle_evaluator_message(client, message))

        client.http.logs_from.assert_awaited_once_with(channel_id, 10, before=790)
        client.http.get_message.assert_awaited_once_with(channel_id, 789)
        self.assertIsNotNone(pending.evaluation)
        self.assertEqual(pending.evaluation.cards[1].name, "Shou Daliuwan")

    def test_image_message_accepts_embed_images(self) -> None:
        """Karuta layouts that render cards as embed images still count."""
        embed = SimpleNamespace(
            image=SimpleNamespace(url="https://cdn.example/card.png"),
            thumbnail=SimpleNamespace(url=None),
        )
        message = SimpleNamespace(
            attachments=[], embeds=[embed]
        )
        self.assertTrue(_is_image_message(message))

        empty_embed = SimpleNamespace(
            image=SimpleNamespace(url=None),
            thumbnail=SimpleNamespace(url=None),
        )
        self.assertFalse(
            _is_image_message(SimpleNamespace(attachments=[], embeds=[empty_embed]))
        )
        self.assertFalse(
            _is_image_message(SimpleNamespace(attachments=[], embeds=[]))
        )


class GrabEmojiTests(unittest.TestCase):
    """The grab is an emoji reaction on the card message, not a button."""

    def _client(self, grab_emoji: str = "") -> object:
        client = object.__new__(KarutaClient)
        client.config = SimpleNamespace(grab_emoji=grab_emoji)
        return client

    @staticmethod
    def _reaction(emoji: str) -> SimpleNamespace:
        return SimpleNamespace(emoji=emoji)

    def test_first_non_number_emoji_skips_keycaps(self) -> None:
        reactions = [self._reaction("1\ufe0f\u20e3"), self._reaction("<:kx:123>")]
        self.assertEqual(_first_non_number_emoji(reactions), "<:kx:123>")
        self.assertIsNone(_first_non_number_emoji([self._reaction("2\ufe0f\u20e3")]))
        self.assertIsNone(_first_non_number_emoji(None))
        self.assertIsNone(_first_non_number_emoji([]))

    def test_first_non_number_emoji_raw_builds_custom_emoji(self) -> None:
        raw = [
            {"emoji": {"name": "2\ufe0f\u20e3", "id": None}},
            {"emoji": {"name": "kx", "id": 123}},
        ]
        self.assertEqual(_first_non_number_emoji_raw(raw), "<:kx:123>")
        animated = [{"emoji": {"name": "kx", "id": 123, "animated": True}}]
        self.assertEqual(_first_non_number_emoji_raw(animated), "<a:kx:123>")
        self.assertEqual(
            _first_non_number_emoji_raw([{"emoji": {"name": "\u2764", "id": None}}]),
            "\u2764",
        )
        self.assertEqual(
            _first_non_number_emoji_raw([{"emoji": {"name": "5", "id": None}}]),
            None,
        )

    def test_env_override_wins(self) -> None:
        client = self._client(grab_emoji="\u2764")
        message = SimpleNamespace(reactions=[self._reaction("<:kx:123>")])
        emoji, source = asyncio.run(
            KarutaClient._resolve_grab_emoji(client, message, 1, time.time() + 30)
        )
        self.assertEqual(emoji, "\u2764")
        self.assertIn("KARUTA_GRAB_EMOJI", source)

    def test_emoji_read_from_card_message_reactions(self) -> None:
        client = self._client()
        message = SimpleNamespace(
            id=5,
            channel=SimpleNamespace(id=123),
            reactions=[self._reaction("1\ufe0f\u20e3"), self._reaction("<:kx:123>")],
        )
        emoji, source = asyncio.run(
            KarutaClient._resolve_grab_emoji(client, message, 1, time.time() + 30)
        )
        self.assertEqual(emoji, "<:kx:123>")
        self.assertIn("card message", source)

    def test_emoji_read_from_rest_copy_when_reactions_empty(self) -> None:
        client = self._client()
        client._fetch_raw_message = AsyncMock(
            return_value={"reactions": [{"emoji": {"name": "kx", "id": 123}}]}
        )
        message = SimpleNamespace(id=5, channel=SimpleNamespace(id=123), reactions=[])
        emoji, source = asyncio.run(
            KarutaClient._resolve_grab_emoji(client, message, 2, time.time() + 30)
        )
        self.assertEqual(emoji, "<:kx:123>")
        self.assertIn("REST", source)
        client._fetch_raw_message.assert_awaited_once_with(123, 5)

    def test_number_keycap_fallback_after_nothing_appears(self) -> None:
        """No grab emoji anywhere: fall back to the slot keycap instead of
        waiting for another player to react first (the old silent failure)."""
        client = self._client()
        client._fetch_raw_message = AsyncMock(return_value={"reactions": []})
        client.wait_for = AsyncMock(side_effect=asyncio.TimeoutError())
        message = SimpleNamespace(id=5, channel=SimpleNamespace(id=123), reactions=[])
        emoji, source = asyncio.run(
            KarutaClient._resolve_grab_emoji(client, message, 2, time.time() + 30)
        )
        self.assertEqual(emoji, "2\ufe0f\u20e3")
        self.assertIn("fallback", source)
        client.wait_for.assert_awaited_once()

    def test_live_reaction_is_used_when_karuta_adds_it_late(self) -> None:
        client = self._client()
        client._fetch_raw_message = AsyncMock(return_value=None)
        client.wait_for = AsyncMock(
            return_value=(
                SimpleNamespace(message=SimpleNamespace(id=5), emoji="<:kx:123>"),
                SimpleNamespace(id=42),
            )
        )
        message = SimpleNamespace(id=5, channel=SimpleNamespace(id=123), reactions=[])
        emoji, source = asyncio.run(
            KarutaClient._resolve_grab_emoji(client, message, 3, time.time() + 30)
        )
        self.assertEqual(emoji, "<:kx:123>")
        self.assertIn("live reaction", source)

    def test_combined_drop_uses_slot_keycap_immediately(self) -> None:
        """One message carrying the keycap row 1-3 (Karuta's combined drop):
        the slot's keycap IS the grab button, used at once - no custom-emoji
        wait.  Production regression: that wait pushed the reaction to T+21s
        of a 60s claim window."""
        client = self._client()
        client._fetch_raw_message = AsyncMock()
        message = SimpleNamespace(
            id=5,
            channel=SimpleNamespace(id=123),
            reactions=[
                self._reaction("1\ufe0f\u20e3"),
                self._reaction("2\ufe0f\u20e3"),
                self._reaction("3\ufe0f\u20e3"),
            ],
        )
        emoji, source = asyncio.run(
            KarutaClient._resolve_grab_emoji(client, message, 2, time.time() + 30)
        )
        self.assertEqual(emoji, "2\ufe0f\u20e3")
        self.assertIn("slot keycap", source)
        client._fetch_raw_message.assert_not_awaited()

    def test_combined_drop_keycap_beats_player_emoji(self) -> None:
        """A custom emoji on a combined drop is a player's reaction, not the
        grab button: the keycap row wins."""
        client = self._client()
        client._fetch_raw_message = AsyncMock()
        message = SimpleNamespace(
            id=5,
            channel=SimpleNamespace(id=123),
            reactions=[
                self._reaction("1\ufe0f\u20e3"),
                self._reaction("2\ufe0f\u20e3"),
                self._reaction("<:kx:123>"),
            ],
        )
        emoji, source = asyncio.run(
            KarutaClient._resolve_grab_emoji(client, message, 2, time.time() + 30)
        )
        self.assertEqual(emoji, "2\ufe0f\u20e3")
        client._fetch_raw_message.assert_not_awaited()

    def test_keycap_row_read_from_rest_copy(self) -> None:
        """Keycaps without the variation selector must still match."""
        client = self._client()
        client._fetch_raw_message = AsyncMock(
            return_value={
                "reactions": [
                    {"emoji": {"name": "1\u20e3", "id": None}},
                    {"emoji": {"name": "2\u20e3", "id": None}},
                    {"emoji": {"name": "3\u20e3", "id": None}},
                ]
            }
        )
        message = SimpleNamespace(id=5, channel=SimpleNamespace(id=123), reactions=[])
        emoji, source = asyncio.run(
            KarutaClient._resolve_grab_emoji(client, message, 3, time.time() + 30)
        )
        self.assertEqual(emoji, "3\ufe0f\u20e3")
        self.assertIn("REST", source)

    def test_single_keycap_is_not_a_combined_drop(self) -> None:
        """A lone keycap could be a player's pick on a per-card message: the
        pre-added custom emoji still wins there."""
        client = self._client()
        message = SimpleNamespace(
            id=5,
            channel=SimpleNamespace(id=123),
            reactions=[self._reaction("2\ufe0f\u20e3"), self._reaction("<:kx:123>")],
        )
        emoji, source = asyncio.run(
            KarutaClient._resolve_grab_emoji(client, message, 2, time.time() + 30)
        )
        self.assertEqual(emoji, "<:kx:123>")
        self.assertIn("card message", source)

    def test_claim_card_reacts_with_resolved_emoji(self) -> None:
        client = self._client()
        client._claim_lock = asyncio.Lock()
        client.pending_grab = None
        client.grab_cooldown_until = 0.0
        client._expire_pending_grab = AsyncMock()
        message = SimpleNamespace(
            id=5,
            channel=SimpleNamespace(id=123),
            reactions=[self._reaction("<:kx:123>")],
            add_reaction=AsyncMock(),
        )

        async def exercise() -> None:
            with patch("bot.asyncio.sleep", new_callable=AsyncMock):
                await KarutaClient._claim_card(client, message, 2, time.time() + 30)

        asyncio.run(exercise())

        message.add_reaction.assert_awaited_once_with("<:kx:123>")
        self.assertIsNotNone(client.pending_grab)

    @staticmethod
    def _image_message(message_id: int, channel_id: int, created_at) -> SimpleNamespace:
        return SimpleNamespace(
            id=message_id,
            channel=SimpleNamespace(id=channel_id),
            created_at=created_at,
            attachments=[
                SimpleNamespace(content_type="image/png", filename="card.png")
            ],
        )

    def test_slot_two_targets_second_card_message(self) -> None:
        """Karuta posts one message per card: slot N grabs the Nth message."""
        channel_id = 123
        now = datetime.now(timezone.utc)
        pending = PendingDrop(
            announcement_id=1,
            expires_at=(now + timedelta(seconds=59)).timestamp(),
        )
        first = self._image_message(10, channel_id, now)
        second = self._image_message(11, channel_id, now + timedelta(seconds=1))
        client = object.__new__(KarutaClient)
        client.config = SimpleNamespace(target_channels=(channel_id,))
        client.pending_drops = {channel_id: pending}
        claim = AsyncMock()
        client._claim_card = claim
        evaluator_message = SimpleNamespace(
            channel=SimpleNamespace(id=channel_id),
            created_at=now + timedelta(seconds=2),
            content=(
                "<:no_1:1> `♡` `0` · **A** · Series\n"
                "<:no_2:2> `♡` `5` · **B** · Series"
            ),
            embeds=[],
        )

        async def exercise() -> None:
            await KarutaClient._attach_drop_image(client, first)
            await KarutaClient._attach_drop_image(client, second)
            await KarutaClient._handle_evaluator_message(client, evaluator_message)

        asyncio.run(exercise())

        self.assertEqual(len(pending.card_messages), 2)
        self.assertTrue(pending.evaluation_processed)
        # Slot 2 (♡5) wins over slot 1 (♡0): the second message is grabbed.
        claim.assert_awaited_once_with(second, 2, pending.expires_at)

    def test_missing_card_messages_get_a_short_grace(self) -> None:
        """Evaluation ready before every card message: wait briefly instead
        of grabbing the wrong (only) tracked message at once."""
        channel_id = 123
        now = datetime.now(timezone.utc)
        pending = PendingDrop(
            announcement_id=1,
            expires_at=(now + timedelta(seconds=59)).timestamp(),
        )
        first = self._image_message(10, channel_id, now)
        second = self._image_message(11, channel_id, now + timedelta(seconds=1))
        client = object.__new__(KarutaClient)
        client.config = SimpleNamespace(target_channels=(channel_id,))
        client.pending_drops = {channel_id: pending}
        claim = AsyncMock()
        client._claim_card = claim
        evaluator_message = SimpleNamespace(
            channel=SimpleNamespace(id=channel_id),
            created_at=now,
            content=(
                "<:no_1:1> `♡` `0` · **A** · Series\n"
                "<:no_2:2> `♡` `5` · **B** · Series"
            ),
            embeds=[],
        )

        async def exercise() -> None:
            await KarutaClient._handle_evaluator_message(client, evaluator_message)
            await KarutaClient._attach_drop_image(client, first)
            self.assertFalse(pending.evaluation_processed)
            self.assertTrue(pending.card_grace_scheduled)
            claim.assert_not_awaited()
            await KarutaClient._attach_drop_image(client, second)

        asyncio.run(exercise())

        self.assertTrue(pending.evaluation_processed)
        claim.assert_awaited_once_with(second, 2, pending.expires_at)

    def test_combined_drop_skips_grace_and_grabs_qualifying_card(self) -> None:
        """Production layout (2026-11-06): Karuta's drop is ONE message with
        the keycap row 1-3.  The bot must (a) not wait 2s for per-card
        messages that will never come and (b) grab the card that meets the
        tiered rules - here Yuki Mitsuya (print #44,190, ♡10 qualifies under
        the 7+ high-print rule) - with its keycap right away."""
        channel_id = 123
        now = datetime.now(timezone.utc)
        pending = PendingDrop(
            announcement_id=1,
            expires_at=(now + timedelta(seconds=59)).timestamp(),
        )
        only = self._image_message(10, channel_id, now)
        only.reactions = [
            self._reaction("1\ufe0f\u20e3"),
            self._reaction("2\ufe0f\u20e3"),
            self._reaction("3\ufe0f\u20e3"),
        ]
        client = object.__new__(KarutaClient)
        client.config = SimpleNamespace(target_channels=(channel_id,))
        client.pending_drops = {channel_id: pending}
        claim = AsyncMock()
        client._claim_card = claim
        evaluator_message = SimpleNamespace(
            channel=SimpleNamespace(id=channel_id),
            created_at=now,
            content=(
                "<:no_1:1> `♡` `1` · `#85420` · **Rick** · Final Fantasy VII: On the Way to a Smile, Episode: Denzel\n"
                "<:no_2:2> `♡` `10` · `#44190` · **Yuki Mitsuya** · ODDTAXI\n"
                "<:no_3:3> `♡` `1` · `#85487` · **Moeka China** · High School Fleet"
            ),
            embeds=[],
        )

        async def exercise() -> None:
            await KarutaClient._attach_drop_image(client, only)
            await KarutaClient._handle_evaluator_message(client, evaluator_message)

        asyncio.run(exercise())

        self.assertTrue(pending.evaluation_processed)
        self.assertFalse(pending.card_grace_scheduled)
        self.assertTrue(pending.combined_confirmed)
        # The only qualifying card (high print, 7+ wishes), grabbed on the
        # single message with its keycap.
        claim.assert_awaited_once_with(only, 2, pending.expires_at)

    def test_combined_drop_all_low_wishlist_high_prints_is_skipped(self) -> None:
        """The exact 2026-11-06 05:17 drop: three high prints with ♡1 each.
        Rule 4 (high prints need 7+ wishes) means NO grab at all - the drop
        is skipped by design, and no reaction is ever sent."""
        channel_id = 123
        now = datetime.now(timezone.utc)
        pending = PendingDrop(
            announcement_id=1,
            expires_at=(now + timedelta(seconds=59)).timestamp(),
        )
        only = self._image_message(10, channel_id, now)
        only.reactions = [
            self._reaction("1\ufe0f\u20e3"),
            self._reaction("2\ufe0f\u20e3"),
            self._reaction("3\ufe0f\u20e3"),
        ]
        client = object.__new__(KarutaClient)
        client.config = SimpleNamespace(target_channels=(channel_id,))
        client.pending_drops = {channel_id: pending}
        claim = AsyncMock()
        client._claim_card = claim
        evaluator_message = SimpleNamespace(
            channel=SimpleNamespace(id=channel_id),
            created_at=now,
            content=(
                "<:no_1:1> `♡` `1` · `#85420` · **Rick** · Final Fantasy VII: On the Way to a Smile, Episode: Denzel\n"
                "<:no_2:2> `♡` `1` · `#44190` · **Yuki Mitsuya** · ODDTAXI\n"
                "<:no_3:3> `♡` `1` · `#85487` · **Moeka China** · High School Fleet"
            ),
            embeds=[],
        )

        async def exercise() -> None:
            await KarutaClient._attach_drop_image(client, only)
            await KarutaClient._handle_evaluator_message(client, evaluator_message)

        asyncio.run(exercise())

        self.assertTrue(pending.evaluation_processed)
        self.assertTrue(pending.combined_confirmed)
        claim.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
