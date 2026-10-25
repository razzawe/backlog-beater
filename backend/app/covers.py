"""Resolve display links locally; never download or check objects in API calls."""
import logging
import os
from functools import lru_cache
import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def s3_client():
    region = os.getenv('AWS_REGION', 'us-east-2')
    return boto3.Session(region_name=region).client('s3',
        endpoint_url=f'https://s3.{region}.amazonaws.com',
        config=Config(signature_version='s3v4', connect_timeout=2, read_timeout=2,
                      retries={'mode': 'standard', 'total_max_attempts': 1}))


def resolve_cover(source_url, object_key):
    result = {'cover_url': source_url, 'cover_fallback_url': source_url}
    if os.getenv('S3_COVERS_ENABLED', 'false').lower() != 'true' or not object_key:
        return result
    bucket = os.getenv('S3_BUCKET')
    if not bucket or not object_key.startswith('covers/'):
        return result
    try:
        result['cover_url'] = s3_client().generate_presigned_url('get_object',
            Params={'Bucket': bucket, 'Key': object_key}, ExpiresIn=900)
    except (BotoCoreError, ClientError, OSError, ValueError):
        # No signed URLs or credential details in logs.
        logger.warning('Cover signing unavailable; using source cover')
    return result
