from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import signal
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import discord
from dotenv import dotenv_values

from evaluator import Evaluation, choose_card, parse_evaluator_message
from stealth import (
    Backoff,
    CaptchaRequired,
    JsonStore,
    PresenceRotator,
    SingleInstance,
    StealthProfile,
    console_captcha_handler,
    jitter,
    skew,
    stable_installation_id,
)

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "kd-state.json"
LOCK_FILE = ROOT / ".kd.pid"
REACTION_EMOJIS = ("1\ufe0f\u20e3", "2\ufe0f\u20e3", "3\ufe0f\u20e3", "4\ufe0f\u20e3")
DROP_ANNOUNCEMENT = re.compile(r"\b(?:i'm|i am)\s+dropping\s+\d+\s+cards?\b", re.I)
MENTIONED_DROP = re.compile(r"<@!?(\d+)>\s+is\s+dropping\s+\d+\s+cards?\b", re.I)
GRAB_SUCCESS = re.compile(r"\b(?:grabbed|got|took)\b.*\bcard\b", re.I)
GRAB_FAILURE = re.compile(
    r"\b(?:lost|missed|failed to claim|couldn't claim|could not claim)\b.*\bcard\b",
    re.I,
)

# Timing is always a range, never a constant.
COMMAND_INTERVAL_SECONDS = 30 * 60
COMMAND_SPREAD_SECONDS = 150
GRAB_COOLDOWN_SECONDS = 10 * 60
FAILED_GRAB_COOLDOWN_SECONDS = 75
CLAIM_WINDOW_SECONDS = 59
REACTION_READY_TIMEOUT_SECONDS = 15
ANNOUNCEMENT_TIMEOUT_SECONDS = 30
TYPING_SECONDS = (1.1, 2.9)
REACTION_DELAY_SECONDS = (0.07, 0.22)
PENDING_DROP_TTL_SECONDS = 180
SKIP_CHANCE = 0.02
MAX_MESSAGE_ATTEMPTS = 3
LOCK_RETRY_ATTEMPTS = 5
LOCK_RETRY_SECONDS = 30

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("karuta")
logging.getLogger("discord.http").setLevel(logging.WARNING)
logging.getLogger("discord.gateway").setLevel(logging.WARNING)


@dataclass(frozen=True)
class Config:
    token: str
    target_channels: tuple[int, ...]
    drop_bot_id: int
    evaluator_bot_id: int
    data_dir: Path
    profile: StealthProfile
    proxy: str | None
    timezone: str | None
    cache_all_members: bool
    chunk_guilds: bool
    max_messages: int
    skip_chance: float


@dataclass
class PendingDrop:
    announcement_id: int
    expires_at: float
    card_message: discord.Message | None = None
    evaluation: Evaluation | None = None
    evaluation_processed: bool = False


@dataclass
class PendingGrab:
    message_id: int
    expires_at: float


def _required_id(name: str) -> int:
    value = os.getenv(name, "").strip()
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a Discord ID.") from exc


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip())
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "").strip())
    except ValueError:
        return default


def _detect_tz() -> str | None:
    """Container clocks report UTC; prefer a real zone if one exists."""
    try:
        from tzlocal import get_localzone_name
    except Exception:
        return None
    name = get_localzone_name()
    return None if name in (None, "UTC", "Etc/UTC") else name


def _load_config_file() -> None:
    for key, value in dotenv_values(ROOT / ".env").items():
        if value is not None and not os.getenv(key, "").strip():
            os.environ[key] = value


