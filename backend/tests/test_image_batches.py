import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from sqlmodel import SQLModel, Session, select
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'image-worker'))
from app.database import Game, ImageBatch, ImageJob, OwnedGame, User, engine
from image_worker.batch import create_batch, batch_status, cancel_batch


class ImageBatchTests(unittest.TestCase):
    def setUp(self):
        self.assertEqual(engine.url.database, 'steam_test')
        SQLModel.metadata.drop_all(engine)
        SQLModel.metadata.create_all(engine)
        with Session(engine) as session:
            user = User(username='owner', hashed_password='unused')
            session.add(user)
            session.flush()
            self.uid = user.id
            self.ids = []
            for i in range(3):
                game = Game(steam_app_id=100+i, cover_url='https://example.test/a.jpg' if i != 2 else None,
                            cover_s3_key='covers/existing.jpg' if i == 1 else None)
                session.add(game)
                session.flush()
                self.ids.append(game.id)
                session.add(OwnedGame(user_id=user.id, game_id=game.id))
            session.commit()

    def test_snapshot_skips_and_future_import_does_not_expand(self):
        result = create_batch(self.uid)
        self.assertEqual((result['total'], result['remaining'], result['counts']['skipped']), (3, 1, 2))
        with Session(engine) as session:
            game = session.get(Game, self.ids[0])
            game.cover_url = 'https://example.test/changed.jpg'
            session.add(game)
            new = Game(steam_app_id=999)
            session.add(new)
            session.flush()
            session.add(OwnedGame(user_id=self.uid, game_id=new.id))
            session.commit()
            job = session.get(ImageJob, (result['batch_id'], self.ids[0]))
            self.assertEqual(job.source_url, 'https://example.test/a.jpg')
        self.assertEqual(batch_status(result['batch_id'])['total'], 3)

    def test_scope_and_ownership_rejected_without_writes(self):
        for kwargs in ({'max_games': 2}, {'max_games': 116}, {'game_ids': [999]}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                create_batch(self.uid, **kwargs)
        with Session(engine) as session:
            self.assertEqual(session.exec(select(ImageBatch)).all(), [])
            self.assertEqual(session.exec(select(ImageJob)).all(), [])

    def test_explicit_single_game_and_cancel(self):
        result = create_batch(self.uid, max_games=1, game_ids=[self.ids[0]])
        self.assertEqual(result['total'], 1)
        with self.assertRaises(ValueError):
            create_batch(self.uid)
        self.assertEqual(cancel_batch(result['batch_id'])['remaining'], 0)
        self.assertEqual(cancel_batch(result['batch_id'])['status'], 'cancelled')
        self.assertEqual(create_batch(self.uid)['total'], 3)

    def test_concurrent_creation_serialized(self):
        def run(_):
            try:
                return create_batch(self.uid)['batch_id']
            except ValueError:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(value is not None for value in pool.map(run, range(2))), 1)

    def test_job_failure_rolls_back_whole_batch(self):
        with patch('image_worker.batch.ImageJob', side_effect=RuntimeError('failure')):
            with self.assertRaises(RuntimeError):
                create_batch(self.uid)
        with Session(engine) as session:
            self.assertEqual(session.exec(select(ImageBatch)).all(), [])

    def test_all_skipped_finishes_immediately(self):
        result = create_batch(self.uid, game_ids=[self.ids[1], self.ids[2]])
        self.assertEqual((result['status'], result['remaining']), ('completed', 0))
