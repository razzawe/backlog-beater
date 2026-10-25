"""Create and inspect durable image batches. No broker or AWS calls."""
import argparse
import json
import sys
from datetime import UTC, datetime

from sqlalchemy import func
from sqlmodel import Session, select
from app.database import Game, ImageBatch, ImageJob, OwnedGame, User, engine

STATUSES = ('ready', 'pending', 'running', 'completed', 'skipped', 'failed', 'cancelled')


def create_batch(user_id, *, max_games=115, game_ids=None):
    if type(user_id) is not int or user_id <= 0:
        raise ValueError('user_id must be positive')
    if type(max_games) is not int or not 1 <= max_games <= 115:
        raise ValueError('max_games must be 1..115')
    if game_ids is not None and (not game_ids or any(type(i) is not int or i <= 0 for i in game_ids)):
        raise ValueError('game IDs must be positive')
    with Session(engine) as session:
        # Serialize batch creation per owner; never create overlapping active batches.
        user = session.exec(select(User).where(User.id == user_id).with_for_update()).first()
        if user is None:
            raise ValueError('User not found')
        active = session.exec(select(ImageBatch.id).where(ImageBatch.user_id == user_id,
            ImageBatch.status.in_(['ready', 'running']))).first()
        if active:
            raise ValueError(f'An active batch already exists: {active}. Inspect or cancel it first.')
        query = select(Game).join(OwnedGame, OwnedGame.game_id == Game.id).where(OwnedGame.user_id == user_id)
        if game_ids is not None:
            query = query.where(Game.id.in_(set(game_ids)))
        games = session.exec(query.order_by(Game.id).limit(max_games + 1)).all()
        if len(games) > max_games:
            raise ValueError('Library exceeds max_games; no batch created. Use explicit --game-id selection.')
        if game_ids is not None and {g.id for g in games} != set(game_ids):
            raise ValueError('Every selected game must be owned by this user')
        if not games:
            raise ValueError('No owned games to process')
        batch = ImageBatch(user_id=user_id, max_games=max_games)
        session.add(batch)
        session.flush()
        ready = 0
        for game in games:
            reason = 'already_stored' if game.cover_s3_key else 'missing_source' if not game.cover_url else None
            status = 'skipped' if reason else 'ready'
            ready += status == 'ready'
            session.add(ImageJob(batch_id=batch.id, game_id=game.id,
                source_url=game.cover_url, status=status, last_error=reason))
        if not ready:
            batch.status = 'completed'
            batch.finished_at = datetime.now(UTC)
        batch_id = batch.id
        session.commit()
    return batch_status(batch_id)


def batch_status(batch_id):
    with Session(engine) as session:
        batch = session.get(ImageBatch, batch_id)
        if batch is None:
            raise ValueError('Batch not found')
        counts = dict.fromkeys(STATUSES, 0)
        counts.update(dict(session.exec(select(ImageJob.status, func.count())
            .where(ImageJob.batch_id == batch_id).group_by(ImageJob.status)).all()))
        return dict(batch_id=batch.id, user_id=batch.user_id, status=batch.status,
            total=sum(counts.values()), remaining=sum(counts[s] for s in ('ready', 'pending', 'running')),
            counts=counts)


def cancel_batch(batch_id):
    with Session(engine) as session:
        batch = session.exec(select(ImageBatch).where(ImageBatch.id == batch_id).with_for_update()).first()
        if batch is None:
            raise ValueError('Batch not found')
        if batch.status in ('ready', 'running'):
            now = datetime.now(UTC)
            jobs = session.exec(select(ImageJob).where(ImageJob.batch_id == batch_id,
                ImageJob.status.in_(['ready', 'pending', 'running'])).with_for_update()).all()
            for job in jobs:
                job.status = 'cancelled'
                job.attempt_token = None
                job.next_attempt_at = None
                job.updated_at = now
                session.add(job)
            batch.status = 'cancelled'
            batch.finished_at = now
            session.add(batch)
            session.commit()
    return batch_status(batch_id)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    create = commands.add_parser('create')
    create.add_argument('--user-id', required=True, type=int)
    create.add_argument('--max-games', type=int, default=115)
    create.add_argument('--game-id', type=int, action='append', help='Optional owned catalog ID; repeat to select a subset.')
    for command in ('status', 'cancel'):
        commands.add_parser(command).add_argument('--batch-id', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'create':
            result = create_batch(args.user_id, max_games=args.max_games, game_ids=args.game_id)
        elif args.command == 'cancel':
            result = cancel_batch(args.batch_id)
        else:
            result = batch_status(args.batch_id)
        print(json.dumps(result, indent=2))
        return 0
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