def load_config() -> Config:
    _load_config_file()
    token = os.getenv("DISCORD_USER_TOKEN", "").strip()
    channel_values = [
        value.strip()
        for value in os.getenv("TARGET_CHANNELS", "").split(",")
        if value.strip()
    ]
    channel_ids: list[int] = []
    for value in channel_values:
        try:
            channel_ids.append(int(value))
        except ValueError as error:
            raise ValueError(
                f"TARGET_CHANNELS contains an invalid channel ID: {value!r}."
            ) from error
    channels = tuple(channel_ids)
    if not token:
        raise ValueError("DISCORD_USER_TOKEN is required.")
    if not channels:
        raise ValueError("TARGET_CHANNELS must contain at least one channel ID.")

    data_dir = Path(os.getenv("DATA_DIR", str(ROOT)).strip() or str(ROOT)).expanduser()
    if not data_dir.is_absolute():
        data_dir = ROOT / data_dir

    timezone = os.getenv("DISCORD_TIMEZONE", "").strip() or _detect_tz()
    if not timezone:
        log.warning("No usable timezone; set DISCORD_TIMEZONE to your real zone.")

    activities = tuple(
        item.strip() for item in os.getenv("FAKE_ACTIVITIES", "").split("|") if item.strip()
    )
    return Config(
        token=token,
        target_channels=channels,
        drop_bot_id=_required_id("DROP_BOT_ID"),
        evaluator_bot_id=_env_int("WISHLIST_EVALUATOR_ID", 1380936713639166082),
        data_dir=data_dir.resolve(),
        profile=StealthProfile(
            locale=os.getenv("DISCORD_LOCALE", "en-US").strip() or "en-US",
            platform=os.getenv("CLIENT_PLATFORM", "web").strip().lower() or "web",
            activity_pool=activities,
        ),
        proxy=os.getenv("DISCORD_PROXY", "").strip() or None,
        timezone=timezone,
        cache_all_members=_env_flag("CACHE_ALL_MEMBERS"),
        chunk_guilds=_env_flag("CHUNK_GUILDS", False),
        max_messages=max(50, _env_int("MAX_MESSAGES", 200)),
        skip_chance=max(0.0, min(0.25, _env_float("SKIP_CHANCE", SKIP_CHANCE))),
    )


def _message_text(message: discord.Message) -> str:
    parts = [message.content or ""]
    for embed in message.embeds:
        parts.extend((embed.title or "", embed.description or ""))
        parts.extend(f"{field.name}\n{field.value}" for field in embed.fields)
        if embed.footer and embed.footer.text:
            parts.append(embed.footer.text)
    return "\n".join(part for part in parts if part)


def _is_image_message(message: discord.Message) -> bool:
    return any(
        (attachment.content_type or "").startswith("image/")
        or Path(attachment.filename).suffix.lower()
        in {".png", ".jpg", ".jpeg", ".gif", ".webp"}
        for attachment in message.attachments
    )


def _kd_payload() -> str:
    roll = random.random()
    if roll < 0.07:
        return "Kd"
    if roll < 0.13:
        return "kd "
    return "kd"


