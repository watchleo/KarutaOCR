import asyncio
from datetime import datetime, timedelta, timezone
import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from bot import KarutaClient, PendingDrop, _acquire_guard, load_config
from stealth import CaptchaRequired, console_captcha_handler


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
        placeholder = SimpleNamespace(
            id=789,
            author=SimpleNamespace(id=456),
            channel=SimpleNamespace(id=channel_id),
            created_at=now,
            content="",
            embeds=[],
        )
        filled = SimpleNamespace(
            id=789,
            author=SimpleNamespace(id=456),
            channel=placeholder.channel,
            created_at=now,
            content="<:no_1:1> `♡` `2` · **First** · Series",
            embeds=[],
        )
        placeholder.channel.fetch_message = AsyncMock(return_value=filled)

        async def exercise() -> None:
            with patch("bot.asyncio.sleep", new_callable=AsyncMock):
                await KarutaClient._handle_evaluator_message(client, placeholder)

        asyncio.run(exercise())

        placeholder.channel.fetch_message.assert_awaited_with(789)
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
            target_channels=(channel_id,), evaluator_bot_id=456
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
        claim.assert_awaited_once_with(
            image_message, "1\ufe0f\u20e3", pending.expires_at
        )


if __name__ == "__main__":
    unittest.main()
