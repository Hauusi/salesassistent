from __future__ import annotations

from redis import Redis
from rq import Queue

from app.config import get_settings

settings = get_settings()

_redis_conn: Redis | None = None
_queue: Queue | None = None


def get_redis() -> Redis:
    global _redis_conn
    if _redis_conn is None:
        _redis_conn = Redis.from_url(settings.redis_url)
    return _redis_conn


def get_queue() -> Queue:
    global _queue
    if _queue is None:
        _queue = Queue("mail_processing", connection=get_redis())
    return _queue