class KarutaClient(discord.Client):
    def __init__(
        self, config: Config, store: JsonStore, installation_id: str, backoff: Backoff
    ) -> None:
        options: dict[str, object] = {
            "status": discord.Status.online,
            "assume_unsync_clock": False,      # mirror server clock, like the real client
            "default_ratelimit_limit": 1,      # never burst a route
            "max_ratelimit_timeout": 60.0,
            "max_messages": config.max_messages,
            "chunk_guilds_at_startup": config.chunk_guilds,
            "guild_subscriptions": False,
            "installation_id": installation_id,  # stable X-Installation-ID
            "captcha_handler": console_captcha_handler,
            "debug_options": ["bugReporterEnabled"],
        }
        if config.proxy:
            options["proxy"] = config.proxy
        if config.timezone:
            options["timezone"] = config.timezone
        if config.cache_all_members:
            options["member_cache_flags"] = discord.MemberCacheFlags(all=True)

        super().__init__(**options)

        self.config = config
        self.store = store
        self.backoff = backoff
        self.pending_drops: dict[int, PendingDrop] = {}
        self.pending_grab: PendingGrab | None = None
        self.grab_cooldown_until = 0.0
        self.last_command_at = self._load_state()
        self.scheduler: asyncio.Task[None] | None = None
        self._claim_lock = asyncio.Lock()
        self._seen_announcements: deque[int] = deque(maxlen=256)
        self._ready_count = 0
        self._presence = PresenceRotator(self, config.profile.activity_pool)
        self._close_requested = False
        try:
            self._connection.locale = config.profile.locale
        except Exception:
            pass

    # ---------------- fingerprint ----------------

    async def headers_context(self) -> discord.HeadersContext:
        context = await self.config.profile.build(self)
        log.info(
            "Fingerprint: ua=%s build=%s platform=%s tz=%s",
            context.user_agent,
            context.super_properties.get("client_build_number"),
            context.platform,
            getattr(self.http, "timezone", None) or "auto",
        )
        return context

    async def before_identify_hook(self, *, initial: bool = False) -> None:
        # Randomised think-time before every IDENTIFY keeps reconnects from
        # landing on a metronomic schedule.
        await asyncio.sleep(jitter(0.4, 2.1))

    async def setup_hook(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(
                    sig, lambda: asyncio.ensure_future(self.close())
                )
            except (NotImplementedError, RuntimeError):
                pass

    # ---------------- state ----------------

    def _load_state(self) -> float:
        try:
            value = float(self.store.load().get("last_command_at", 0))
        except (TypeError, ValueError):
            return 0.0
        return value if value > 0 else 0.0

    def _save_state(self) -> None:
        try:
            data = self.store.load()
            data["last_command_at"] = self.last_command_at
            self.store.save(data)
        except OSError as error:
            log.error("State write failed: %s", error)

    # ---------------- lifecycle ----------------

    def _spawn_scheduler(self) -> None:
        if self.scheduler is not None and not self.scheduler.done():
            return
        self.scheduler = asyncio.create_task(self._command_scheduler())
        self.scheduler.add_done_callback(self._log_scheduler_result)

    @staticmethod
    def _log_scheduler_result(task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            log.error("Scheduler died: %r", error)

    async def on_ready(self) -> None:
        if self.user is None:
            log.error("Connected without a user identity.")
            return
        self._ready_count += 1
        log.info("Connected as %s (session %s).", self.user, self._ready_count)
        self.backoff.reset()

        # Any drop in flight belongs to the previous socket.
        self.pending_drops.clear()
        self._seen_announcements.clear()

        self._spawn_scheduler()
        self._presence.start()

    async def on_resumed(self) -> None:
        log.info("Session resumed.")
        self._spawn_scheduler()
        self._presence.start()

    async def on_disconnect(self) -> None:
        log.warning("Gateway disconnected.")

    async def close(self) -> None:
        self._close_requested = True
        self._presence.stop()
        if self.scheduler is not None and not self.scheduler.done():
            self.scheduler.cancel()
        await super().close()

    # ---------------- scheduling ----------------

    async def _sleep_until(self, deadline: float) -> None:
        while not self.is_closed():
            remaining = deadline - time.time()
            if remaining <= 0:
                return
            await asyncio.sleep(min(remaining, 5.0))

    async def _command_scheduler(self) -> None:
        if self.last_command_at <= 0:
            await asyncio.sleep(jitter(18.0, 85.0))

        while not self.is_closed():
            if self.last_command_at > 0:
                base = self.last_command_at + COMMAND_INTERVAL_SECONDS
                target = max(base, self.grab_cooldown_until)
                target += jitter(0.0, COMMAND_SPREAD_SECONDS)
                await self._sleep_until(target)
            if self.is_closed():
                return

            # People miss cycles now and then.
            if random.random() < self.config.skip_chance:
                log.info("Skipping this cycle to break the pattern.")
                self.last_command_at = time.time()
                self._save_state()
                continue

            if not await self._send_kd():
                await asyncio.sleep(skew(75.0, 0.3))

    async def _resolve_channel(self, channel_id: int) -> discord.abc.Messageable | None:
        channel = self.get_channel(channel_id)
        if channel is not None:
            return channel
        try:
            return await self.fetch_channel(channel_id)
        except discord.HTTPException as error:
            log.error("Channel %s unavailable: %s", channel_id, error)
            return None

    async def _send_text(self, channel: discord.abc.Messageable, text: str) -> bool:
        for attempt in range(1, MAX_MESSAGE_ATTEMPTS + 1):
            try:
                await channel.send(text)
                return True
            except discord.HTTPException as error:
                if error.status in (403, 404):
                    log.error("Send rejected (%s) in %s.", error.status, channel.id)
                    return False
                if attempt == MAX_MESSAGE_ATTEMPTS:
                    log.error("Send failed in %s: %s", channel.id, error)
                    return False
                wait = getattr(error, "retry_after", None) or skew(2.0 ** attempt, 0.4)
                log.warning(
                    "Retrying send in %s after %.1fs (%s).", channel.id, wait, error.status
                )
                await asyncio.sleep(wait)
            except Exception as error:  # transport level
                if attempt == MAX_MESSAGE_ATTEMPTS:
                    log.error("Send errored in %s: %s", channel.id, error)
                    return False
                await asyncio.sleep(skew(3.0 * attempt, 0.4))
        return False

    async def _send_kd(self) -> bool:
        channels = list(self.config.target_channels)
        random.shuffle(channels)  # humans do not always start with the same server

        sent = False
        for index, channel_id in enumerate(channels):
            if self.is_closed():
                break
            channel = await self._resolve_channel(channel_id)
            if channel is None or not hasattr(channel, "send"):
                continue

            try:
                async with channel.typing():
                    await asyncio.sleep(jitter(*TYPING_SECONDS))
                    if await self._send_text(channel, _kd_payload()):
                        sent = True
                        log.info("Sent kd in %s.", channel_id)
            except Exception as error:
                log.error("Typing/send failed in %s: %s", channel_id, error)

            if index + 1 < len(channels):
                await asyncio.sleep(jitter(1.2, 4.5))

        if sent:
            self.last_command_at = time.time()
            self._save_state()
        return sent

    # ---------------- drop tracking ----------------

    def _is_drop_announcement(self, message: discord.Message) -> bool:
        content = message.content or ""
        if DROP_ANNOUNCEMENT.search(content):
            return True
        if self.user is None:
            return False
        return any(
            int(match.group(1)) == self.user.id
            for match in MENTIONED_DROP.finditer(content)
        )

    def _record_drop_announcement(self, message: discord.Message) -> None:
        if message.id in self._seen_announcements:
            return
        self._seen_announcements.append(message.id)
        self._prune_drops()
        self.pending_drops[message.channel.id] = PendingDrop(
            announcement_id=message.id,
            expires_at=message.created_at.timestamp() + CLAIM_WINDOW_SECONDS,
            card_message=message if _is_image_message(message) else None,
        )
        log.info("Drop announced in %s.", message.channel.id)

    def _prune_drops(self) -> None:
        now = time.time()
        for channel_id, pending in list(self.pending_drops.items()):
            if pending.expires_at + PENDING_DROP_TTL_SECONDS < now:
                self.pending_drops.pop(channel_id, None)

    async def _attach_drop_image(self, message: discord.Message) -> PendingDrop | None:
        pending = self.pending_drops.get(message.channel.id)
        if (
            pending is None
            or pending.card_message is not None
            or message.id == pending.announcement_id
            or message.created_at.timestamp() > pending.expires_at
            or not _is_image_message(message)
        ):
            return pending
        pending.card_message = message
        log.info("Drop image received in %s.", message.channel.id)
        await self._process_pending_drop(message.channel.id, pending)
        return pending

    # ---------------- evaluator / claim ----------------

    async def _handle_evaluator_message(self, message: discord.Message) -> None:
        if message.channel.id not in self.config.target_channels:
            return
        pending = self.pending_drops.get(message.channel.id)
        if pending is None or pending.evaluation_processed:
            return
        if message.created_at.timestamp() > pending.expires_at:
            self.pending_drops.pop(message.channel.id, None)
            return

        message_text = _message_text(message)
        evaluation = parse_evaluator_message(message_text)
        if evaluation is None:
            log.warning(
                "Evaluator output had no parseable entries "
                "(author_id=%s message_id=%s embeds=%d): %r",
                message.author.id,
                message.id,
                len(message.embeds),
                message_text,
            )
            return

        pending.evaluation = evaluation
        if evaluation.expires_at is not None:
            pending.expires_at = min(pending.expires_at, float(evaluation.expires_at))

        await self._process_pending_drop(message.channel.id, pending)

    async def _process_pending_drop(self, channel_id: int, pending: PendingDrop) -> None:
        evaluation = pending.evaluation
        if self.pending_drops.get(channel_id) is not pending:
            return
        if pending.evaluation_processed or pending.card_message is None or evaluation is None:
            return

        pending.evaluation_processed = True
        chosen = choose_card(evaluation)
        if chosen is None:
            log.info("No eligible card in the evaluator output.")
            return
        if time.time() >= pending.expires_at:
            return

        emoji = REACTION_EMOJIS[chosen.slot - 1]
        log.info(
            "Slot %s -> %s (%s matches).", chosen.slot, chosen.name, chosen.wishlist_count
        )
        await self._claim_card(pending.card_message, emoji, pending.expires_at)

    async def _reaction_present(self, message: discord.Message, emoji: str) -> bool:
        return any(str(reaction.emoji) == emoji for reaction in message.reactions)

    async def _claim_card(
        self, message: discord.Message, emoji: str, expires_at: float
    ) -> None:
        async with self._claim_lock:
            if self.pending_grab is not None:
                log.info("Grab already in flight.")
                return
            if time.time() < self.grab_cooldown_until:
                log.info("Grab cooldown active.")
                return

            if await self._reaction_present(message, emoji):
                log.info("%s already present.", emoji)
            else:
                timeout = min(REACTION_READY_TIMEOUT_SECONDS, expires_at - time.time() - 0.5)
                if timeout <= 0:
                    log.info("No time left to wait for %s.", emoji)
                    return

                def check(reaction: discord.Reaction, _user: discord.User) -> bool:
                    return reaction.message.id == message.id and str(reaction.emoji) == emoji

                try:
                    await self.wait_for("reaction_add", check=check, timeout=timeout)
                except asyncio.TimeoutError:
                    if not await self._reaction_present(message, emoji):
                        log.info("%s never appeared.", emoji)
                        return

            if time.time() >= expires_at:
                return

            # Human click latency.
            await asyncio.sleep(jitter(*REACTION_DELAY_SECONDS))

            try:
                await message.add_reaction(emoji)
            except discord.NotFound:
                log.warning("Drop message vanished before the reaction.")
                return
            except discord.HTTPException as error:
                log.error("Reaction failed: %s", error)
                return

            pending = PendingGrab(
                message_id=message.id,
                expires_at=time.time() + skew(ANNOUNCEMENT_TIMEOUT_SECONDS, 0.15),
            )
            self.pending_grab = pending
            log.info("Reacted %s to %s; awaiting confirmation.", emoji, message.id)
            asyncio.create_task(self._expire_pending_grab(pending))

    async def _expire_pending_grab(self, pending: PendingGrab) -> None:
        await asyncio.sleep(max(0.0, pending.expires_at - time.time()))
        if self.pending_grab == pending:
            self._record_grab_result(success=False)
            log.info("Grab confirmation timed out.")

    def _record_grab_result(self, success: bool) -> None:
        self.pending_grab = None
        base = GRAB_COOLDOWN_SECONDS if success else FAILED_GRAB_COOLDOWN_SECONDS
        self.grab_cooldown_until = time.time() + skew(base, 0.08)
        log.info("Grab %s; cooldown %.0fs.", "ok" if success else "failed", base)

    # ---------------- gateway ----------------

    async def on_message(self, message: discord.Message) -> None:
        if self.user is None or message.author.id == self.user.id:
            return

        if message.author.id == self.config.evaluator_bot_id:
            await self._handle_evaluator_message(message)
            return

        if message.author.id != self.config.drop_bot_id:
            return

        if self.pending_grab is not None:
            content = message.content or ""
            refers = self.user.id in message.raw_mentions or bool(
                re.search(r"\byou\b", content, re.I)
            )
            if refers and GRAB_SUCCESS.search(content):
                self._record_grab_result(success=True)
                return
            if refers and GRAB_FAILURE.search(content):
                self._record_grab_result(success=False)
                return

        if message.channel.id not in self.config.target_channels:
            return
        if self._is_drop_announcement(message):
            self._record_drop_announcement(message)
            return

        await self._attach_drop_image(message)

    async def on_message_edit(
        self, _before: discord.Message, after: discord.Message
    ) -> None:
        if self.user is None or after.author.id == self.user.id:
            return

        if after.author.id == self.config.evaluator_bot_id:
            await self._handle_evaluator_message(after)


def main() -> None:
    try:
        config = load_config()
    except ValueError as error:
        raise SystemExit(str(error)) from error

    try:
        config.data_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise SystemExit(f"Cannot create DATA_DIR {config.data_dir}: {error}") from error

    store = JsonStore(config.data_dir / STATE_FILE.name)
    guard = SingleInstance(config.data_dir / LOCK_FILE.name)

    try:
        try:
            _acquire_guard(guard)
            installation_id = stable_installation_id(store)
        except OSError as error:
            raise SystemExit(f"Cannot initialize bot state: {error}") from error

        backoff = Backoff()
        while True:
            client = KarutaClient(config, store, installation_id, backoff)
            try:
                client.run(config.token)
            except CaptchaRequired as error:
                raise SystemExit(str(error)) from error
            except KeyboardInterrupt:
                break
            except discord.LoginFailure as error:
                raise SystemExit(f"Login rejected: {error}") from error
            except Exception as error:
                delay = backoff.next()
                log.error("Connection lost (%s); reconnecting in %.0fs.", error, delay)
                time.sleep(delay)
                continue

            if client._close_requested:
                break
            delay = backoff.next()
            log.warning("Client stopped unexpectedly; restarting in %.0fs.", delay)
            time.sleep(delay)
    finally:
        guard.release()


def _acquire_guard(guard: SingleInstance) -> None:
    for attempt in range(1, LOCK_RETRY_ATTEMPTS + 1):
        if guard.acquire():
            return
        if attempt == LOCK_RETRY_ATTEMPTS:
            break
        log.warning(
            "Another instance holds this session; retrying in %s seconds (%s/%s).",
            LOCK_RETRY_SECONDS,
            attempt,
            LOCK_RETRY_ATTEMPTS,
        )
        time.sleep(LOCK_RETRY_SECONDS)
    raise SystemExit(
        f"Another instance still holds this session after {LOCK_RETRY_ATTEMPTS} checks."
    )


if __name__ == "__main__":
    main()
