"""API-side publisher; imports no worker implementation."""

import os

from celery import Celery

from .review_cache import PENDING_SECONDS, claim_refresh, release_refresh

publisher = Celery("review-dispatch", broker=os.getenv(
    "ENRICHMENT_BROKER_URL", "redis://enrichment-redis:6379/0"))
publisher.conf.update(task_serializer="json", task_ignore_result=True,
    broker_transport_options={"socket_timeout": 2, "socket_connect_timeout": 2})


def request_refresh(game_id):
    token = claim_refresh(game_id)
    if token is None:
        return
    try:
        with publisher.connection_for_write(connect_timeout=2) as connection:
            connection.ensure_connection(max_retries=0)
            with publisher.amqp.Producer(connection) as producer:
                publisher.send_task("enrichment.refresh_review", args=[game_id, token],
                    queue="enrichment", producer=producer, retry=False, expires=PENDING_SECONDS)
    except Exception:
        release_refresh(game_id, token)
        raise
