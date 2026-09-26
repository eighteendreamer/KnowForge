import math
import time

from redis.asyncio import Redis

from app.core.errors import AppError

TOKEN_BUCKET = """
local now = tonumber(ARGV[1])
local capacity = tonumber(ARGV[2])
local daily_limit = tonumber(ARGV[3])
local state = redis.call('HMGET', KEYS[1], 'tokens', 'updated')
local tokens = tonumber(state[1]) or capacity
local updated = tonumber(state[2]) or now
tokens = math.min(capacity, tokens + math.max(0, now - updated) * capacity / 60)
local daily = tonumber(redis.call('GET', KEYS[2])) or 0
if daily >= daily_limit then return tonumber(ARGV[4]) end
if tokens < 1 then return math.ceil((1 - tokens) * 60 / capacity) end
redis.call('HSET', KEYS[1], 'tokens', tokens - 1, 'updated', now)
redis.call('EXPIRE', KEYS[1], 120)
redis.call('INCR', KEYS[2])
redis.call('EXPIRE', KEYS[2], ARGV[4])
return 0
"""


async def enforce_limit(redis: Redis, prefix: str, subject: str, per_minute: int, per_day: int) -> None:
    now = time.time()
    day = int(now // 86400)
    seconds_left = math.ceil((day + 1) * 86400 - now)
    wait = await redis.execute_command(
        "EVAL",
        TOKEN_BUCKET,
        2,
        f"{prefix}rate:{subject}",
        f"{prefix}day:{subject}:{day}",
        now,
        per_minute,
        per_day,
        seconds_left,
    )
    if wait:
        raise AppError(429, 3001, "超过调用频率或日配额限制", {"Retry-After": str(wait)})
