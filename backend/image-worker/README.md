# Image batch commands

The CLI creates/inspects/cancels fixed snapshots only. The image worker now
dispatches those saved jobs through the private images queue. Its Compose
services are opt-in and uploads default to disabled. No public endpoints are
added. App cover display remains Steam-only until the next implementation step.

Apply migrations 012 and 013 to the target database before using the updated
models. Use an environment with backend/pyproject.toml dependencies installed,
set DATABASE_URL for that database, and from the repository root in PowerShell:

```powershell
$env:PYTHONPATH = "$PWD/backend;$PWD/backend/image-worker"
python -m image_worker.batch create --user-id 1 --max-games 115
python -m image_worker.batch status --batch-id <returned-batch-id>
python -m image_worker.batch cancel --batch-id <returned-batch-id>
```

For the first one-game acceptance test, use an owned internal catalog game ID
(not its Steam app ID):

```powershell
python -m image_worker.batch create --user-id 1 --max-games 1 --game-id <catalog-game-id>
```

Without explicit --game-id selection, creation snapshots the whole owned library
and refuses to create anything if it exceeds --max-games (allowed range 1..115).
Repeated --game-id selects a subset; every selected game must be owned. Missing
sources and games with saved S3 keys are represented by skipped jobs. Sources
and membership are captured once; later syncs never expand the batch.

Creation locks the owner row to serialize competing batch commands and rejects
another ready/running batch for that owner. Batch and jobs commit together.
Status reports ready/pending/running/completed/skipped/failed/cancelled counts;
remaining includes ready/pending/running. A batch with only skipped jobs finishes
immediately. Cancel is idempotent and invalidates unfinished job attempt tokens;
it cannot undo an external upload already in flight.

This is a trusted operator CLI, not end-user authorization. The dispatcher and
tasks validate batch/attempt state. Conditional S3 writes to deterministic keys
deduplicate overlapping owners; existing objects require matching source metadata.

## Docker setup and live acceptance (not run automatically)

Start the database, back it up, and apply both migrations before rebuilt apps:

```powershell
docker compose up -d db
docker compose exec -T db pg_dump -U backlog -d backlog_beater -Fc -f /tmp/before-image-jobs.dump
Get-Content backend/migrations/012_add_game_cover_s3_key.sql | docker compose exec -T db psql -U backlog -d backlog_beater -v ON_ERROR_STOP=1
Get-Content backend/migrations/013_add_image_batches_and_jobs.sql | docker compose exec -T db psql -U backlog -d backlog_beater -v ON_ERROR_STOP=1
docker compose --profile images up -d --build image-worker image-scheduler
```

Uploads stay disabled. Create one owned-game batch (internal game ID, not Steam ID):

```powershell
docker compose run --rm image-worker python -m image_worker.batch create --user-id 1 --max-games 1 --game-id <catalog-id>
docker compose run --rm image-worker python -m image_worker.batch status --batch-id <batch-id>
```

For an explicitly authorized live upload, set S3_UPLOADS_ENABLED=true in .env,
then run the following. This optional override mounts your Windows .aws directory
read-only (its profiles become readable to the worker); never commit credentials.
Only the worker uses backlog-demo credentials, not the scheduler.

```powershell
docker compose -f compose.yml -f compose.s3-demo.yml --profile images up -d --force-recreate image-worker image-scheduler
docker compose logs -f --tail 50 image-worker
```

After one game completes, inspect Game.cover_s3_key and its S3 object. App display
is a separate next step. Cancel/finish the one-game batch before creating the full
batch with --max-games 115 and no --game-id. Future syncs never add jobs.

After the demo, cancel unfinished work, set uploads false, recreate/stop the image
services, and deactivate/delete the access key. Delete objects and clear saved
keys when retiring them. A config change alone does not alter running containers
or interrupt an S3 request in flight. Stored objects and download usage remain
billable; this is not a guaranteed spending cap.

## Execution and recovery

The scheduler publishes maintenance every 15 seconds. One worker executes one
task at a time. Only saved batch jobs are dispatched, in batches of at most 100.
Broker failures leave pending intent in PostgreSQL; publication is retried after
five minutes using the same token. Pending backlog does not expire. Running jobs
older than two minutes are recovered with new tokens. Transient failures receive
up to three total attempts with one/five-minute delays; invalid images/sources,
configuration errors, and permission failures terminate. A completed batch may
contain failed jobs: inspect its counts.

Task limits are 80 seconds soft / 90 hard. Downloads allow only approved HTTPS
Steam hosts, reject redirects, and cap source/normalized JPEG size at 2 MB and
four million pixels. AWS calls use bounded timeouts/retries. No database
transaction stays open during network I/O. Cancellation and stale attempts cannot
save keys. Upload success followed by a database failure recovers through the same
S3 key, conditional PUT, and source-metadata verification. Unreferenced objects
from interrupted/cancelled work may remain; orphan cleanup is deferred.

## Testing without AWS

```powershell
docker compose -f compose.test.yml run --rm tests
```

Tests use isolated PostgreSQL/Redis. Image-task tests mock download and S3
boundaries to inject failures and inspect saved state. A botocore Stubber validates
real SDK PUT parameters using fake credentials and intercepts the request before
networking. No AWS profile is mounted. These tests verify logic, not live AWS
permissions or full Celery delivery; the one-game acceptance test remains needed.
