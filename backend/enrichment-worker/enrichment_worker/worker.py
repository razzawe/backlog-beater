"""Celery application for the dedicated enrichment queue."""

import os

from celery import Celery
from kombu import Queue

QUEUE_NAME = "enrichment"
app = Celery(
    "enrichment",
    broker=os.environ.get("ENRICHMENT_BROKER_URL", "redis://enrichment-redis:6379/0"),
)
app.conf.update(
    imports=("enrichment_worker.tasks",),
    broker_transport_options={"socket_timeout": 2, "socket_connect_timeout": 2},
    task_queues=(Queue(QUEUE_NAME, routing_key=QUEUE_NAME),),
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
