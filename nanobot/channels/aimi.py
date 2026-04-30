"""Nanobot channel adapter for AIMI."""

from __future__ import annotations

import asyncio
import importlib.util
from typing import Any

from loguru import logger
from pydantic import Field

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.config.schema import Base

AIMI_AVAILABLE = importlib.util.find_spec("aimi") is not None

if AIMI_AVAILABLE:
    import aimi


class AimiConfig(Base):
    enabled: bool = False
    token: str = ""
    allow_from: list[str] = Field(default=["*"])


class AimiChannel(BaseChannel):
    """Bridge AIMI platform into nanobot."""

    name = "aimi"
    display_name = "AIMI"

    @classmethod
    def default_config(cls) -> dict[str, Any]:
        return AimiConfig().model_dump(by_alias=True)

    def __init__(self, config: Any, bus: MessageBus):
        if isinstance(config, dict):
            config = AimiConfig.model_validate(config)
        super().__init__(config, bus)
        self.config: AimiConfig = config
        self._client: aimi.Client | None = None
        self._session_role_fetched: set[str] = set()

    async def start(self) -> None:
        if not AIMI_AVAILABLE:
            logger.error("aimi package not installed. Run: pip install -e ../aimi_sdk/aimi_py")
            return

        if not self.config.token:
            logger.error("AIMI token not configured")
            return

        self._client = aimi.Client(bot_token=self.config.token)

        @self._client.event
        async def on_message(raw: Any) -> None:
            # SDK currently passes the raw JSON dict; wrap it into Message.
            try:
                if isinstance(raw, dict):
                    payload = raw.get("data", raw)
                    msg = aimi.Message.model_validate(payload)
                else:
                    msg = raw
            except Exception:
                logger.exception("AIMI failed to parse incoming message: {}", raw)
                return

            text = ""
            if msg.content_obj and msg.content_obj.text:
                text = msg.content_obj.text

            meta: dict[str, Any] = {}
            if self._client and msg.session_id not in self._session_role_fetched:
                try:
                    session_role = await self._client.get_session_role_prompt(msg.session_id)
                    self._session_role_fetched.add(msg.session_id)
                    if session_role:
                        meta["session_role"] = session_role
                except Exception:
                    logger.exception("Failed to get session_role_prompt for session {}", msg.session_id)

            await self._handle_message(
                sender_id=msg.sender_id,
                chat_id=msg.session_id,
                content=text,
                metadata=meta,
            )

        # NOTE: These events are registered but the SDK currently only
        # dispatches "message". They are kept for forward-compatibility.
        @self._client.event
        async def on_connect() -> None:
            logger.info("AIMI channel connected")

        @self._client.event
        async def on_disconnect() -> None:
            logger.info("AIMI channel disconnected")

        @self._client.event
        async def on_error(exc: Exception) -> None:
            logger.error("AIMI channel error: {}", exc)

        self._running = True
        try:
            await self._client.start()
        except asyncio.CancelledError:
            self._running = False
            raise
        except Exception as e:
            self._running = False
            logger.error("AIMI client error: {}", e)

    async def stop(self) -> None:
        self._running = False
        if self._client:
            await self._client.close()
            self._client = None

    async def send(self, msg: OutboundMessage) -> None:
        if not self._client or not self._client.is_connected():
            logger.warning("AIMI client not connected; dropping outbound message")
            return
        try:
            await self._client.send_message(
                session_id=msg.chat_id,
                text=msg.content,
            )
        except Exception as e:
            logger.error("Error sending AIMI message: {}", e)
            raise
