from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import signal
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import discord
from dotenv import dotenv_values

from evaluator import (
    Evaluation,
    explain_choice,
    parse_evaluator_message,
)
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
# Number keycaps and bare digits mark slot picks, never the grab itself, so
# they are skipped while looking for the emoji to grab with.
_NUMBER_KEYCAPS = frozenset(
    {str(digit) for digit in range(10)}
    | {f"{digit}\ufe0f" for digit in range(10)}
    | {f"{digit}\u20e3" for digit in range(10)}
    | {f"{digit}\ufe0f\u20e3" for digit in range(10)}
)
DROP_ANNOUNCEMENT = re.compile(r"\b(?:i'm|i am)\s+dropping\s+\d+\s+cards?\b", re.I)
MENTIONED_DROP = re.compile(r"<@!?(\d+)>\s+is\s+dropping\s+\d+\s+cards?\b", re.I)
GRAB_SUCCESS = re.compile(r"\b(?:grabbed|got|took)\b.*\bcard\b", re.I)
GRAB_FAILURE = re.compile(
    r"\b(?:lost|missed|failed to claim|couldn't claim|could not claim)\b.*\bcard\b",
    re.I,
)
HEART_HINT = re.compile(r"[\u2661\u2665\u2764]")
# ◈ (white diamond) - the quantity marker evaluators print after the card code.
GEM_HINT = re.compile("\u25c8")
EVALUATOR_EXPIRY_HINT = re.compile(r"drop\s+expires", re.I)
EVALUATOR_CARD_CODE = re.compile(r"`\s*#\s*\d")

# Timing is always a range, never a constant.
COMMAND_INTERVAL_SECONDS = 30 * 60
COMMAND_SPREAD_SECONDS = 150
GRAB_COOLDOWN_SECONDS = 10 * 60
FAILED_GRAB_COOLDOWN_SECONDS = 75
CLAIM_WINDOW_SECONDS = 59
# Karuta's current layout puts every card of a drop in ONE message (the
# announcement itself) and pre-adds the number keycaps 1..N as grab buttons.
# Older/per-card layouts posted several card messages; if the evaluation is
# ready before all of them arrive, wait this long before falling back to the
# closest tracked message.  A visible keycap row proves the single-message
# layout and skips the wait entirely.
CARD_ARRIVAL_GRACE_SECONDS = 2.0
REACTION_READY_TIMEOUT_SECONDS = 15
ANNOUNCEMENT_TIMEOUT_SECONDS = 30
TYPING_SECONDS = (1.1, 2.9)
REACTION_DELAY_SECONDS = (0.07, 0.22)
PENDING_DROP_TTL_SECONDS = 180
# Defensive cap for the per-card-message layouts; single-message drops track 1.
MAX_CARD_MESSAGES = 8
EVALUATOR_SETTLE_SECONDS = 3.0
# A message that already looks like a finished analysis is acted on quickly;
# only the configured evaluator bot's placeholder pattern needs the slow settle.
EVALUATOR_CONTENT_SETTLE_SECONDS = 0.6
# The gateway can deliver self-bot analysis messages (posted by another client
# running on this very account) with empty content; re-fetch them shortly
# after they arrive so the edited version is still caught.
EVALUATOR_RECHECK_SETTLE_SECONDS = 1.5
# An analysis whose expiry timestamp is this far in the past belongs to an
# older drop (a quote or a re-edited message), never to the current one.
STALE_EXPIRY_GRACE_SECONDS = 30.0
# An empty evaluator message is usually a placeholder that gets edited moments
# later; re-fetch it on this schedule so a missed edit does not lose the drop.
# Every second counts inside the ~60s claim window, so keep the total short.
EVALUATOR_REFETCH_DELAYS = (2.0, 3.0, 5.0)
# Discord "Components V2" flag (message text lives in Text Display components
# instead of the content field). discord.py-self 2.1.0 predates this feature:
# it parses content/embeds and silently drops v2 components, so such messages
# look empty even through REST re-fetches. Their text is only readable from
# the raw message payload.
IS_COMPONENTS_V2_FLAG = 1 << 15
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
    grab_emoji: str
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
    # Single-message drops (the current layout) track one message that holds
    # every card; per-card layouts would list one message per slot.
    card_messages: list[discord.Message] = field(default_factory=list)
    evaluation: Evaluation | None = None
    evaluation_processed: bool = False
    announced_at: float = 0.0
    # True once the number-keycap row proved a single-message (combined) drop.
    combined_confirmed: bool = False
    # True once the short wait for missing card messages has been scheduled.
    card_grace_scheduled: bool = False


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
        grab_emoji=os.getenv("KARUTA_GRAB_EMOJI", "").strip(),
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


