"""Publish catalog events without importing consumer implementation code."""

import logging
import os

from celery import Celery
from pydantic import BaseModel

logger = logging.getLogger(__name__)
client = Celery("sync-events", broker=os.environ.get(
    "CATALOG_BROKER_URL", "redis://enrichment-redis:6379/0"))
client.conf.update(
    task_serializer="json", accept_content=["json"], task_ignore_result=True,
    broker_transport_options={"socket_timeout": 2, "socket_connect_timeout": 2},
)


class ImportEventSubmission(BaseModel):
    submitted: bool = False
    unavailable: bool = False


def publish_games_imported(app_ids: list[int]) -> ImportEventSubmission:
    """Publish one v1 event after commit; broker failure never undoes the import."""
    if not app_ids:
        return ImportEventSubmission()
    try:
        with client.connection_for_write(connect_timeout=2) as connection:
            connection.ensure_connection(max_retries=0)
            with client.amqp.Producer(connection) as producer:
                client.send_task(
                    "catalog.games_imported",
                    kwargs={"schema_version": 1, "steam_app_ids": sorted(set(app_ids))},
                    queue="enrichment", producer=producer, retry=False,
                )
        return ImportEventSubmission(submitted=True)
    except Exception:
        logger.exception("Catalog event submission failed; Steam import remains saved")
        return ImportEventSubmission(unavailable=True)
