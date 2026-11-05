"""Fingerprint, timing and connection-hardening helpers.

Every knob here exists to make the session indistinguishable from a person
sitting in front of a browser/desktop client, and to make restarts look like
the same install coming back online instead of a fresh login.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import discord

# --------------------------------------------------------------------------
# Timing primitives
# --------------------------------------------------------------------------

def jitter(low: float, high: float) -> float:
    return random.uniform(low, high)


def skew(base: float, ratio: float = 0.12, floor: float = 1.0) -> float:
    """Widen a fixed interval into a plausible human range."""
    return max(floor, base * random.uniform(1.0 - ratio, 1.0 + ratio))


class Backoff:
    """Exponential backoff with decorrelated jitter, for reconnect loops."""

    def __init__(self, base: float = 5.0, cap: float = 300.0, factor: float = 1.9) -> None:
        self._base = base
        self._cap = cap
        self._factor = factor
        self._current = base

    def reset(self) -> None:
        self._current = self._base

    def next(self) -> float:
        value = min(self._cap, self._current)
        self._current = min(self._cap, self._current * self._factor)
        return value * random.uniform(0.75, 1.35)


# --------------------------------------------------------------------------
# Durable state
# --------------------------------------------------------------------------

class JsonStore:
    """Crash-safe JSON file store (atomic replace, never leaves half a file)."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()

    def load(self) -> dict[str, Any]:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return {}
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return {}
        return data if isinstance(data, dict) else {}

    def save(self, data: dict[str, Any]) -> None:
        with self._lock:
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            try:
                payload = json.dumps(data, indent=2) + "\n"
                with open(tmp, "w", encoding="utf-8") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp, self._path)
            except OSError as error:
                raise OSError(f"Cannot persist state to {self._path}: {error}") from error


class SingleInstance:
    """PID lock so two copies never hold the same session at once."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._acquired = False

    @staticmethod
    def _alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        # PIDs get reused in fresh containers; confirm it is really our script.
        try:
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
        except OSError:
            return True
        return b"bot.py" in cmdline

    def acquire(self) -> bool:
        try:
            pid = int(self._path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            pid = 0
        if pid and pid != os.getpid() and self._alive(pid):
            return False
        try:
            self._path.write_text(str(os.getpid()), encoding="utf-8")
        except OSError as error:
            raise OSError(f"Cannot create instance lock at {self._path}: {error}") from error
        self._acquired = True
        return True

    def release(self) -> None:
        if not self._acquired:
            return
        try:
            if self._path.read_text(encoding="utf-8").strip() == str(os.getpid()):
                self._path.unlink()
        except OSError:
            pass


def stable_installation_id(store: JsonStore) -> str:
    """Persist one installation id forever, like a real client install."""
    data = store.load()
    value = data.get("installation_id")
    if not isinstance(value, str) or len(value) < 8:
        value = str(uuid.uuid4())
        data["installation_id"] = value
        store.save(data)
    return value


# --------------------------------------------------------------------------
# Fingerprint
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class StealthProfile:
    """Consistent client identity applied to the HTTP and gateway layers."""

    locale: str = "en-US"
    platform: str = "web"
    activity_pool: tuple[str, ...] = field(default_factory=tuple)

    async def build(self, client: discord.Client) -> discord.HeadersContext:
        # The library's own session and proxy settings are reused so the
        # context stays coherent with what curl_cffi is impersonating.
        http = client.http
        session = getattr(http, "_HTTPClient__asession", None)

        async def make(active: Any) -> discord.HeadersContext:
            if self.platform == "desktop":
                try:
                    return await discord.HeadersContext.desktop(
                        active, http.proxy, http.proxy_auth
                    )
                except Exception:
                    pass
            return await discord.HeadersContext.default(
                active, http.proxy, http.proxy_auth
            )

        if session is None:
            import aiohttp

            async with aiohttp.ClientSession() as fallback:
                return self._apply(await make(fallback))
        return self._apply(await make(session))

    def _apply(self, context: discord.HeadersContext) -> discord.HeadersContext:
        props = dict(context.super_properties)
        gateway = dict(context.extra_gateway_properties or {})

        # Volatile per-launch fields, freshly randomised every start.
        props["system_locale"] = self.locale
        props["client_app_state"] = "focused" if random.random() < 0.55 else "unfocused"
        props.setdefault("has_client_mods", False)

        # Slow-connect shape; matches a cold app start rather than a reskin.
        gateway.setdefault("is_fast_connect", False)
        gateway.setdefault("gateway_connect_reasons", "AppSkeleton")

        # Rebuilding re-encodes X-Super-Properties so the header and the
        # IDENTIFY payload can never drift apart.
        return discord.HeadersContext(
            super_properties=props,
            extra_gateway_properties=gateway,
            platform=context.platform,  # type: ignore[arg-type]
            browser_type=context.browser_type,  # type: ignore[arg-type]
            browser_major_version=context.browser_major_version,
        )


# --------------------------------------------------------------------------
# CAPTCHA
# --------------------------------------------------------------------------

async def console_captcha_handler(captcha: Any, _client: discord.Client) -> str:
    """Solve a CAPTCHA on the console instead of failing the login outright."""
    sitekey = getattr(captcha, "sitekey", "")
    rqdata = getattr(captcha, "rqdata", "")
    service = getattr(captcha, "service", "")

    print("\n" + "=" * 68, flush=True)
    print("CAPTCHA required (service=%s sitekey=%s)" % (service, sitekey), flush=True)
    print("Open https://discord.com/captcha , solve it, then copy", flush=True)
    print("`captcha_key` out of DevTools -> Network -> the hcaptcha response", flush=True)
    print("payload, or from localStorage['captcha_key'].", flush=True)
    if rqdata:
        print("rqdata: %s" % rqdata, flush=True)
    print("=" * 68, flush=True)

    for attempt in range(3):
        token = await asyncio.to_thread(
            input, "captcha_key (%d/3): " % (attempt + 1)
        )
        token = (token or "").strip()
        if token:
            return token
    raise RuntimeError("No CAPTCHA solution supplied.")


# --------------------------------------------------------------------------
# Presence
# --------------------------------------------------------------------------

class PresenceRotator:
    """Drift the status/activity on a slow random cadence."""

    def __init__(self, client: discord.Client, pool: tuple[str, ...]) -> None:
        self._client = client
        self._pool = pool
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._pool and (self._task is None or self._task.done()):
            self._task = asyncio.create_task(self._run())

    def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()

    async def _run(self) -> None:
        while not self._client.is_closed():
            await asyncio.sleep(jitter(1500.0, 3300.0))
            if self._client.is_closed():
                return
            roll = random.random()
            try:
                if roll < 0.18:
                    await self._client.change_presence(status=discord.Status.idle)
                elif roll < 0.34:
                    await self._client.change_presence(
                        status=discord.Status.online,
                        activity=discord.Game(random.choice(self._pool)),
                    )
                else:
                    await self._client.change_presence(
                        status=discord.Status.online, activity=None
                    )
            except Exception:
                pass
