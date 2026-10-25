"""Database-owned attempts. Pending rows are durable dispatch intent."""
from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe

from sqlalchemy import or_, update
from sqlalchemy.dialects.postgresql import insert
from sqlmodel import Session, select

from app.database import Game, GameMetadata, GameMetadataJob, JobStatus, engine

BATCH_SIZE = 100
REPUBLISH_SECONDS = 300
RUNNING_SECONDS = 120  # Above the task's 35-second hard limit.


def create_ready(session, game_ids):
    # Skip successfully collected metadata forever; imports do not reset jobs.
    eligible = session.exec(select(Game.id).outerjoin(GameMetadata,
        GameMetadata.game_id == Game.id).where(Game.id.in_(game_ids),
        GameMetadata.last_success_at.is_(None))).all()
    for game_id in eligible:
        session.execute(insert(GameMetadataJob).values(game_id=game_id,
            status=JobStatus.READY, updated_at=datetime.now(UTC))
            .on_conflict_do_nothing(index_elements=["game_id"]))



def claim_running(game_id, token):
    with Session(engine) as session:
        claimed = session.execute(update(GameMetadataJob).where(
            GameMetadataJob.game_id == game_id, GameMetadataJob.status == JobStatus.PENDING,
            GameMetadataJob.attempt_token == token).values(
                status=JobStatus.RUNNING, updated_at=datetime.now(UTC))
            .returning(GameMetadataJob.game_id)).scalar_one_or_none()
        session.commit()
        return claimed is not None


def lock_attempt(session, game_id, token):
    return session.exec(select(GameMetadataJob).where(
        GameMetadataJob.game_id == game_id, GameMetadataJob.status == JobStatus.RUNNING,
        GameMetadataJob.attempt_token == token).with_for_update()).first()


def finish(job, *, failed=False, error=None):
    job.status = JobStatus.FAILED if failed else JobStatus.COMPLETED
    job.last_error = error
    job.updated_at = datetime.now(UTC)


def fail_attempt(game_id, token, error):
    with Session(engine) as session:
        job = lock_attempt(session, game_id, token)
        if job:
            finish(job, failed=True, error=error)
            session.add(job)
        session.commit()


def retry_failed(game_id):
    """Explicit retry; imports never reset failed jobs."""
    with Session(engine) as session:
        result = session.execute(update(GameMetadataJob).where(
            GameMetadataJob.game_id == game_id, GameMetadataJob.status == JobStatus.FAILED)
            .values(status=JobStatus.READY, attempt_token=None, last_error=None,
                last_published_at=None, updated_at=datetime.now(UTC)))
        session.commit()
        return result.rowcount == 1


def dispatch(publisher, game_ids=None):
    """Reserve a bounded publication batch, commit, then contact the broker.

    Reserving before publication throttles failures and concurrent dispatchers.
    A crash is recoverable after the cooldown; pending tokens never expire.
    """
    now = datetime.now(UTC)
    with Session(engine) as session:
        session.execute(update(GameMetadataJob).where(
            GameMetadataJob.status == JobStatus.RUNNING,
            GameMetadataJob.updated_at < now - timedelta(seconds=RUNNING_SECONDS))
            .values(status=JobStatus.FAILED, last_error="worker_timeout", updated_at=now))
        query = select(GameMetadataJob).where(or_(
            GameMetadataJob.status == JobStatus.READY,
            (GameMetadataJob.status == JobStatus.PENDING) & or_(
                GameMetadataJob.last_published_at.is_(None),
                GameMetadataJob.last_published_at < now - timedelta(seconds=REPUBLISH_SECONDS))))
        if game_ids is not None:
            query = query.where(GameMetadataJob.game_id.in_(game_ids))
        rows = session.exec(query.order_by(GameMetadataJob.updated_at, GameMetadataJob.game_id)
            .limit(BATCH_SIZE).with_for_update(skip_locked=True)).all()
        messages = []
        for job in rows:
            if job.status == JobStatus.READY:
                job.status = JobStatus.PENDING
                job.attempt_token = token_urlsafe(24)
                job.updated_at = now
            job.last_published_at = now
            session.add(job)
            messages.append((job.game_id, job.attempt_token))
        session.commit()
    if not messages:
        return 0
    with publisher.connection_for_write(connect_timeout=2) as connection:
        connection.ensure_connection(max_retries=0)
        with publisher.amqp.Producer(connection) as producer:
            for game_id, token in messages:
                publisher.send_task("catalog.collect_metadata", args=[game_id, token],
                    queue="steam_metadata", producer=producer, retry=False)
    return len(messages)
