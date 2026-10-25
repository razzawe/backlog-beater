"""Durable dispatch, bounded retries, and batch/attempt fencing."""
import os
from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe
from sqlmodel import Session, select
from app.database import ImageBatch, ImageJob, engine


def enabled():
    return os.getenv('S3_UPLOADS_ENABLED', 'false').lower() == 'true'


def finish_batch(session, batch):
    session.flush()
    remaining = session.exec(select(ImageJob.game_id).where(ImageJob.batch_id == batch.id,
        ImageJob.status.in_(['ready', 'pending', 'running'])).limit(1)).first()
    if remaining is None and batch.status != 'cancelled':
        batch.status = 'completed'
        batch.finished_at = datetime.now(UTC)
        session.add(batch)


def fail(job, code, retry):
    job.last_error = code
    job.updated_at = datetime.now(UTC)
    job.attempt_token = None
    job.status = 'ready' if retry and job.attempt_count < 3 else 'failed'
    job.next_attempt_at = (job.updated_at + timedelta(seconds=60 if job.attempt_count == 1 else 300)
                          if job.status == 'ready' else None)


def dispatch(publisher):
    if not enabled():
        return 0
    now = datetime.now(UTC)
    messages = []
    with Session(engine) as session:
        batches = session.exec(select(ImageBatch).where(ImageBatch.status.in_(['ready','running']))
            .order_by(ImageBatch.id).limit(100).with_for_update(skip_locked=True)).all()
        for batch in batches:
            rows = session.exec(select(ImageJob).where(ImageJob.batch_id == batch.id)
                .order_by(ImageJob.game_id).with_for_update()).all()
            for job in rows:
                if job.status == 'running' and job.updated_at < now - timedelta(seconds=120):
                    fail(job, 'worker_timeout', True)
                due = job.next_attempt_at is None or job.next_attempt_at <= now
                republish = (job.status == 'pending' and (job.last_published_at is None
                    or job.last_published_at <= now - timedelta(seconds=300)))
                if len(messages) < 100 and due and (job.status == 'ready' or republish):
                    if job.status == 'ready':
                        job.status = 'pending'
                        job.attempt_token = token_urlsafe(24)
                    job.last_published_at = now
                    job.updated_at = now
                    messages.append((batch.id, job.game_id, job.attempt_token))
                session.add(job)
            finish_batch(session, batch)
        session.commit()
    if messages:
        with publisher.connection_for_write(connect_timeout=2) as connection:
            connection.ensure_connection(max_retries=0)
            with publisher.amqp.Producer(connection) as producer:
                for batch_id, game_id, token in messages:
                    publisher.send_task('images.upload', args=[batch_id, game_id, token],
                        queue='images', producer=producer, retry=False)
    return len(messages)
