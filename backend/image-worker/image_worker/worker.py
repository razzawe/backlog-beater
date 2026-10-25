import os
from celery import Celery
from kombu import Queue
from celery.signals import worker_process_init

app = Celery('images', broker=os.getenv('IMAGE_BROKER_URL', 'redis://enrichment-redis:6379/0'))
app.conf.update(imports=('image_worker.tasks',), task_queues=(Queue('images'),),
    task_default_queue='images', task_create_missing_queues=False,
    task_serializer='json', accept_content=['json'], task_ignore_result=True,
    worker_prefetch_multiplier=1, broker_connection_retry_on_startup=True,
    broker_transport_options={'socket_timeout': 2, 'socket_connect_timeout': 2},
    beat_schedule={'image-maintenance': {'task': 'images.maintain', 'schedule': 15.0,
        'options': {'expires': 15, 'queue': 'images'}}}, timezone='UTC', enable_utc=True)


@worker_process_init.connect
def reset_pool(**kwargs):
    from app.database import engine
    engine.dispose(close=False)