def _walk_component_text(components: object, parts: list[str]) -> None:
    """Collect the text of Components V2 objects, recursively.

    V2 layouts nest Text Display components (type 10) inside Sections (9) and
    Containers (17); only they carry analysis text.
    """
    if not isinstance(components, list):
        return
    for component in components:
        if not isinstance(component, dict):
            continue
        if component.get("type") == 10 and component.get("content"):
            parts.append(str(component["content"]))
        _walk_component_text(component.get("components"), parts)


def _text_from_raw(data: dict) -> str:
    """Extract every readable text field from a raw message payload.

    Covers normal content, embeds and Components V2 text displays; used when
    the parsed Message object looks empty (v2 messages read as content="" and
    embeds=[] on discord.py-self versions that predate Components V2).
    """
    parts: list[str] = [data.get("content") or ""]
    for embed in data.get("embeds") or []:
        if not isinstance(embed, dict):
            continue
        parts.append(embed.get("title") or "")
        parts.append(embed.get("description") or "")
        for field in embed.get("fields") or []:
            if isinstance(field, dict):
                parts.append(f"{field.get('name') or ''}\n{field.get('value') or ''}")
        footer = embed.get("footer") or {}
        if isinstance(footer, dict):
            parts.append(footer.get("text") or "")
    _walk_component_text(data.get("components"), parts)
    return "\n".join(part for part in parts if part)


def _flags_value(message: discord.Message) -> int:
    """The raw message flag bits (MessageFlags keeps unknown bits)."""
    flags = getattr(message, "flags", None)
    value = getattr(flags, "value", 0)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _raw_flags_value(data: dict) -> int:
    """The raw flag bits of a gateway/REST message payload."""
    try:
        return int(data.get("flags") or 0)
    except (TypeError, ValueError):
        return 0


def _looks_like_evaluator_output(text: str) -> bool:
    """True when the text looks like a wishlist evaluator's drop analysis.

    Evaluators such as CardCompanion run as self-bots on the user's own
    token, so their analysis messages are authored by this very account and
    an author-ID filter alone never sees them.  Matching the content
    instead catches the analysis no matter who posted it.
    """
    if not text:
        return False
    if EVALUATOR_EXPIRY_HINT.search(text):
        return True
    return bool(
        EVALUATOR_CARD_CODE.search(text)
        and (HEART_HINT.search(text) or GEM_HINT.search(text))
    )


def _is_image_message(message: discord.Message) -> bool:
    if any(
        (attachment.content_type or "").startswith("image/")
        or Path(attachment.filename).suffix.lower()
        in {".png", ".jpg", ".jpeg", ".gif", ".webp"}
        for attachment in message.attachments
    ):
        return True
    # Karuta has shipped drop layouts that render the cards as embed images
    # instead of attachments; accept those too.
    for embed in message.embeds:
        image = getattr(embed, "image", None)
        thumbnail = getattr(embed, "thumbnail", None)
        if getattr(image, "url", None) or getattr(thumbnail, "url", None):
            return True
    return False


def _first_non_number_emoji(reactions: object) -> str | None:
    """The first reaction on a card message that is not a number keycap.

    Karuta pre-adds the grab emoji to every card message; numbered reactions
    (when present at all) belong to players picking slots, not to the grab.
    """
    for reaction in reactions or []:
        emoji = str(getattr(reaction, "emoji", "") or "")
        if emoji and emoji not in _NUMBER_KEYCAPS:
            return emoji
    return None


def _first_non_number_emoji_raw(raw_reactions: object) -> str | None:
    """Same as _first_non_number_emoji, for a raw REST payload's reactions."""
    for entry in raw_reactions or []:
        if not isinstance(entry, dict):
            continue
        emoji = entry.get("emoji") or {}
        if not isinstance(emoji, dict):
            continue
        name = str(emoji.get("name") or "")
        emoji_id = emoji.get("id")
        if emoji_id:
            if emoji.get("animated"):
                return f"<a:{name}:{emoji_id}>"
            return f"<:{name}:{emoji_id}>"
        if name and name not in _NUMBER_KEYCAPS:
            return name
    return None


def _normalized_emoji(emoji: str) -> str:
    """Compare keycap emoji reliably: strip the optional variation selector."""
    return emoji.replace("\ufe0f", "")


def _raw_emoji_names(raw_reactions: object) -> set[str]:
    """Normalized emoji names present in a raw REST payload's reactions."""
    names: set[str] = set()
    for entry in raw_reactions or []:
        if not isinstance(entry, dict):
            continue
        emoji = entry.get("emoji") or {}
        if isinstance(emoji, dict) and emoji.get("name"):
            names.add(_normalized_emoji(str(emoji["name"])))
    return names


def _number_keycaps_in(reactions: object) -> set[str]:
    """The normalized number keycaps (1-4) present on a message."""
    wanted = {_normalized_emoji(emoji) for emoji in REACTION_EMOJIS}
    found = {
        _normalized_emoji(str(getattr(reaction, "emoji", "") or ""))
        for reaction in reactions or []
    }
    return found & wanted


