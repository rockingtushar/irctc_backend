import json
import os
from typing import Any, Optional

from dotenv import load_dotenv
from upstash_redis.asyncio import Redis


load_dotenv()


REDIS_URL = os.getenv("UPSTASH_REDIS_REST_URL")
REDIS_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN")


if not REDIS_URL or not REDIS_TOKEN:
    raise RuntimeError(
        "UPSTASH_REDIS_REST_URL and "
        "UPSTASH_REDIS_REST_TOKEN must be configured."
    )


redis = Redis(
    url=REDIS_URL,
    token=REDIS_TOKEN,
)


async def cache_get(
    key: str,
) -> Optional[Any]:
    value = await redis.get(key)

    if value is None:
        return None

    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value

    return value


async def cache_set(
    key: str,
    value: Any,
    ttl_seconds: int,
) -> None:

    serialized = json.dumps(
        value,
        ensure_ascii=False,
    )

    await redis.set(
        key,
        serialized,
        ex=ttl_seconds,
    )


async def cache_delete(
    key: str,
) -> None:
    await redis.delete(key)


async def cache_exists(
    key: str,
) -> bool:
    result = await redis.exists(key)

    return bool(result)