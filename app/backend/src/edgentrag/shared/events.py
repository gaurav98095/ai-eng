"""Best-effort Redis cache/pubsub; PostgreSQL remains the source of truth."""

from __future__ import annotations

import json
from typing import Any

from redis import Redis
from redis.asyncio import Redis as AsyncRedis


class RedisEvents:
    def __init__(self, url: str, *, history_turns: int = 8, tls: bool = False) -> None:
        self.url = url
        self.history_turns = history_turns
        self.tls = tls
        self.client = Redis.from_url(url, decode_responses=True, ssl=tls or None)

    def append_history(self, session_id: str, role: str, content: str) -> None:
        key = f"chat:{session_id}"
        try:
            self.client.rpush(key, json.dumps({"role": role, "content": content}))
            self.client.ltrim(key, -(self.history_turns * 2), -1)
            self.client.expire(key, 86400)
        except Exception:
            pass

    def publish(self, session_id: str, event: str, payload: dict[str, Any]) -> None:
        try:
            self.client.publish(f"events:{session_id}", json.dumps({"event": event, **payload}))
        except Exception:
            pass

    def close(self) -> None:
        self.client.close()

    def ping(self) -> bool:
        """Check the cache dependency without exposing Redis to callers."""
        try:
            return bool(self.client.ping())
        except Exception:
            return False


async def subscribe(url: str, session_id: str, *, tls: bool = False):
    """Yield pub/sub payloads; callers decide keepalive and disconnect policy."""
    client = AsyncRedis.from_url(url, decode_responses=True, ssl=tls or None)
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
