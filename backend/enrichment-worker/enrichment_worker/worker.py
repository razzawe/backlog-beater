"""Celery application for the HLTB and Steam metadata queues."""

import os

from celery import Celery
from kombu import Queue

QUEUE_NAME = "enrichment"
METADATA_QUEUE_NAME = "steam_metadata"
app = Celery(
    "enrichment",
    broker=os.environ.get("ENRICHMENT_BROKER_URL", "redis://enrichment-redis:6379/0"),
)
app.conf.update(
    beat_schedule={"maintain-enrichment-jobs": {
        "task": "enrichment.maintain_jobs", "schedule": 60.0,
        "options": {"expires": 60},
    }, "maintain-metadata-jobs": {
        "task": "catalog.maintain_metadata_jobs", "schedule": 60.0,
        "options": {"expires": 60, "queue": METADATA_QUEUE_NAME},
    }},
    imports=("enrichment_worker.tasks",),
    broker_transport_options={"socket_timeout": 2, "socket_connect_timeout": 2},
    task_queues=(Queue(QUEUE_NAME, routing_key=QUEUE_NAME),
                 Queue(METADATA_QUEUE_NAME, routing_key=METADATA_QUEUE_NAME)),
    task_routes={
        "catalog.collect_metadata": {"queue": METADATA_QUEUE_NAME},
        "catalog.maintain_metadata_jobs": {"queue": METADATA_QUEUE_NAME},
    },
    task_default_queue=QUEUE_NAME,
    task_default_routing_key=QUEUE_NAME,
    task_create_missing_queues=False,
    task_serializer="json",
    accept_content=["json"],
    task_ignore_result=True,
    broker_connection_retry_on_startup=True,
    worker_prefetch_multiplier=1,
    timezone="UTC",
    enable_utc=True,
)


# Each prefork child owns its connection pool; never reuse parent DB sockets.
from celery.signals import worker_process_init


@worker_process_init.connect
def reset_database_pool(**kwargs):
    from app.database import engine
    engine.dispose(close=False)
