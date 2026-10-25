"""Shared request-start pacing across worker processes, using the broker Redis."""
import os
import time

from redis import Redis
from redis.exceptions import RedisError

client = Redis.from_url(os.getenv("ENRICHMENT_BROKER_URL", "redis://enrichment-redis:6379/0"),
    socket_timeout=2, socket_connect_timeout=2)
KEY = "catalog:steam-metadata:request-slot:v1"


class SteamPacingError(Exception):
    pass


def wait_for_slot():
    # One request start per second across all processes. Fail closed if Redis
    # is unavailable; never bypass provider pacing. No DB transaction is held.
    deadline = time.monotonic() + 5
    try:
        while time.monotonic() < deadline:
            if client.set(KEY, "1", nx=True, px=1000):
                return
            time.sleep(0.1)
    except RedisError as error:
        raise SteamPacingError("pacing_unavailable") from error
    raise SteamPacingError("pacing_timeout")
