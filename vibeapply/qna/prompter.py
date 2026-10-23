from __future__ import annotations

from inputimeout import TimeoutOccurred, inputimeout

"""
Discord-based prompt interface for collecting manual answers and sending notifications.

Pythonic, single-client design:
- Creates one Discord client per DiscordPrompt instance.
- Runs client on a dedicated background event loop thread.
- Schedules coroutines on that loop instead of using asyncio.run.
- Falls back to console input and logs issues instead of failing silently.

Environment variables used:
- DISCORD_BOT_TOKEN: Bot token (required to enable Discord integration)
- DISCORD_CHANNEL_ID: Channel ID to post questions/notifications (required)
- DISCORD_ALLOWED_USER_ID: Optional single user ID to accept answers from
- DISCORD_PROMPT_TIMEOUT: Optional timeout for answers (seconds, default 300)
"""

import asyncio
import inspect
import logging
import os
import threading
from abc import abstractmethod
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Optional

import discord  # type: ignore
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


class Prompter:
    @abstractmethod
    def ask(self, question: str) -> str | None:
        pass

    @abstractmethod
    def send(self, message: str) -> bool:
        pass


class DiscordPrompt(Prompter):
    """Minimal Discord-based prompter.

    Public methods:
      - ask(question: str) -> str
      - send(message: str) -> bool
    """

    def __init__(self, testing: bool = False) -> None:
        # Read config from environment
        self.token: Optional[str] = os.getenv("DISCORD_BOT_TOKEN")
        ch = os.getenv("DISCORD_TEST_CHANNEL_ID") if testing else os.getenv("DISCORD_PROMPT_CHANNEL_ID")
        self.prompt_channel_id: Optional[int] = int(ch) if ch and ch.isdigit() else None

        ch = os.getenv("DISCORD_TEST_CHANNEL_ID") if testing else os.getenv("DISCORD_NOTIF_CHANNEL_ID")
        self.notif_channel_id: Optional[int] = int(ch) if ch and ch.isdigit() else None

        allowed = os.getenv("DISCORD_ALLOWED_USER_ID")
        self.allowed_user_id: Optional[int] = int(allowed) if allowed and allowed.isdigit() else None
        try:
            self.timeout: float = float(os.getenv("DISCORD_PROMPT_TIMEOUT", "300"))
        except ValueError:
            self.timeout = 300.0

        # Require configuration; always enable the bot
        if not self.token or self.prompt_channel_id is None:
            logger.error("DISCORD_BOT_TOKEN and DISCORD_PROMPT_CHANNEL_ID must be set")
            raise RuntimeError("DiscordPrompt requires DISCORD_BOT_TOKEN and DISCORD_PROMPT_CHANNEL_ID")

        # Runtime state
        self._ready: Optional[threading.Event] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._client = None  # discord.Client, set on start

        self._start_client_once()

    # ---------- Setup / lifecycle ----------
    def _start_client_once(self) -> None:
        if self._client is not None:
            return
        intents = discord.Intents.default()
        intents.message_content = True
        self._client = discord.Client(intents=intents)
        self._ready = threading.Event()
        self._loop = asyncio.new_event_loop()

        @self._client.event
        async def on_ready():  # type: ignore
            logger.info("Discord client connected and ready")
            if self._ready:
                self._ready.set()

        @self._client.event
        async def on_error(event_method, *args, **kwargs):  # type: ignore
            logger.error("Discord client error in %s", event_method, exc_info=True)

        def _run():
            assert self._loop is not None and self._client is not None
            asyncio.set_event_loop(self._loop)
            logger.info("Starting Discord client event loop thread")
            # Don’t block here; keep the loop running forever
            self._loop.create_task(self._client.start(self.token, reconnect=True))  # type: ignore[arg-type]
            self._loop.run_forever()

        t = threading.Thread(target=_run, name="DiscordPromptLoop", daemon=True)
        t.start()
        # Wait briefly for ready; non-fatal if it times out
        if self._ready:
            self._ready.wait(timeout=60)

    def _ensure_ready(self):
        if not self._ready:
            raise RuntimeError("Discord client not initialized")
        logger.debug("Waiting for Discord client readiness")
        self._ready.wait(timeout=15)
        ok = self._ready.is_set()
        if not ok:
            logger.warning("Discord client not ready after timeout")
        return ok

    # ---------- Public API ----------
    def ask(self, question: str) -> str | None:
        """Prompt via Discord and return the user's reply, or 'None' on timeout/failure."""
        if not self._client or not self._loop:
            logger.error("DiscordPrompt not initialized correctly (client/loop missing)")
            return None
        # Optionally wait for ready, but continue regardless
        if not self._ensure_ready():
            raise RuntimeError("Discord client not ready")

        logger.debug("Scheduling Discord question to channel %s", self.prompt_channel_id)
        fut = asyncio.run_coroutine_threadsafe(self._ask_coro(question), self._loop)
        try:
            ans = fut.result(timeout=self.timeout + 10)
            logger.debug("Discord answer: %s", (ans[:120] + "…") if ans and len(ans) > 120 else ans)
            return ans
        except FuturesTimeoutError:
            logger.warning("DiscordPrompt.ask timed out")
            return None

    def send(self, message: str):
        """Send a one-off notification message to the configured channel (best-effort)."""
        if not self._client or not self._loop:
            logger.error("DiscordPrompt not initialized correctly (client/loop missing)")
            raise RuntimeError("Discord client not initialized")
        if not self._ensure_ready():
            raise RuntimeError("Discord client not ready")
        logger.debug("Scheduling notification to channel %s (%d chars)", self.notif_channel_id, len(message))
        fut = asyncio.run_coroutine_threadsafe(self._send_coro(message), self._loop)
        # Let exceptions propagate to caller
        fut.result(timeout=30)

    # ---------- Internal coroutines ----------
    async def _ask_coro(self, question: str) -> str | None:
        channel = self._get_channel(self.prompt_channel_id)
        if channel is None:
            return None
        # await _maybe_await(getattr(channel, "send", lambda *_: None)(f"Q: {question}"))
        embed = discord.Embed(color=0x6e34e3, title="Question", description=question)
        if isinstance(channel, discord.TextChannel) or isinstance(channel, discord.DMChannel):
            await channel.send(embed=embed)
            logger.debug("Question sent to channel %s", self.prompt_channel_id)
        else:
            logger.error("Discord channel %s is not a text channel", self.prompt_channel_id)
            return None

        def check(message):  # type: ignore[no-untyped-def]
            if getattr(message.author, "bot", False):
                return False
            if getattr(getattr(message, "channel", None), "id", None) != self.prompt_channel_id:
                return False
            if self.allowed_user_id and getattr(getattr(message, "author", None), "id", None) != self.allowed_user_id:
                return False
            return True

        try:
            assert self._client is not None
            msg = await self._client.wait_for("message", timeout=self.timeout, check=check)  # type: ignore[arg-type]
            ans = (getattr(msg, "content", "") or "").strip() or None
            logger.debug("Discord reply from user_id=%s", getattr(getattr(msg, "author", None), "id", "?"))
            return ans
        except asyncio.TimeoutError:
            logger.warning("Timed out waiting for Discord reply in channel %s", self.prompt_channel_id)
            await channel.send("Timed out waiting for a reply.")
            return None

    async def _send_coro(self, message: str):
        channel = self._get_channel(self.notif_channel_id)
        if channel is None:
            raise RuntimeError(f"Discord channel {self.notif_channel_id} not found or bot lacks access")
        embed = discord.Embed(color=0x36eb90, title="Notification", description=message)
        if isinstance(channel, discord.TextChannel) or isinstance(channel, discord.DMChannel):
            await channel.send(embed=embed)
            logger.debug("Notification sent to channel %s", self.notif_channel_id)
        else:
            raise RuntimeError(f"Discord channel {self.notif_channel_id} is not a text channel")

    # ---------- Utilities ----------
    def _get_channel(self, channel_id: Optional[int] = None):
        assert self._client is not None
        chan = self._client.get_channel(int(channel_id)) if channel_id is not None else None
        if chan is None:
            logger.error("Discord channel %s not found or bot lacks access", channel_id)
        return chan


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


class StdioPrompter(Prompter):
    """Standard input/output prompter for console interaction."""

    def ask(self, question: str) -> str | None:
        """Prompt via console and return the user's reply."""
        try:
            ans = inputimeout(f"Q: {question}\nA: ", timeout=300)
            return ans.strip() or None
        except TimeoutOccurred:
            print("No input received within timeout period. Returning None.")
            return None

    def send(self, message: str) -> bool:
        """Send a one-off notification message to the console."""
        print(f"Notification: {message}")
        return True
