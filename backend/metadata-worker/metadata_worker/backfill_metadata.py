"""Explicit bounded metadata backfill; never resets failed/completed jobs."""
import argparse

from sqlmodel import Session, select
from app.database import Game, GameMetadata, GameMetadataJob, engine
from . import metadata_jobs
from .worker import app


def backfill(*, limit=100, after_id=0):
    if type(limit) is not int or not 1 <= limit <= 1000 or type(after_id) is not int or after_id < 0:
        raise ValueError("limit must be 1..1000 and after_id nonnegative")
    with Session(engine) as session:
        ids = session.exec(select(Game.id).outerjoin(GameMetadata,
            GameMetadata.game_id == Game.id).outerjoin(GameMetadataJob,
            GameMetadataJob.game_id == Game.id).where(Game.id > after_id,
            GameMetadata.last_success_at.is_(None), GameMetadataJob.game_id.is_(None))
            .order_by(Game.id).limit(limit)).all()
        metadata_jobs.create_ready(session, ids)
        session.commit()
    # Maintenance handles subsequent batches when limit exceeds dispatch's 100.
    published = metadata_jobs.dispatch(app, ids)
    return {"created": len(ids), "published": published,
            "last_game_id": ids[-1] if ids else after_id}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--after-id", type=int, default=0)
    args = parser.parse_args()
    print(backfill(limit=args.limit, after_id=args.after_id))
