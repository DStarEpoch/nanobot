"""Nanobot channel adapter for AIMI."""

from __future__ import annotations

import logging
from typing import Any
from pydantic import Field

from nanobot.config.schema import Base
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.bus.events import OutboundMessage

import aimi

_LOGGER = logging.getLogger(__name__)


class AimiConfig(Base):
    enabled: bool = False
    token: str = ""
    allow_from: list[str] = Field(default_factory=list)
    gateway_url: str | None = None


class AimiChannel(BaseChannel):
    """Bridge AIMI platform into nanobot."""

    name = "aimi"
    display_name = "AIMI"

    def __init__(self, config: Any, bus: MessageBus):
        if isinstance(config, dict):
            config = AimiConfig(**config)
        super().__init__(config, bus)
        self.client = aimi.Client()
        self._running = False

    @classmethod
    def default_config(cls) -> dict[str, Any]:
        return AimiConfig().model_dump(by_alias=True)

    async def start(self) -> None:
        """Start the AIMI client and forward messages to nanobot."""
        self._running = True

        @self.client.event
        async def on_ready() -> None:
            _LOGGER.info("AimiChannel connected as aimi client")

        @self.client.event
        async def on_message(message: aimi.Message) -> None:
            # Ignore messages from ourselves (if bot user is known)
            await self._handle_message(
                sender_id=str(message.author.id),
                chat_id=str(message.channel_id),
                content=message.content,
                media=[],
                metadata={"message_id": str(message.id)},
            )

        # Override gateway URL if configured
        if self.config.gateway_url:
            self.client.http.base_url = self.config.gateway_url.replace("wss:", "https:").replace("ws:", "http:")
            # TODO: pass gateway_url override to AimiWebSocket if supported

        await self.client.start(self.config.token)

    async def stop(self) -> None:
        """Stop the AIMI client."""
        self._running = False
        await self.client.close()

    async def send(self, msg: OutboundMessage) -> None:
        """Send nanobot's reply back to AIMI."""
        channel = aimi.models.TextChannel(self.client, {"id": int(msg.chat_id)})
        await channel.send(msg.content)
