"""Shared 24-hour review snapshots and token-owned refresh leases in Redis."""

import json
import os
from dataclasses import asdict
from secrets import token_urlsafe

from redis import Redis

TTL_SECONDS = 86400
PENDING_SECONDS = 900
RUNNING_SECONDS = 60
client = Redis.from_url(os.getenv("HLTB_REVIEW_CACHE_URL", "redis://enrichment-cache:6379/0"),
                        decode_responses=True, socket_timeout=2, socket_connect_timeout=2)


def key(game_id):
    return f"hltb:review:v1:{game_id}"


def lease_key(game_id):
    return f"hltb:review-refresh:v1:{game_id}"


def get_review(game_id):
    raw = client.get(key(game_id))
    if raw is None:
        return None
    try:
        value = json.loads(raw)
        if (not isinstance(value, dict) or not isinstance(value.get("version"), str)
                or not isinstance(value.get("candidates"), list)
                or len(value["candidates"]) > 3
                or value.get("status") not in {"matched", "ambiguous", "no_match", "lookup_failed"}
                or any(not isinstance(candidate, dict) or type(candidate.get("hltb_id")) is not int
                       or candidate["hltb_id"] <= 0 for candidate in value["candidates"])):
            return None
        return value
    except (ValueError, TypeError):
        return None


def claim_refresh(game_id):
    token = token_urlsafe(24)
    return token if client.set(lease_key(game_id), token, nx=True, ex=PENDING_SECONDS) else None


def begin_refresh(game_id, token):
    # Only the pending owner can begin. Duplicate deliveries cannot renew a running lease.
    return bool(client.eval("""
        if redis.call('GET', KEYS[1]) == ARGV[1] then
            redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3]); return 1
        end
        return 0
    """, 1, lease_key(game_id), token, "running:" + token, RUNNING_SECONDS))


def release_refresh(game_id, token):
    client.eval("""
        local value = redis.call('GET', KEYS[1])
        if value == ARGV[1] or value == ARGV[2] then return redis.call('DEL', KEYS[1]) end
        return 0
    """, 1, lease_key(game_id), token, "running:" + token)


def put_review(game_id, token, *, result, last_success_at):
    # Only actionable suggestions belong in the review cache. Remove an older
    # shortlist when a new lookup resolves the game or finds no candidates.
    if result.status.value != "ambiguous" or not result.candidates:
        cleared = client.eval("""
            if redis.call('GET', KEYS[1]) == ARGV[1] then
                redis.call('DEL', KEYS[2]); return 1
            end
            return 0
        """, 2, lease_key(game_id), key(game_id), "running:" + token)
        if not cleared:
            raise ValueError("Review refresh lease expired")
        return
    value = {"version": token_urlsafe(24), "status": result.status.value,
             "reason": result.reason, "last_success_at": last_success_at.isoformat(),
             "candidates": [asdict(candidate) for candidate in result.candidates[:3]]}
    # An expired worker cannot overwrite the shortlist from a newer refresh.
    saved = client.eval("""
        if redis.call('GET', KEYS[1]) == ARGV[1] then
            redis.call('SET', KEYS[2], ARGV[2], 'EX', ARGV[3]); return 1
        end
        return 0
    """, 2, lease_key(game_id), key(game_id), "running:" + token,
        json.dumps(value), TTL_SECONDS)
    if not saved:
        raise ValueError("Review refresh lease expired")


def put_failure(game_id, token):
    # Brief failure caching prevents every modal poll from starting another search.
    saved = client.eval("""
        if redis.call('GET', KEYS[1]) == ARGV[1] then
            redis.call('SET', KEYS[2], ARGV[2], 'EX', 60); return 1
        end
        return 0
    """, 2, lease_key(game_id), key(game_id), "running:" + token,
        json.dumps({"version": token_urlsafe(24), "status": "lookup_failed", "candidates": []}))

    if not saved:
        raise ValueError("Review refresh lease expired")
