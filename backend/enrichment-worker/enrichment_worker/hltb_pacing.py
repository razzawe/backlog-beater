"""HLTB start spacing and provider cooldown for the single-slot worker."""
import os
import time

from redis import Redis
from redis.exceptions import RedisError

client = Redis.from_url(os.getenv('ENRICHMENT_BROKER_URL', 'redis://enrichment-redis:6379/0'),
    socket_timeout=2, socket_connect_timeout=2)
SPACING = 'hltb:global:spacing:v1'
COOLDOWN = 'hltb:global:cooldown:v1'


def wait_for_slot():
    """Wait locally for short start spacing; return long cooldown deferral.

    A single enrichment worker with concurrency one serializes lookups. There
    is no active-lookup lease and no retry loop for worker availability.
    Long provider cooldowns use countdown rather than exceeding task time limits.
    """
    try:
        while True:
            wait = client.eval('''
                local cooldown = redis.call('PTTL',KEYS[2])
                if cooldown > 0 then return cooldown end
                local spacing = redis.call('PTTL',KEYS[1])
                if spacing > 0 then return spacing end
                redis.call('SET',KEYS[1],'1','PX',3000)
                return 0
            ''', 2, SPACING, COOLDOWN)
            if wait <= 0:
                return 0
            if wait > 3000:
                return wait / 1000
            time.sleep(wait / 1000)
    except RedisError:
        return 10  # Fail closed; defer infrastructure failures, not busy lookups.


def cooldown(seconds):
    client.eval('''
        if redis.call('PTTL',KEYS[1]) < tonumber(ARGV[1]) then
            redis.call('SET',KEYS[1],'1','PX',ARGV[1])
        end
    ''', 1, COOLDOWN, max(1, int(seconds * 1000)))
