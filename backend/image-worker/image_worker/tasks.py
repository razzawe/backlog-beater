"""Image attempts never call AWS unless explicitly enabled."""
import logging
import os
from contextlib import closing
from datetime import UTC, datetime
import httpx
from botocore.exceptions import BotoCoreError, ClientError
from PIL import Image, UnidentifiedImageError
from sqlmodel import Session, select
from app.database import Game, ImageBatch, ImageJob, engine
from .worker import app
from . import jobs
from .download import download_cover
from .storage import create_client, store_cover


def lock_attempt(session, batch_id, game_id, token, status):
    batch = session.exec(select(ImageBatch).where(ImageBatch.id == batch_id).with_for_update()).first()
    if batch is None or batch.status not in ('ready', 'running'):
        return None, None
    job = session.exec(select(ImageJob).where(ImageJob.batch_id == batch_id,
        ImageJob.game_id == game_id).with_for_update()).first()
    if job is None or job.status != status or job.attempt_token != token:
        return batch, None
    return batch, job


@app.task(name='images.upload', soft_time_limit=80, time_limit=90)
def upload(batch_id, game_id, token):
    if not jobs.enabled():
        return 'disabled'
    with Session(engine) as session:
        batch, job = lock_attempt(session, batch_id, game_id, token, 'pending')
        if job is None:
            return 'stale'
        game = session.get(Game, game_id)
        if game.cover_s3_key or not job.source_url or game.cover_url != job.source_url:
            job.status = 'skipped'
            job.last_error = 'already_stored' if game.cover_s3_key else 'missing_or_changed_source'
            session.add(job)
            jobs.finish_batch(session, batch)
            session.commit()
            return 'skipped'
        source = job.source_url
        job.status = 'running'
        job.attempt_count += 1
        job.updated_at = datetime.now(UTC)
        batch.status = 'running'
        session.add(job)
        session.add(batch)
        session.commit()
    try:
        content = download_cover(source)
        # Recheck cancellation/state after download, before starting AWS I/O.
        with Session(engine) as session:
            _, current = lock_attempt(session, batch_id, game_id, token, 'running')
            if current is None or not jobs.enabled():
                return 'stale'
        bucket = os.environ.get('S3_BUCKET')
        if not bucket:
            raise ValueError('missing_bucket')
        with closing(create_client()) as client:
            key = store_cover(client, bucket, game_id, source, content)
        with Session(engine) as session:
            batch, job = lock_attempt(session, batch_id, game_id, token, 'running')
            if job is None:
                return 'stale'
            game = session.exec(select(Game).where(Game.id == game_id).with_for_update()).one()
            if game.cover_url != source:
                job.status = 'skipped'
                job.last_error = 'changed_source'
            else:
                game.cover_s3_key = key
                job.status = 'completed'
                job.last_error = None
                session.add(game)
            job.updated_at = datetime.now(UTC)
            session.add(job)
            jobs.finish_batch(session, batch)
            outcome = job.status
            session.commit()
        return outcome
    except Exception as error:
        retry = isinstance(error, (httpx.TransportError, BotoCoreError))
        code = 'task_error'
        if isinstance(error, httpx.HTTPStatusError):
            retry = error.response.status_code in (408, 429, 500, 502, 503, 504)
            code = 'source_http_error'
        elif isinstance(error, ClientError):
            retry = error.response['ResponseMetadata']['HTTPStatusCode'] in (408,409,429,500,502,503,504)
            code = 's3_error'
        elif isinstance(error, (ValueError, UnidentifiedImageError, Image.DecompressionBombError)):
            code = 'invalid_source_or_image'
        elif retry:
            code = 'network_error'
        else:
            logging.getLogger(__name__).exception('Image task failed batch=%s game=%s', batch_id, game_id)
        with Session(engine) as session:
            batch, job = lock_attempt(session, batch_id, game_id, token, 'running')
            if job is not None:
                jobs.fail(job, code, retry)
                session.add(job)
                jobs.finish_batch(session, batch)
                session.commit()
        return 'failed'


@app.task(name='images.maintain')
def maintain():
    return jobs.dispatch(app)
