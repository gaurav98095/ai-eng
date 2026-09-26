"""Best-effort Redis pub/sub; PostgreSQL remains the source of truth."""

from __future__ import annotations

import json
import logging
from typing import Any

from redis.asyncio import Redis as AsyncRedis

logger = logging.getLogger(__name__)


class RedisEvents:
    def __init__(self, url: str, *, tls: bool = False) -> None:
        self.url = url
        self.tls = tls
        # Do not pass ``ssl=None``: redis-py treats the presence of the
        # keyword as an instruction to construct an SSL connection, and the
        # plain TCP connection class rejects it.
        options = {"decode_responses": True}
        if tls:
            options["ssl"] = True
        self.client = AsyncRedis.from_url(url, **options)

    async def mint_ticket(self, ticket: str, value: str, expires_in: int) -> None:
        """Store one short-lived EventSource ticket."""
        await self.client.setex(f"sse-ticket:{ticket}", expires_in, value)

    async def consume_ticket(self, ticket: str) -> str | None:
        """Atomically consume a ticket so concurrent replays cannot succeed."""
        return await self.client.getdel(f"sse-ticket:{ticket}")

    async def publish(
        self, session_id: str, event: str, payload: dict[str, Any]
    ) -> None:
        try:
            await self.client.publish(
                f"events:{session_id}", json.dumps({"event": event, **payload})
            )
        except Exception as exc:
            logger.warning(
                "redis_event_failed operation=publish session_id=%s event=%s error=%s",
                session_id,
                event,
                type(exc).__name__,
            )

    async def close(self) -> None:
        await self.client.aclose()

    async def ping(self) -> bool:
        """Check the cache dependency without exposing Redis to callers."""
        try:
            return bool(await self.client.ping())
        except Exception:
            return False


async def subscribe(url: str, session_id: str, *, tls: bool = False):
    """Yield pub/sub payloads; callers decide keepalive and disconnect policy."""
    options = {"decode_responses": True}
    if tls:
        options["ssl"] = True
    client = AsyncRedis.from_url(url, **options)
    pubsub = client.pubsub()
    await pubsub.subscribe(f"events:{session_id}")
    try:
        async for item in pubsub.listen():
            if item.get("type") == "message":
                yield json.loads(item["data"])
    finally:
        await pubsub.unsubscribe()
        await pubsub.close()
        await client.close()