def _number_keycaps_in_raw(raw_reactions: object) -> set[str]:
    """Same as _number_keycaps_in, for a raw REST payload's reactions."""
    return _raw_emoji_names(raw_reactions) & {
        _normalized_emoji(emoji) for emoji in REACTION_EMOJIS
    }


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
        self._evaluator_tasks: dict[int, asyncio.Task[None]] = {}
        self.pending_grab: PendingGrab | None = None
        self.grab_cooldown_until = 0.0
        self.last_command_at = self._load_state()
        self.scheduler: asyncio.Task[None] | None = None
        self._claim_lock = asyncio.Lock()
        self._seen_announcements: deque[int] = deque(maxlen=256)
        self._ready_count = 0
        self._hinted_foreign_evaluator = False
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
        for task in self._evaluator_tasks.values():
            task.cancel()
        self._evaluator_tasks.clear()
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
            card_messages=[message] if _is_image_message(message) else [],
            announced_at=message.created_at.timestamp(),
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
            or message.id == pending.announcement_id
            or message.created_at.timestamp() > pending.expires_at
            or not _is_image_message(message)
        ):
            return pending
        if any(existing.id == message.id for existing in pending.card_messages):
            return pending
        if len(pending.card_messages) >= MAX_CARD_MESSAGES:
            return pending
        pending.card_messages.append(message)
        log.info(
            "Drop image received in %s (%d tracked).",
            message.channel.id,
            len(pending.card_messages),
        )
        await self._process_pending_drop(message.channel.id, pending)
        return pending

    # ---------------- evaluator / claim ----------------

    async def _handle_evaluator_message(
        self, message: discord.Message, *, reason: str = "author"
    ) -> None:
        """Object entry point; the gate/parse logic lives in the text core."""
        await self._handle_evaluator_text(
            message.channel.id,
            getattr(message, "id", 0),
            getattr(getattr(message, "author", None), "id", None),
            message.created_at.timestamp(),
            _message_text(message),
            reason=reason,
            embeds_count=len(getattr(message, "embeds", None) or []),
        )

    async def _handle_evaluator_text(
        self,
        channel_id: int,
        message_id: int,
        author_id: int | None,
        created_at: float,
        text: str,
        *,
        reason: str = "author",
        embeds_count: int = 0,
    ) -> None:
        if channel_id not in self.config.target_channels:
            return
        pending = self.pending_drops.get(channel_id)
        if pending is None or pending.evaluation_processed:
            if pending is None:
                log.info(
                    "Evaluator message %s ignored: no pending drop in channel %s "
                    "(was the drop announcement detected?).",
                    message_id,
                    channel_id,
                )
            return
        if created_at > pending.expires_at:
            log.info(
                "Evaluator message %s arrived after the claim window; drop discarded.",
                message_id,
            )
            self.pending_drops.pop(channel_id, None)
            return

        message_text = text
        if not message_text.strip() and not embeds_count:
            # Components V2 messages keep their text in components, which
            # discord.py-self cannot parse: re-read the raw payload so the
            # analysis is not mistaken for an empty placeholder.
            raw_data = await self._fetch_raw_message(channel_id, message_id)
            if raw_data is not None:
                raw_text = _text_from_raw(raw_data)
                if raw_text.strip():
                    message_text = raw_text
                    log.info(
                        "Evaluator message %s keeps its text in components "
                        "(Components V2); read %d chars via the raw API.",
                        message_id,
                        len(raw_text),
                    )
        log.info(
            "Evaluator message %s settled (%s; author=%s embeds=%d chars=%d): %s",
            message_id,
            reason,
            author_id if author_id is not None else "?",
            embeds_count,
            len(message_text),
            repr(message_text)[:160],
        )
        evaluation = parse_evaluator_message(message_text)
        if evaluation is None:
            evaluation = await self._recover_evaluator_text(
                channel_id,
                message_id,
                author_id,
                message_text,
                embeds_count=embeds_count,
                quiet=reason == "recheck",
            )
            if evaluation is None:
                return

        # A stale expiry means this text belongs to an older drop (a quoted
        # or re-edited analysis) and must not feed the current one.
        fresh_expiry = (
            evaluation.expires_at is not None
            and time.time() - evaluation.expires_at <= STALE_EXPIRY_GRACE_SECONDS
        )
        if evaluation.expires_at is not None and not fresh_expiry:
            log.info(
                "Evaluator message %s carries a stale expiry timestamp (%d); "
                "ignoring it.",
                message_id,
                evaluation.expires_at,
            )
            return
        if (
            pending.announced_at
            and created_at + 5.0 < pending.announced_at
            and not fresh_expiry
        ):
            # An edit to an evaluator message from a PREVIOUS drop (for example
            # the bot marking it as expired) must not feed the current drop.
            # A fresh expiry proves the message was just updated for THIS drop,
            # which is how evaluators that reuse one persistent message per
            # channel operate.
            log.info(
                "Evaluator message %s predates the current drop announcement; "
                "ignoring its edit.",
                message_id,
            )
            return

        pending.evaluation = evaluation
        if evaluation.expires_at is not None:
            pending.expires_at = min(pending.expires_at, float(evaluation.expires_at))

        await self._process_pending_drop(channel_id, pending)

    async def _recover_evaluator_text(
        self,
        channel_id: int,
        message_id: int,
        author_id: int | None,
        message_text: str,
        *,
        embeds_count: int = 0,
        quiet: bool = False,
    ) -> Evaluation | None:
        """Recover or diagnose an evaluator message the parser could not read.

        Empty messages are usually placeholders that get edited moments later;
        re-fetch their raw payload so a missed edit (or Components V2 text the
        library cannot parse) does not lose the drop.  Messages that look like
        an analysis (hearts or wishlist hints) but do not parse are real format
        problems and stay warnings; anything else is an unrelated notice from
        the same bot (ping, tip, expiry note) and is only logged for info.
        """
        if not message_text.strip() and not embeds_count:
            log.info(
                "Evaluator message %s is empty (probably a placeholder); "
                "re-fetching to catch the edited version.",
                message_id,
            )
            for attempt, delay in enumerate(EVALUATOR_REFETCH_DELAYS, start=1):
                await asyncio.sleep(delay)
                current = self.pending_drops.get(channel_id)
                if current is None or current.evaluation_processed:
                    return None
                raw_data = await self._fetch_raw_message(channel_id, message_id)
                if raw_data is None:
                    log.info(
                        "Re-fetch %d/%d of message %s returned no payload "
                        "(HTTP error or the message was deleted).",
                        attempt,
                        len(EVALUATOR_REFETCH_DELAYS),
                        message_id,
                    )
                    continue
                fresh_text = _text_from_raw(raw_data)
                raw_author = (raw_data.get("author") or {}).get("id")
                log.info(
                    "Re-fetch %d/%d of message %s: author=%s edited=%s chars=%d "
                    "preview=%s",
                    attempt,
                    len(EVALUATOR_REFETCH_DELAYS),
                    message_id,
                    raw_author,
                    bool(raw_data.get("edited_timestamp")),
                    len(fresh_text),
                    repr(fresh_text[:120]),
                )
                evaluation = parse_evaluator_message(fresh_text)
                if evaluation is not None:
                    log.info(
                        "Evaluator message %s became parseable after a re-fetch "
                        "(chars=%d).",
                        message_id,
                        len(fresh_text),
                    )
                    return evaluation
            log.info(
                "Evaluator message %s was still empty after %d re-fetches.",
                message_id,
                len(EVALUATOR_REFETCH_DELAYS),
            )
            return None

        if HEART_HINT.search(message_text) or "wish" in message_text.lower():
            if quiet:
                # Re-checked messages were not identified as evaluator output
                # by their author or content, so a non-match is expected.
                log.info(
                    "Re-checked message %s from author %s: no card lines.",
                    message_id,
                    author_id,
                )
            else:
                log.warning(
                    "Evaluator output looks like an analysis but had no parseable "
                    "entries (author_id=%s message_id=%s embeds=%d): %s",
                    author_id,
                    message_id,
                    embeds_count,
                    repr(message_text)[:400],
                )
        else:
            log.info(
                "Evaluator message %s has no card lines (likely a ping or "
                "notice from the same bot, not the drop analysis): %s",
                message_id,
                repr(message_text)[:160],
            )
        return None

    def _schedule_evaluator_message(
        self,
        message: discord.Message,
        settle_seconds: float = EVALUATOR_SETTLE_SECONDS,
        reason: str = "author",
    ) -> None:
        """Object entry point; scheduling itself is text based (see below)."""
        self._schedule_evaluator_text(
            message.channel.id,
            message.id,
            getattr(getattr(message, "author", None), "id", None),
            message.created_at.timestamp(),
            _message_text(message),
            settle_seconds=settle_seconds,
            reason=reason,
            embeds_count=len(getattr(message, "embeds", None) or []),
        )

    def _schedule_evaluator_text(
        self,
        channel_id: int,
        message_id: int,
        author_id: int | None,
        created_at: float,
        text: str,
        *,
        settle_seconds: float,
        reason: str,
        embeds_count: int = 0,
    ) -> None:
        """Schedule (or supersede) the settle of one evaluator message.

        Tasks are keyed by message ID, so a fresher copy of the same message
        (for example an edit payload) simply replaces the older task.
        """
        task = self._evaluator_tasks.get(message_id)
        if task is not None:
            task.cancel()
        task = asyncio.create_task(
            self._settle_evaluator_text_after_delay(
                channel_id,
                message_id,
                author_id,
                created_at,
                text,
                settle_seconds,
                reason,
                embeds_count,
            )
        )
        self._evaluator_tasks[message_id] = task
        task.add_done_callback(self._log_evaluator_result)

    async def _settle_evaluator_text_after_delay(
        self,
        channel_id: int,
        message_id: int,
        author_id: int | None,
        created_at: float,
        text: str,
        settle_seconds: float,
        reason: str,
        embeds_count: int,
    ) -> None:
        await asyncio.sleep(settle_seconds)
        if self._evaluator_tasks.get(message_id) is not asyncio.current_task():
            return
        self._evaluator_tasks.pop(message_id, None)
        await self._handle_evaluator_text(
            channel_id,
            message_id,
            author_id,
            created_at,
            text,
            reason=reason,
            embeds_count=embeds_count,
        )

    @staticmethod
    def _log_evaluator_result(task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            log.error("Evaluator processing failed: %r", error)

    def _own_user_id(self) -> int | None:
        connection = getattr(self, "_connection", None)
        user = getattr(connection, "user", None)
        return getattr(user, "id", None)

    async def _fetch_raw_message(
        self, channel_id: int, message_id: int
    ) -> dict | None:
        """Fetch a message's raw JSON payload (content, embeds, components)."""
        http = getattr(self, "http", None)
        if http is None or not hasattr(http, "get_message"):
            return None
        try:
            return await http.get_message(channel_id, message_id)  # type: ignore[arg-type]
        except discord.HTTPException as error:
            # http.get_message is built on "?limit=1&around=", which can miss
            # a message that is still the newest one in the channel - exactly
            # the moment an evaluator analysis is posted.  A small history
            # window ending at the target message is deterministic.
            log.info(
                "Raw fetch of message %s failed (%s); trying a history window.",
                message_id,
                error,
            )
            return await self._fetch_raw_message_via_history(
                http, channel_id, message_id
            )

    async def _fetch_raw_message_via_history(
        self, http: object, channel_id: int, message_id: int
    ) -> dict | None:
        """Read the message out of a ?before= window (deterministic)."""
        if not hasattr(http, "logs_from"):
            return None
        try:
            window = await http.logs_from(channel_id, 10, before=message_id + 1)
        except discord.HTTPException as error:
            log.info(
                "History window for message %s failed: %s", message_id, error
            )
            return None
        for entry in window or []:
            if not isinstance(entry, dict):
                continue
            try:
                if int(entry.get("id", 0)) == message_id:
                    return entry
            except (TypeError, ValueError):
                continue
        return None

    def _watch_log(self, message: discord.Message, event: str) -> None:
        """Trace messages seen while a drop is pending (post-mortem data).

        These lines show, for every message in a watched channel, who sent it
        and exactly what the bot could read from it - the fastest way to see
        which account posts the evaluator analysis and whether its content
        ever reaches this connection.
        """
        channel = getattr(message, "channel", None)
        channel_id = getattr(channel, "id", None)
        if channel_id is None or channel_id not in self.pending_drops:
            return
        author = getattr(message, "author", None)
        try:
            text = _message_text(message)
        except AttributeError:
            text = getattr(message, "content", "") or ""
        log.info(
            "[watch:%s] id=%s author=%s bot=%s webhook=%s type=%s flags=%s v2=%s "
            "embeds=%d attachments=%d components=%d chars=%d preview=%s",
            event,
            getattr(message, "id", "?"),
            getattr(author, "id", "?"),
            getattr(author, "bot", None),
            getattr(message, "webhook_id", None),
            getattr(message, "type", None),
            _flags_value(message),
            bool(_flags_value(message) & IS_COMPONENTS_V2_FLAG),
            len(getattr(message, "embeds", None) or []),
            len(getattr(message, "attachments", None) or []),
            len(getattr(message, "components", None) or []),
            len(text),
            repr(text[:100]),
        )

    def _watch_payload(self, channel_id: int, data: dict, event: str) -> None:
        """Trace a raw gateway payload while a drop is pending (post-mortem).

        MESSAGE_UPDATE payloads are the freshest copy of an edited message
        and the only place Components V2 text is visible without a re-fetch;
        these lines pin down exactly what the gateway delivered.
        """
        if channel_id not in self.pending_drops:
            return
        author = data.get("author") or {}
        text = _text_from_raw(data)
        log.info(
            "[watch:%s] id=%s author=%s bot=%s edited=%s v2=%s embeds=%d "
            "components=%d chars=%d preview=%s",
            event,
            data.get("id"),
            author.get("id"),
            author.get("bot"),
            bool(data.get("edited_timestamp")),
            bool(_raw_flags_value(data) & IS_COMPONENTS_V2_FLAG),
            len(data.get("embeds") or []),
            len(data.get("components") or []),
            len(text),
            repr(text[:100]),
        )

    async def _process_pending_drop(self, channel_id: int, pending: PendingDrop) -> None:
        evaluation = pending.evaluation
        if self.pending_drops.get(channel_id) is not pending:
            return
        if pending.evaluation_processed:
            return
        if evaluation is None:
            return
        if not pending.card_messages:
            log.info(
                "Evaluation ready in channel %s; still waiting for the drop image(s).",
                channel_id,
            )
            return

        expected = len(evaluation.cards)
        if (
            expected > len(pending.card_messages)
            and not pending.card_grace_scheduled
            and time.time() + CARD_ARRIVAL_GRACE_SECONDS < pending.expires_at
        ):
            if await self._combined_drop_confirmed(pending, expected):
                # The whole keycap row is on the tracked message: this is a
                # single-message (combined) drop, the "missing" card messages
                # will never arrive, and reacting now keeps the grab well
                # inside the ~60s claim window.
                pending.combined_confirmed = True
            else:
                # The evaluator lists more cards than have been seen as
                # messages; Karuta posts them a moment apart, so give the rest
                # a short grace before falling back to the closest tracked
                # message.
                pending.card_grace_scheduled = True
                log.info(
                    "Evaluation ready in channel %s; %d of %d card message(s) "
                    "tracked; waiting up to %.1fs for the rest.",
                    channel_id,
                    len(pending.card_messages),
                    expected,
                    CARD_ARRIVAL_GRACE_SECONDS,
                )
                asyncio.create_task(
                    self._wait_for_remaining_cards(channel_id, pending)
                )
                return

        pending.evaluation_processed = True
        # Every card the evaluator listed, with the stats the choice is made
        # from: this line is the proof that the whole evaluation was read.
        log.info(
            "Drop decision input in channel %s - %d card(s) evaluated: %s.",
            channel_id,
            len(evaluation.cards),
            "; ".join(
                "slot %d (♡%s%s, %s)"
                % (
                    card.slot,
                    card.wishlist_count,
                    f" #{card.print_number}"
                    if card.print_number is not None
                    else "",
                    card.name,
                )
                for card in evaluation.cards
            ),
        )
        chosen, reason = explain_choice(evaluation)
        if chosen is None:
            log.warning(
                "Skipping drop in channel %s - no card met the grabbing "
                "rules (see the evaluated cards above).",
                channel_id,
            )
            return
        if time.time() >= pending.expires_at:
            return

        target = (
            pending.card_messages[chosen.slot - 1]
            if chosen.slot <= len(pending.card_messages)
            else pending.card_messages[-1]
        )
        if chosen.slot > len(pending.card_messages):
            if pending.combined_confirmed:
                log.info(
                    "Combined drop: slot %d is grabbed with its keycap on the "
                    "single drop message.",
                    chosen.slot,
                )
            else:
                log.warning(
                    "Slot %d chosen but only %d card message(s) tracked; "
                    "targeting the last one.",
                    chosen.slot,
                    len(pending.card_messages),
                )
        log.info(
            "Slot %s -> %s (♡%s%s; %s).",
            chosen.slot,
            chosen.name,
            chosen.wishlist_count,
            f", #{chosen.print_number}" if chosen.print_number is not None else "",
            reason,
        )
        await self._claim_card(target, chosen.slot, pending.expires_at)

    async def _combined_drop_confirmed(
        self, pending: PendingDrop, expected: int
    ) -> bool:
        """True when the tracked drop message already carries the slot keycaps.

        Karuta's current layout puts every card of a drop into ONE message
        and pre-adds the number keycaps 1..N as the grab buttons.  When that
        keycap row is visible there is nothing left to wait for: reacting
        right away keeps the grab well inside the ~60s claim window instead
        of burning it on a grace period for messages that will never come.
        """
        if not pending.card_messages:
            return False
        message = pending.card_messages[0]
        wanted = {_normalized_emoji(emoji) for emoji in REACTION_EMOJIS[:expected]}
        if wanted <= _number_keycaps_in(getattr(message, "reactions", None)):
            return True
        channel = getattr(message, "channel", None)
        channel_id = getattr(channel, "id", None)
        if channel_id is None:
            return False
        raw_data = await self._fetch_raw_message(channel_id, message.id)
        if raw_data is None:
            return False
        return wanted <= _number_keycaps_in_raw(raw_data.get("reactions"))

    async def _wait_for_remaining_cards(
        self, channel_id: int, pending: PendingDrop
    ) -> None:
        """Re-check a drop after the card-arrival grace period."""
        await asyncio.sleep(CARD_ARRIVAL_GRACE_SECONDS)
        current = self.pending_drops.get(channel_id)
        if current is not pending:
            return
        evaluation = pending.evaluation
        if (
            evaluation is not None
            and len(evaluation.cards) > len(pending.card_messages)
            and not pending.combined_confirmed
            and await self._combined_drop_confirmed(
                pending, len(evaluation.cards)
            )
        ):
            # The keycap row appeared during the grace period: this is a
            # combined drop after all, not missing per-card messages.
            pending.combined_confirmed = True
        try:
            await self._process_pending_drop(channel_id, pending)
        except Exception as error:  # a background task must never die silently
            log.error("Deferred drop processing failed: %r", error)

    async def _resolve_grab_emoji(
        self, message: discord.Message, slot: int, expires_at: float
    ) -> tuple[str | None, str]:
        """Find the emoji to grab the card with.

        Grabbing in Karuta is an emoji reaction on the card message - not a
        button - and Karuta pre-adds that emoji to every card message, so the
        message itself is the source of truth.  Combined drops (one message,
        several cards) are grabbed with the slot's NUMBER KEYCAP: when the
        whole keycap row 1..N is already on the message, that is the grab
        button and it is used at once - waiting for a custom emoji would only
        burn claim-window time.  An explicit KARUTA_GRAB_EMOJI override
        always wins; a fresh REST copy and a short live wait cover messages
        whose emoji had not been added yet; the slot's number keycap is the
        final fallback for servers where cards are picked with numbered
        reactions.
        """
        override = (getattr(self.config, "grab_emoji", "") or "").strip()
        if override:
            return override, "KARUTA_GRAB_EMOJI override"

        keycap = (
            REACTION_EMOJIS[slot - 1] if 1 <= slot <= len(REACTION_EMOJIS) else None
        )
        reactions = getattr(message, "reactions", None)
        # Two or more number keycaps on one message = Karuta's combined drop
        # (one message, several cards): the keycaps ARE the grab buttons.
        if keycap is not None and len(_number_keycaps_in(reactions)) >= 2:
            return keycap, "slot keycap on the drop message"

        channel_id = getattr(getattr(message, "channel", None), "id", None)
        if channel_id is not None:
            raw_data = await self._fetch_raw_message(channel_id, message.id)
            if raw_data is not None:
                if keycap is not None and len(
                    _number_keycaps_in_raw(raw_data.get("reactions"))
                ) >= 2:
                    return keycap, "slot keycap in a fresh REST copy"
                emoji = _first_non_number_emoji_raw(raw_data.get("reactions"))
                if emoji:
                    return emoji, "reactions in a fresh REST copy"

        emoji = _first_non_number_emoji(reactions)
        if emoji:
            return emoji, "reactions on the card message"

        timeout = min(REACTION_READY_TIMEOUT_SECONDS, expires_at - time.time() - 0.5)
        if timeout > 0:

            def check(reaction: discord.Reaction, _user: discord.User) -> bool:
                same = getattr(getattr(reaction, "message", None), "id", None)
                if same != message.id:
                    return False
                emoji = str(getattr(reaction, "emoji", "") or "")
                return bool(emoji) and emoji not in _NUMBER_KEYCAPS

            try:
                reaction, user = await self.wait_for(
                    "reaction_add", check=check, timeout=timeout
                )
            except asyncio.TimeoutError:
                pass
            else:
                return str(reaction.emoji), f"live reaction (added by {user})"

        if keycap is not None:
            return keycap, "slot keycap fallback"
        return None, "no emoji found"

    async def _claim_card(
        self, message: discord.Message, slot: int, expires_at: float
    ) -> None:
        """React to the chosen card message with the grab emoji.

        The old flow reacted with the slot's number keycap and then waited
        for another player to add that same number first; nobody ever did,
        so every grab silently expired ("... never appeared.").  Grabbing is
        simply reacting with the emoji Karuta put on the card message, so
        the emoji is resolved and the reaction is sent at once.
        """
        async with self._claim_lock:
            if self.pending_grab is not None:
                log.info("Grab already in flight.")
                return
            if time.time() < self.grab_cooldown_until:
                log.info("Grab cooldown active.")
                return
            if time.time() >= expires_at:
                return

            emoji, source = await self._resolve_grab_emoji(message, slot, expires_at)
            if emoji is None:
                log.warning(
                    "No grab emoji found for message %s; set KARUTA_GRAB_EMOJI "
                    "in .env (for a custom emoji paste it as <:name:id>).",
                    message.id,
                )
                return

            # Human click latency.
            await asyncio.sleep(jitter(*REACTION_DELAY_SECONDS))
            if time.time() >= expires_at:
                return

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
            log.info(
                "Reacted %s to %s (slot %d, emoji from %s); awaiting confirmation.",
                emoji,
                message.id,
                slot,
                source,
            )
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
        if self.user is None:
            return

        # Karuta's own messages drive the grab state machine.
        if message.author.id == self.config.drop_bot_id:
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
            return

        # Everything else could be an evaluator message.  Trace it while a
        # drop is pending so the log shows exactly what the bot sees.
        self._watch_log(message, "create")

        text = _message_text(message)
        is_configured_evaluator = message.author.id == self.config.evaluator_bot_id
        looks_like_analysis = _looks_like_evaluator_output(text)
        is_components_v2 = bool(_flags_value(message) & IS_COMPONENTS_V2_FLAG)

        if is_configured_evaluator or looks_like_analysis:
            # NOTE: messages from this very account are included on purpose.
            # Self-bot evaluators (CardCompanion, Keqing, ...) post their
            # analysis using the same user token this bot runs on, so the
            # analysis is authored by ourselves and an author filter alone
            # would never see it.
            if looks_like_analysis:
                log.info(
                    "Wishlist analysis detected from author %s (bot=%s, "
                    "configured_evaluator=%s).",
                    message.author.id,
                    getattr(message.author, "bot", None),
                    is_configured_evaluator,
                )
                self._schedule_evaluator_message(
                    message,
                    settle_seconds=EVALUATOR_CONTENT_SETTLE_SECONDS,
                    reason="content",
                )
            else:
                self._schedule_evaluator_message(message)
            return

        if (
            is_components_v2
            and message.channel.id in self.pending_drops
        ):
            # Components V2: the text lives in components the library cannot
            # parse, so it cannot be checked here - the handler reads it via
            # the raw API.
            log.info(
                "Components V2 message %s from author %s during a drop; "
                "reading its text via the raw API.",
                message.id,
                message.author.id,
            )
            self._schedule_evaluator_message(
                message,
                settle_seconds=EVALUATOR_CONTENT_SETTLE_SECONDS,
                reason="components",
            )
            return

        if (
            not text.strip()
            and not message.embeds
            and not getattr(message, "attachments", None)
            and message.channel.id in self.pending_drops
        ):
            # An empty message from any author (this account included) while
            # a drop is pending: the gateway often delivers self-bot messages
            # before their content lands, and Components V2 messages always
            # read as empty.  The settle re-reads the raw payload shortly
            # after arrival.
            self._schedule_evaluator_message(
                message,
                settle_seconds=EVALUATOR_RECHECK_SETTLE_SECONDS,
                reason="recheck",
            )
            return

        if (
            not self._hinted_foreign_evaluator
            and getattr(message.author, "bot", False)
            and HEART_HINT.search(text)
        ):
            self._hinted_foreign_evaluator = True
            log.warning(
                "Message from author %s looks like an evaluation but matches "
                "neither WISHLIST_EVALUATOR_ID=%s nor DROP_BOT_ID=%s and did "
                "not parse as a known analysis format; copy the evaluator's "
                "ID (right-click it in Discord -> Copy User ID) and set "
                "WISHLIST_EVALUATOR_ID.",
                message.author.id,
                self.config.evaluator_bot_id,
                self.config.drop_bot_id,
            )

    async def on_message_edit(
        self, _before: discord.Message, after: discord.Message
    ) -> None:
        if self.user is None:
            return
        if after.author.id == self.config.drop_bot_id:
            return

        self._watch_log(after, "edit")
        if after.author.id == self.config.evaluator_bot_id:
            self._schedule_evaluator_message(after)
            return
        if _looks_like_evaluator_output(_message_text(after)):
            self._schedule_evaluator_message(
                after,
                settle_seconds=EVALUATOR_CONTENT_SETTLE_SECONDS,
                reason="content",
            )

    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent) -> None:
        """Catch evaluator edits for messages that fell out of the cache.

        The edit payload is the freshest possible copy of the message - and the
        only place Components V2 text is visible without a re-fetch - so the
        settle is scheduled directly from it.  No channel object is needed and
        a superseded task for the same message ID is simply replaced.
        """
        data = getattr(payload, "data", None) or {}
        author = data.get("author") or {}
        try:
            author_id = int(author.get("id", ""))
        except (TypeError, ValueError):
            return
        if author_id == self.config.drop_bot_id:
            return
        channel_id = payload.channel_id
        message_id = payload.message_id
        payload_text = _text_from_raw(data)
        is_evaluator = author_id == self.config.evaluator_bot_id
        is_v2_payload = bool(_raw_flags_value(data) & IS_COMPONENTS_V2_FLAG)

        if not is_evaluator:
            # Any other author (this account included: self-bot evaluators)
            # only matters while a drop is pending in that channel.
            if channel_id not in self.pending_drops:
                return
            if (
                not _looks_like_evaluator_output(payload_text)
                and not is_v2_payload
                and (payload_text or author_id != self._own_user_id())
            ):
                return

        self._watch_payload(channel_id, data, "edit-raw")
        looks_like = _looks_like_evaluator_output(payload_text)
        self._schedule_evaluator_text(
            channel_id,
            message_id,
            author_id,
            discord.utils.snowflake_time(message_id).timestamp(),
            payload_text,
            settle_seconds=(
                EVALUATOR_SETTLE_SECONDS
                if is_evaluator
                else EVALUATOR_CONTENT_SETTLE_SECONDS
            ),
            reason=(
                "author"
                if is_evaluator
                else ("content" if looks_like else "components")
            ),
            embeds_count=len(data.get("embeds") or []),
        )


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
