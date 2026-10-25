# Optional private S3 cover display

`app/covers.py:resolve_cover` returns cover_url and cover_fallback_url. Default
behavior uses the saved Steam source for both. With S3_COVERS_ENABLED=true, a
configured bucket and a covers/ key, it locally signs a 15-minute GET URL. No
HEAD, GET, download, upload, or object existence check is made by the helper.
The SDK may obtain/refresh credentials, so use the explicit local static read
profile for this demo. Missing signing credentials fall back to Steam.

The library response builder serves library/status updates; Play Next consumes
the same library objects (its queue endpoint returns IDs). Recommendations carry
the saved key internally through pure ranking and resolve only returned results
at the API boundary, without extra database queries. Keys and signed URLs are
never persisted into cover_url or returned as separate internal-key fields.

`frontend/assets/covers.js` tries each distinct safe URL once: S3, Steam, then
the existing placeholder. It is used for library/current game cards, Play Next,
recommendations, and completion celebrations. Reloading API data renews links;
an expired lazy-loaded image falls back to Steam. Expiry does not delete objects
and anyone holding a valid link can view/download that object. Do not log URLs.

## Local demonstration

1. Keep S3_UPLOADS_ENABLED=false and the image worker/scheduler stopped.
2. Configure a separate IAM identity/profile named backlog-cover-read, with only
   s3:GetObject on arn:aws:s3:::backlog-beater-demo/covers/*.
   Console: IAM > Users > Create user `backlog-beater-cover-reader`, without
   console login or group memberships. Add an inline policy granting only the
   GetObject permission above; do not attach broad S3/admin policies.
   Create a Local code access key for this dedicated identity.
3. Place ONLY that profile's credentials/config in an isolated directory outside
   the repository. Do not mount your upload/admin profiles into API containers.
4. In .env set S3_COVERS_ENABLED=true and S3_READ_PROFILE_DIR to that directory
   (use a full Windows path, with forward slashes). Set region us-east-2 and
   bucket backlog-beater-demo. Never commit credentials.
5. Rebuild/start with the display override:

```powershell
docker compose -f compose.yml -f compose.s3-display.yml up -d --build --wait
docker compose -f compose.yml -f compose.s3-display.yml exec -T nginx nginx -s reload
```

The image profile is not enabled by this command. Image API credentials are read
only; no upload permission is needed to sign GET URLs. Existing migrations
012/013 must be applied before rebuilding against the updated Game model.

Log in and find Portal 2. In browser Network tools, its cover request should go
to the private bucket with X-Amz-* signing parameters. Other games without stored
keys stay on Steam. Verify Play Next only if you choose to queue it; recommendations
show it only when eligible. To test fallback, block its S3 URL in browser devtools
and reload: the Steam cover should load next. Never publish the signed URLs.

Set S3_COVERS_ENABLED=false and recreate the API containers to revert to Steam.
Stop using the display override to remove credential mounts. Credential profile
setup and live authenticated/browser acceptance are not performed by unit tests.

## Validation

Backend tests mock the signer and verify disabled/missing-key paths never create
an SDK client, credential failures fall back, and library/recommendation fields
agree without leaking the internal key. Frontend tests simulate image errors and
ensure one fallback and no retry loops. No AWS credentials/calls are needed.
