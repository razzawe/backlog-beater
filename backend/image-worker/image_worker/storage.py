"""Small conditional S3 writes; immutable source-based keys support recovery."""
import hashlib
import os
import boto3
from botocore.config import Config


def store_cover(client, bucket, game_id, source, content):
    source_hash = hashlib.sha256(source.encode()).hexdigest()
    key = f'covers/steam/{game_id}/{source_hash}.jpg'
    try:
        client.put_object(Bucket=bucket, Key=key, Body=content,
            ContentType='image/jpeg', ServerSideEncryption='AES256', IfNoneMatch='*',
            Metadata={'source-sha256': source_hash})
    except client.exceptions.ClientError as error:
        if error.response['ResponseMetadata']['HTTPStatusCode'] != 412:
            raise
        existing = client.head_object(Bucket=bucket, Key=key)
        if existing.get('Metadata', {}).get('source-sha256') != source_hash:
            raise ValueError('object_source_conflict') from error
    return key


def create_client():
    region = os.environ.get('AWS_REGION', 'us-east-2')
    return boto3.Session(region_name=region).client('s3',
        endpoint_url=f'https://s3.{region}.amazonaws.com', config=Config(
            signature_version='s3v4', connect_timeout=5, read_timeout=10,
            retries={'mode': 'standard', 'total_max_attempts': 2}))
