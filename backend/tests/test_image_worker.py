import hashlib
import os
import sys
import unittest
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch
from concurrent.futures import ThreadPoolExecutor

import httpx
from PIL import Image
from botocore.exceptions import ClientError
from botocore.stub import Stubber
from botocore.config import Config
import boto3
from sqlmodel import SQLModel, Session, select
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'image-worker'))
from app.database import Game, ImageJob, User, OwnedGame, engine
from image_worker.batch import create_batch, cancel_batch, batch_status
from image_worker import jobs, tasks
from image_worker.storage import store_cover
from image_worker.download import download_cover


class ImageWorkerTests(unittest.TestCase):
    def setUp(self):
        self.assertEqual(engine.url.database, 'steam_test')
        SQLModel.metadata.drop_all(engine)
        SQLModel.metadata.create_all(engine)
        self.env = patch.dict(os.environ, S3_UPLOADS_ENABLED='true', S3_BUCKET='fake-bucket')
        self.env.start()
        self.addCleanup(self.env.stop)
        with Session(engine) as session:
            user = User(username='image-owner', hashed_password='unused')
            game = Game(steam_app_id=620, cover_url='https://cdn.akamai.steamstatic.com/steam/apps/620/header.jpg')
            session.add(user)
            session.add(game)
            session.flush()
            self.uid, self.gid = user.id, game.id
            session.add(OwnedGame(user_id=user.id, game_id=game.id))
            session.commit()
        self.bid = create_batch(self.uid)['batch_id']

    def queue(self):
        publisher = MagicMock()
        jobs.dispatch(publisher)
        return publisher.send_task.call_args.kwargs['args']

    def test_success_duplicate_and_no_network_when_disabled(self):
        args = self.queue()
        with patch.object(tasks, 'download_cover', return_value=b'jpeg') as download, \
             patch.object(tasks, 'create_client') as create, patch.object(tasks, 'store_cover', return_value='covers/test.jpg'):
            self.assertEqual(tasks.upload(*args), 'completed')
            self.assertEqual(tasks.upload(*args), 'stale')
            download.assert_called_once()
            create.return_value.close.assert_called_once()
        with Session(engine) as session:
            self.assertEqual(session.get(Game, self.gid).cover_s3_key, 'covers/test.jpg')
        self.assertEqual(batch_status(self.bid)['status'], 'completed')
        with patch.dict(os.environ, S3_UPLOADS_ENABLED='false'), patch.object(tasks, 'create_client') as create:
            self.assertEqual(tasks.upload(*args), 'disabled')
            self.assertEqual(jobs.dispatch(MagicMock()), 0)
            create.assert_not_called()

    def test_retry_budget_and_timeout_recovery(self):
        for attempt in range(3):
            with Session(engine) as session:
                job = session.get(ImageJob, (self.bid, self.gid))
                job.next_attempt_at = None
                session.add(job)
                session.commit()
            args = self.queue()
            with patch.object(tasks, 'download_cover', side_effect=httpx.ReadTimeout('timeout')):
                self.assertEqual(tasks.upload(*args), 'failed')
            with Session(engine) as session:
                job = session.get(ImageJob, (self.bid, self.gid))
                self.assertEqual(job.attempt_count, attempt + 1)
                self.assertEqual(job.status, 'ready' if attempt < 2 else 'failed')
        self.assertEqual(batch_status(self.bid)['remaining'], 0)

    def test_publication_failure_recovery_and_concurrent_dispatch(self):
        publisher = MagicMock()
        publisher.connection_for_write.side_effect = RuntimeError('offline')
        with self.assertRaises(RuntimeError):
            jobs.dispatch(publisher)
        with Session(engine) as session:
            job = session.get(ImageJob, (self.bid, self.gid))
            token = job.attempt_token
            job.last_published_at = datetime.now(UTC) - timedelta(minutes=6)
            session.add(job)
            session.commit()
        publisher = MagicMock()
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(lambda _: jobs.dispatch(publisher), range(2))), 1)
        self.assertEqual(publisher.send_task.call_args.kwargs['args'][2], token)

    def test_cancel_after_download_fences_aws(self):
        args = self.queue()
        def downloaded(_):
            cancel_batch(self.bid)
            return b'jpeg'
        with patch.object(tasks, 'download_cover', side_effect=downloaded), patch.object(tasks, 'create_client') as create:
            self.assertEqual(tasks.upload(*args), 'stale')
            create.assert_not_called()

    def test_stale_after_upload_does_not_save_key(self):
        args = self.queue()
        def stored(*_):
            cancel_batch(self.bid)
            return 'covers/orphan.jpg'
        with patch.object(tasks, 'download_cover', return_value=b'jpeg'), patch.object(tasks, 'create_client'), \
             patch.object(tasks, 'store_cover', side_effect=stored):
            self.assertEqual(tasks.upload(*args), 'stale')
        with Session(engine) as session:
            self.assertIsNone(session.get(Game, self.gid).cover_s3_key)

    def test_source_validation_and_size(self):
        for url in ('http://cdn.akamai.steamstatic.com/x', 'https://127.0.0.1/x', 'https://evil.test/x'):
            with self.assertRaises(ValueError):
                download_cover(url)
        image = BytesIO()
        Image.new('RGB', (8, 8)).save(image, format='PNG')
        with patch('image_worker.download.httpx.stream') as stream:
            response = stream.return_value.__enter__.return_value
            response.iter_bytes.return_value = [image.getvalue()]
            self.assertTrue(download_cover('https://cdn.akamai.steamstatic.com/x').startswith(b'\xff\xd8'))
            response.iter_bytes.return_value = [b'x' * (2*1024*1024+1)]
            with self.assertRaises(ValueError):
                download_cover('https://cdn.akamai.steamstatic.com/x')

    def test_conditional_upload_recovers_existing_object(self):
        client = MagicMock()
        client.exceptions.ClientError = ClientError
        client.put_object.side_effect = ClientError({'Error': {'Code': 'PreconditionFailed'},
            'ResponseMetadata': {'HTTPStatusCode': 412}}, 'PutObject')
        source = 'https://example.test/a'
        client.head_object.return_value = {'Metadata': {'source-sha256': hashlib.sha256(source.encode()).hexdigest()}}
        key = store_cover(client, 'bucket', 1, source, b'jpeg')
        self.assertTrue(key.startswith('covers/steam/1/'))
        self.assertEqual(client.put_object.call_args.kwargs['IfNoneMatch'], '*')
        client.head_object.return_value = {'Metadata': {}}
        with self.assertRaises(ValueError):
            store_cover(client, 'bucket', 1, source, b'jpeg')

    def test_real_sdk_upload_contract_without_aws(self):
        # Explicit fake credentials plus Stubber prevent credential/network access.
        client = boto3.client('s3', region_name='us-east-2',
            aws_access_key_id='testing', aws_secret_access_key='testing',
            config=Config(signature_version='s3v4'))
        source = 'https://example.test/source.jpg'
        digest = hashlib.sha256(source.encode()).hexdigest()
        key = f'covers/steam/1/{digest}.jpg'
        with Stubber(client) as stub:
            stub.add_response('put_object', {}, dict(Bucket='bucket', Key=key,
                Body=b'jpeg', ContentType='image/jpeg', ServerSideEncryption='AES256',
                IfNoneMatch='*', Metadata={'source-sha256': digest}))
            self.assertEqual(store_cover(client, 'bucket', 1, source, b'jpeg'), key)
            stub.assert_no_pending_responses()
        client.close()

    def test_expired_running_attempt_is_retried_and_old_token_fenced(self):
        args = self.queue()
        with Session(engine) as session:
            job = session.get(ImageJob, (self.bid, self.gid))
            job.status = 'running'
            job.attempt_count = 1
            job.updated_at = datetime.now(UTC) - timedelta(minutes=3)
            session.add(job)
            session.commit()
        jobs.dispatch(MagicMock())
        with Session(engine) as session:
            job = session.get(ImageJob, (self.bid, self.gid))
            self.assertEqual(job.status, 'ready')
            self.assertIsNone(job.attempt_token)
        self.assertEqual(tasks.upload(*args), 'stale')
