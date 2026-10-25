# Backend services

See [architecture.md](../architecture.md) for service responsibilities, the implemented
catalog-event boundary and planned reliability improvements.

HLTB lookup groundwork lives in `enrichment-service/enrichment_service/hltb.py` and uses pinned
`howlongtobeatpy==1.0.24`. `await lookup_candidates(title)` returns candidate
IDs, names, aliases, types, platforms, release years, and three completion
estimates in hours. Zero/missing estimates become `None`. The default overall
lookup deadline is 20 seconds. An empty list means no search results;
`HLTBLookupError` means provider or response failure, and `HLTBLookupTimeout`
means the deadline elapsed. Caller cancellation propagates.

The worker uses `review_match_candidates()` in `matching.py` to rank plausible
HLTB titles and aliases (minimum normalized similarity 0.65, with a small PC
metadata preference). It deduplicates by HLTB ID and retains up to three choices.
Similarity is a suggestion heuristic, not proof of identity. A unique exact-title
candidate with no known conflict can be accepted automatically; multiple plausible
identities, near-title matches, or metadata conflicts require review. Release-year
checking exists in the strict matcher but Steam imports currently provide no year.

## Shared review cache and personal choices

A separate `enrichment-cache` Redis service holds a shared shortlist only for
ambiguous catalog games with suggested candidates, under `hltb:review:v1:{game_id}` for **86400 seconds (24 hours)**. Every owner
sees the same candidates. Cache values carry a version, lookup timestamp, outcome,
and candidate metadata/estimates. Redis TTL handles automatic cleanup; restarting
this nonpersistent cache discards suggestions without touching the task broker.
The instance has a 128 MB no-eviction limit; a full/unavailable cache fails explicitly
rather than silently evicting active refresh leases. No cache port is published.
`HLTB_REVIEW_CACHE_URL` must match on the enrichment API and worker.

`GET /api/v1/enrichment/games/{id}/hltb-review` checks ownership and returns cached
choices. Missing/expired suggestions for an ambiguous game queue
`enrichment.refresh_review` and return HTTP 202 with `status: refreshing` and
`Retry-After: 3`. The modal polls every three seconds for up to two minutes,
stopping on close or account change. Matched and no-match outcomes remain in
PostgreSQL and do not create suggestion entries; a resolved lookup removes any
previous shortlist. A no-match refresh leaves no selectable review. Refresh
failures use a separate operational outcome marker for only 60 seconds; initial
lookup failures do not create review entries.
The API returns 503 when Redis or queue submission is unavailable.

Redis `SET NX EX` claims combine concurrent refresh requests. A pending claim
expires after 15 minutes; queued refresh tasks have the same expiry. Execution
atomically changes the token to a 60-second running lease. Duplicate deliveries
cannot start, expired workers cannot replace newer suggestions, and cleanup only
releases the caller's token. See [Redis SET documentation](https://redis.io/docs/latest/commands/set/).
This is refresh deduplication, not the pending PostgreSQL job/outbox workflow.
A queue backlog longer than 15 minutes may require reopening review to resubmit.

`PUT /api/v1/enrichment/games/{id}/hltb-selection` accepts `hltb_id` and
`cache_version`. It locks the game, checks ownership, the current cache version,
lookup timestamp and candidate membership, then saves the selected candidate and
estimates to `PersonalHLTBOverride`. Expired/changed selections return 409.
Selections remain per user and survive cache expiry and automatic refreshes.
Library responses prefer the complete personal override, including missing values.
The modal shows distinguishing metadata and “None of these / decide later”, which
closes without applying a choice. The API and worker import a shared cache helper;
sync imports neither enrichment API nor worker implementation.

`GameEnrichment` now stores only durable outcome/reason/timestamps/error codes.
Accepted shared estimates remain on `Game`; full personal selections remain in
PostgreSQL. Candidate lists are no longer written to PostgreSQL. Migration 004
removes the legacy candidate column; old ambiguous games obtain new suggestions
on their next review request. Run with old enrichment API/workers stopped:

```sh
docker-compose exec -T db psql -U backlog -d backlog_beater -v ON_ERROR_STOP=1 < backend/migrations/004_move_review_candidates_to_redis.sql
```

### Background enrichment after Steam sync

After the import commits, sync's independent `sync_service/events.py` client
publishes one `catalog.games_imported` message to Redis with `schema_version: 1`
and `steam_app_ids`. Sync does not import or package enrichment code. Its response
contains `enrichment.submitted` and `enrichment.unavailable`, describing event
submission rather than completed lookups or individual game-task counts. An empty
library publishes no event. The frontend asks users to refresh later for results.

Enrichment's `tasks.py` consumes and validates the event, resolves Steam IDs to
catalog IDs, and publishes one `enrichment.enrich_game` task per eligible game.
Eligible games have a nonempty title, no shared HLTB ID, and either no lookup
record or a prior failure. Successful no-match and ambiguous results are reused.
Duplicate IDs within an event are collapsed; overlapping events can still queue
duplicate work. Both task types run on the same worker and queue.

Each task loads a title and lookup timestamp, closes its database session,
then searches HLTB with a 20-second deadline. It locks the game in a new
transaction and rechecks title, eligibility, and lookup timestamp before saving.
Changed titles or intervening writes make the result stale. Failure records
use fixed error codes; personal overrides and ownership/progress are preserved.
Celery task limits are 30 seconds soft and 35 seconds hard. Task execution does
not hold a database connection during the HLTB request.

Sync publication uses one broker connection, no publish retries, and two-second
socket/connect timeouts. Broker failures produce a nonfatal `unavailable` flag;
Steam imports remain committed. Enrichment's event handler publishes eligible
game tasks through durable PostgreSQL job claims. A database or broker failure there fails
the background event task and is logged; it cannot alter the earlier sync response.
A later sync can resubmit eligible games. `CATALOG_BROKER_URL` configures the sync
producer; `ENRICHMENT_BROKER_URL` configures the enrichment worker.

Persistent job claims deduplicate execution. HLTB lookups now have a shared gate
and bounded temporary-failure retries; metadata has separate pacing and explicit
backfill. A sync-side outbox and HLTB backfill remain pending: process failure
between import commit and event publication can leave games unqueued until
another sync. `inline.py` has been removed.

Validated October 10, 2026: all 97 backend tests and five frontend tests passed.
Updated sync and worker containers are healthy. A task published from the sync
container through Redis was consumed by the worker and safely skipped a missing
game ID; real HLTB calls were mocked in the persistence tests.

Validated October 11, 2026: 103 backend tests and five frontend tests passed.
Sync's rebuilt image imports no enrichment source. A live v1 catalog import event
was consumed by enrichment and safely scheduled zero tasks for an unknown Steam
app ID. Scheduling and eligibility are covered with mocked broker tests. Both
updated containers are healthy; Nginx was restarted after recreation.

`Game` now requires four additional nullable columns and their constraints;
existing databases need a migration before updated services are started.
Startup can create missing tables but cannot add columns to existing tables.
Apply the tested HLTB upgrade from the repository root:

```sh
docker-compose exec -T db psql -U backlog -d backlog_beater -v ON_ERROR_STOP=1 < backend/migrations/003_add_hltb_enrichment.sql
```

Migration 003 adds the nullable Game fields/constraints and creates missing
enrichment tables in one transaction. It can be rerun without changing existing
metadata. `python3 scripts/check_hltb_migration.py` verifies legacy-data
preservation, repeat application and constraints in a disposable schema.
Applied locally October 10, 2026: fingerprints confirmed all 115 catalog and
115 ownership/progress records unchanged; authenticated library retrieval
through Nginx returned HTTP 200 with HLTB fields. A pre-migration database backup
was saved at `/tmp/backlog-before-hltb-migration.dump` (temporary storage).
The full versioned migration workflow and remaining accumulated-schema work
are still pending, as recorded in `to-do.md`.
Storage tests require the isolated PostgreSQL Docker suite shown below.
Validated October 10, 2026: all 97 backend tests passed against fresh PostgreSQL,
including 20 enrichment persistence/inline tests. A separate disposable Compose
project (`docker-compose -p backlog-enrichment-tests -f compose.test.yml run --rm tests`)
avoids stale schemas in previously used test containers.

Validate lookup and matching without a database or live HLTB requests, after
installing the backend dependencies:

The service folder is `enrichment-service`; its importable Python package is
`enrichment_service`. Add the service folder to `PYTHONPATH` when importing it
outside its own working directory. The test suite sets this path automatically.

```sh
cd backend
python -m unittest discover -s tests -p 'test_hltb*.py' -v
```

The frontend in the repository-root `frontend/` folder is served by Nginx at `http://localhost:8080` as its entry point. Each HTTP service has its own
FastAPI application, Dockerfile, and image:

| Service | Entry point | Responsibilities |
| --- | --- | --- |
| Auth | `auth_service.main:app` | Registration, login, account details, Steam account linking and callbacks |
| Library | `library_service.main:app` | Read owned games and update personal completion status |
| Sync | `sync_service.main:app` | Fetch and import the linked user's Steam library |
| Enrichment | `enrichment_service.main:app` | HLTB candidate review, selection validation and personal overrides |

Existing API paths are preserved: `/api/v1/auth/*`, `/api/v1/steam/link`, and
`/api/v1/steam/callback` belong to auth; `/api/v1/games` and
`/api/v1/games/{game_id}` belong to library; `/api/v1/steam/sync` belongs to sync.
There is no combined main API application.
The `/api/v1/enrichment/games/{game_id}/hltb-*` review routes belong to
enrichment. Library routes retain their original `/api/v1/games/` prefix.

`app/database.py` holds the shared models and database session code.
`app/core/security.py` validates tokens only in the internal auth check.
`app/core/identity.py` parses the verified `X-User-ID` forwarded by Nginx.
Library uses that integer directly for ownership queries, avoiding a second
user lookup. Auth and sync load account records only where account details
are needed; Nginx's auth check has already verified user existence.
Protected auth, library, sync, and enrichment routes use that identity and do not decode
JWTs again. Ownership checks remain in the services. Direct service access is
trusted access: a caller that can reach a backend port can supply identity.
Compose publishes only Nginx, isolates the service/database networks, and gives
auth and sync separate outbound networks for Steam. Do not publish backend ports
or connect untrusted containers to these networks.

## Worker package boundary

The Celery runtime is in `enrichment-worker/enrichment_worker/`, separate from
`enrichment-service/enrichment_service/`. The worker has its own Dockerfile;
the API image contains no worker package. The worker image copies the existing
HLTB, matching, and storage helpers but excludes `api.py` and `main.py`.
Those helpers and shared database models remain code dependencies; this folder
split does not imply entirely independent domain libraries or databases.
Task names and event payloads are unchanged by the relocation.

## Enrichment worker infrastructure

`enrichment_worker.worker:app` defines a Celery app with a single `enrichment`
queue. Compose starts `enrichment-worker` with concurrency two and a dedicated
`enrichment-redis` broker on a private network, without published ports.
Redis uses append-only persistence and the `enrichment_redis_data` volume;
`docker compose down` preserves that volume. The worker builds its own
`enrichment-worker/Dockerfile`, runs as a non-root user, and has database and outbound network access
for HLTB and Steam Store tasks. It waits for healthy PostgreSQL and Redis.

`enrichment.healthcheck` is a harmless task that logs completion.
`enrichment.enrich_game` processes one catalog game; sync publishes catalog import events
after import, and the enrichment event handler submits eligible HLTB and metadata
jobs. Persistent PostgreSQL job state and token claims deduplicate execution.
Metadata backfill is explicit; HLTB backfill remains deferred. HLTB retries
are bounded to identifiable temporary failures. Celery results are disabled; PostgreSQL is the job-status store. JSON serialization and explicit queue declarations follow the
[Celery configuration documentation](https://docs.celeryq.dev/en/stable/userguide/configuration.html).

Start and verify from the repository root:

```sh
docker compose up -d --build enrichment-redis enrichment-worker
docker compose ps enrichment-redis enrichment-worker
docker compose exec enrichment-worker python -m celery -A enrichment_worker.worker:app inspect active_queues
docker compose exec enrichment-worker python -m celery -A enrichment_worker.worker:app call enrichment.healthcheck
docker compose logs --tail=50 enrichment-worker
```

The logs should show `enrichment.healthcheck` received and succeeded. The worker
healthcheck targets its own node with a Celery ping. For manual worker startup,
set `ENRICHMENT_BROKER_URL` to a reachable Redis URL and include
`backend/enrichment-worker` and `backend/enrichment-service` on `PYTHONPATH`. Compose sets the internal broker
URL directly; no new root `.env` setting is required.

Validated October 10, 2026: Compose configuration passed, both new containers
reported healthy, the worker subscribed only to `enrichment`, and a queued
healthcheck was received and succeeded. No schema changes were needed.

## Build images

Run from the repository root. All builds use `backend` as their build context
for the shared dependency manifest and shared `app` package:

```sh
docker build -f backend/auth_service/Dockerfile -t backlog-beater-auth ./backend
docker build -f backend/library_service/Dockerfile -t backlog-beater-library ./backend
docker build -f backend/sync_service/Dockerfile -t backlog-beater-sync ./backend
docker build -f backend/enrichment-service/Dockerfile -t backlog-beater-enrichment ./backend
docker build -f backend/enrichment-worker/Dockerfile -t backlog-beater-enrichment-worker ./backend
```

Each image includes its own service package and shared code, and starts its
own application automatically.

## Start the gateway stack

From the repository root:

```sh
cp .env.example .env
python3 -c 'import secrets; print(secrets.token_hex(32))'
```

Put the generated value in `.env` as `SECRET_KEY`, and set `STEAM_API_KEY`
when testing real Steam syncs. Only auth receives the JWT secret. Compose loads
this root `.env`; applications do not load environment files themselves.
Keep `POSTGRES_PASSWORD` consistent with an existing database volume. A password
used in the database URL must be URL-safe or percent-encoded.

```sh
docker compose up --build -d
docker compose logs -f nginx auth library sync enrichment
docker compose down
```

If using standalone Compose, replace `docker compose` with `docker-compose`.
The only published port is `127.0.0.1:8080`; PostgreSQL is no longer exposed at
host port 5433. `docker compose down` preserves the database volume.

Auth waits for PostgreSQL, creates missing tables, then becomes healthy.
Library and sync wait for auth; Nginx waits for all three services. Startup
creation does not migrate existing tables. Use migrations for existing schemas.

Steam linking uses `APP_PUBLIC_URL=http://localhost:8080` and
`STEAM_CALLBACK_URL=http://localhost:8080/api/v1/steam/callback` by default.
For hosted use, set both to the public HTTPS gateway origin and configure TLS.
Nginx passes cookies and `Set-Cookie` unchanged. The sync proxy timeout is 60s. Steam fetching has a 45-second overall deadline,
a 10-second HTTP I/O timeout, and at most four attempts with 1-, 2-, and
4-second retry delays. Connection/transport failures and HTTP 408, 429, 500,
502, 503, and 504 are retried; other HTTP errors and invalid library data fail
immediately. Exhausted timeouts return 504; other upstream failures return 502.
No library writes occur until fetching and validation succeed.
Uvicorn trusts forwarded headers only from Nginx's fixed service-network address,
`172.30.80.2`; keep that address and the Compose subnet aligned. Automatic
container addresses use `172.30.80.128/25` to avoid assigning Nginx's reserved IP
to another service.

Nginx authenticates protected routes through an internal `auth_request`
subrequest, captures the auth response's user ID, overwrites caller identity,
and strips `Authorization` before forwarding. Login, registration, and the
independently validated Steam callback are public. Unknown/internal routes return
404. Backend responses pass through; unavailable backends return gateway errors.
Auth-subrequest failures deny access (Nginx returns 500 for an unexpected auth
response). If a backend container is recreated with a new IP, restart Nginx to
refresh its upstream addresses.

## Validation

Auth provides `GET /internal/auth/check` for Nginx authentication subrequests.
Send the original `Authorization: Bearer <token>` header. A valid token for an
existing user returns an empty `204` response with `X-User-ID` and
`Cache-Control: no-store`; missing, invalid, expired, or unknown-user tokens
return `401` with `WWW-Authenticate: Bearer` and no identity header. The check
ignores caller-supplied identity headers and is omitted from OpenAPI docs.
Nginx exposes this check only through its internal subrequest location; direct
requests to internal paths return 404.

The existing PostgreSQL integration tests now target their owning services.
Run from the repository root:

```sh
docker compose -f compose.test.yml run --rm tests
```

Run the database-independent service boundary tests from `backend`:

```sh
python -m unittest discover -s tests -p test_service_boundaries.py -v
```

Validate Nginx and run the live gateway checks from the repository root:

```sh
docker compose exec nginx nginx -t
python3 scripts/check_gateway.py
```

The gateway checks create two disposable accounts and a pending Steam link.
They verify token rejection, identity spoofing, routing, hidden internal endpoints,
and Steam cookie forwarding. PostgreSQL tests verify ownership access and sync
transactions. Full Steam callback success still needs real Steam testing; service
unit tests mock Steam's OpenID verification. Enrichment and recommendation
services will be implemented separately.

Validated locally: all three images built, Nginx configuration check passed,
all five live gateway tests passed, and all 40 Docker backend tests passed.
An auth outage returned 500 for protected requests, and a correctly signed
expired JWT returned 401. The local runtime is Colima (`colima start`);
standalone `docker-compose` works with it. The Steam API key is still needed
for real Steam syncing.

Common proxy headers and timeouts live in `gateway/common.conf`, included by
both the public proxy and protected route snippets.

Browser Steam callbacks redirect to the frontend with success/error feedback.
API clients requesting JSON keep the existing callback response contract.
See `frontend/README.md` for interface setup and current metadata limitations.

Game metadata: sync requests Steam app info, persists names and Steam header
image URLs, and refreshes existing catalog metadata without resetting progress.
Apply `backend/migrations/002_add_game_name.sql` to existing databases before
running updated services; missing-table creation does not add the new column.
The local database has been upgraded. Re-sync to populate existing game names.

## Live enrichment validation — October 11, 2026

Restarted the stack with `docker-compose down` and `docker-compose up -d --build`
without removing volumes. Service health checks, Nginx configuration, and five
live gateway checks passed. An import event published from sync for existing
Red Faction (Steam app 20530) scheduled one game task. The separated worker made
a real HLTB lookup and persisted a shared match (HLTB 7681, main story 6.02 hours)
with no lookup error. A repeat import event scheduled zero tasks, confirming
saved-result reuse. This exercised producer, Redis, event handler, game task,
provider access, and persistence; it did not exercise a new live Steam fetch.

Validated October 11, 2026 for Redis review: 113 backend tests, eight frontend
tests, five live gateway checks, and Chrome loading/selection checks passed.
Migration 004 was applied locally with old enrichment processes stopped and a
backup at `/tmp/backlog-before-redis-review.dump` (temporary storage). Game and
ownership fingerprints confirmed all 115 records in each table unchanged;
personal override data was unchanged. All shared-model images were refreshed.
A real review of Half-Life 2: Deathmatch returned HTTP 202, then a cached candidate
with a TTL near 86400 seconds; repeat review reused its cache version.

## Currently Playing and Play Next

Currently Playing uses the existing `in progress` completion status. Multiple games
can be active. Users manually choose single-player games until verified game-mode
metadata is available. Play Next is a separate personal ordered queue; completed
games can be queued for replay, while active games cannot be queued.

Authenticated library routes (already covered by the gateway's `/games/` route):
- `GET /api/v1/games/queue`: ordered catalog game IDs.
- `POST /api/v1/games/queue/{id}`: append an owned game, idempotently.
- `DELETE /api/v1/games/queue/{id}`: remove a queue entry, idempotently.
- `PUT /api/v1/games/queue`: `{ "game_ids": [...] }`, a complete permutation of
  the current queue; conflicts return 409 and require a refresh.
- Existing `PATCH /api/v1/games/{id}` with `in progress` starts a game and removes
  its queue entry in the same transaction. Finishing does not start another game.

Apply `backend/migrations/005_add_play_queue.sql` before serving the new UI against
an existing database. It only creates a table and preserves existing library data.
All queue mutations and completion updates lock the account row to serialize
simultaneous edits. Queue data is independent of Steam import updates.
Recommendations remain a separate future service; accepting a recommendation
can use these same library actions.

## Enrichment assessment views and search matching

The library has Needs Review and Missing Completion Data views with counts and
search support. Ambiguous games open the existing selection modal. Failed/no-match
lookups and resolved matches without any usable estimates appear under missing
data. Pending lookups remain labeled pending; valid personal estimates resolve an
ambiguous shared outcome, while personal matches without estimates remain missing.
These are views of existing ownership, not separate collections.

Search queries now remove trademark symbols and collapse whitespace before HLTB
requests, preserving subtitles, edition names and numbers. An exact base-game
match can ignore competing entries explicitly labeled `dlc` or `expansion`;
unknown entry types and near-title base games still require review. Verified live
Wildlands metadata labels Narco Road and Fallen Ghosts as `dlc`.

Retry/reassessment endpoints and automatic reprocessing of saved outcomes are
outside this change. Existing no-match/failed records are not refreshed by deployment.

### Persistent enrichment jobs

Apply `migrations/006_add_enrichment_jobs.sql` before starting the updated worker.
Rebuild the worker image and start `enrichment-worker` and `enrichment-scheduler`;
run exactly one scheduler. Migration 006 creates no jobs and triggers no backfill.
Drain old enrichment messages before upgrading: game lookup messages now require
`game_id` and `attempt_token`.

Jobs follow `ready → pending → running → completed`, or `running → failed`.
Successful lookups with ambiguous/no-match outcomes are completed jobs. Imports
create missing jobs only; existing failed/completed jobs are not reset. For an
explicit retry, call `enrichment_worker.jobs.retry_failed(game_id)` in the worker
container; the scheduler dispatches the resulting ready job.

Maintenance runs every minute in bounded batches of 100. Pending attempts can be
republished after five minutes with the same token; queue backlog does not expire
them. Running attempts older than two minutes fail with `worker_timeout`.
Conditional database claims and token validation make duplicate deliveries harmless.
Job completion and enrichment writes share a transaction. Review-cache refreshes
continue using their existing Redis lease, independently of these job records.
The sync-to-catalog import event is still best-effort: failure before job creation
requires a repeat sync. No automatic catalog-wide backfill is performed.

Validation (October 24, 2026): 128 backend tests pass against isolated PostgreSQL
and Redis, including job claims/recovery, stale-worker fencing, and migration 006
rerun/data-preservation/constraint checks. If an older test database contains
legacy tables that prevent fixture teardown, use a fresh Compose project rather
than deleting that database:

```sh
docker-compose -p backlog-foundation-check -f compose.test.yml run --rm tests
```

This command does not apply migrations to the application database.

### Steam metadata schema

Migration `007_add_game_metadata.sql` adds `game_metadata` and
`game_metadata_jobs`. It is additive/rerunnable and does not backfill or enqueue.
`game_metadata` stores nullable single-player support, JSON genre objects such as
`[{"id":"1","name":"Action"}]`, lookup/success timestamps, and a last error. Successful metadata is retained
without automatic refresh; failure retry timing belongs to job scheduling. NULL single-player means unknown;
an empty genre array alone does not prove successful collection. Genre-entry
validation is performed by the Steam adapter; the database enforces an
array container. The independent metadata job uses the existing five job states,
attempt tokens, and publication/recovery timestamps. Neither table changes HLTB
outcomes or personal ownership, progress, overrides, or queues.

Apply migration 007 before deploying the metadata worker integration; application
migration is separate from isolated tests. Automatic retry cycles remain deferred.

Schema validation (October 24, 2026): all 130 backend tests pass against isolated
PostgreSQL/Redis, including metadata model round trips, independent job states,
migration 007 reruns, preservation of populated catalog/user/HLTB/queue tables,
and database constraints. The JSON-array constraint is PostgreSQL-specific so
SQLite-based service tests remain supported. Migration 007 has not been applied
to the application database.

### Steam metadata adapter

`metadata-worker/metadata_worker/steam_metadata.py` provides async
`lookup_metadata(steam_app_id)` and pure `parse_metadata(steam_app_id, payload)`.
It performs one HTTPS Store `appdetails` request in English with a ten-second
I/O and overall timeout; redirects are rejected. It has no database or queue
side effects. The metadata task applies shared Redis pacing before calling it;
automatic delayed retries remain deferred.

Results contain nullable `single_player` and immutable genre records with stable
string IDs and normalized display names. Category ID 2 indicates single-player,
including mixed-mode games. Missing categories mean unknown; a valid category
list without ID 2 means no declared support. Genres must be an explicit list;
duplicates are collapsed and conflicting labels rejected. Unavailable apps,
malformed responses, network/HTTP errors, and timeouts raise typed lookup errors
instead of returning multiplayer-only or empty fallback results. Future storage
must preserve previously verified metadata on these failures.

Response shape checked against Steam's own endpoint:
https://store.steampowered.com/api/appdetails?appids=620&l=english

Adapter validation: all 141 backend tests pass, including mocked Store responses,
unknown/mixed-mode support, genre normalization, errors, and overall timeouts.

### Metadata collection jobs and backfill

Imports now create missing metadata jobs independently of HLTB eligibility.
The existing queue and worker process both task types with concurrency two.
`catalog.collect_metadata(game_id, attempt_token)` atomically claims its job,
waits for the shared Redis request slot (one Store request start per second),
then calls the adapter. No database transaction is held during pacing or HTTP I/O.
Task limits are 25 seconds soft / 30 seconds hard. Failed slot acquisition stops
requests rather than bypassing pacing. Result writes and job completion commit
atomically after checking the token and catalog identity. Verified metadata is
never automatically refreshed or overwritten on failure.

Beat schedules metadata maintenance every minute. Pending attempts are eligible
for same-token republication after five minutes; duplicate deliveries skip at the
claim. Running attempts expire after two minutes. Failed jobs are retained and
require `metadata_worker.metadata_jobs.retry_failed(game_id)`. Import events do
not reset them. Automatic delayed retries and a user-facing retry API are deferred.

To explicitly backfill a bounded set of existing games after migration/deployment:

```sh
docker-compose exec metadata-worker python -m metadata_worker.backfill_metadata --limit 100 --after-id 0
```

The command reports created/published counts and `last_game_id`; use that cursor
as `--after-id` for another batch. Limits are 1..1000; maintenance handles any jobs
above the dispatch batch size of 100. Existing jobs and successful metadata are
skipped. Backfill is never automatic. The worker image and Compose command both
use concurrency two; no extra queue or worker container was added.

Metadata integration validated October 24, 2026: all 148 backend tests pass
against isolated PostgreSQL/Redis. Coverage includes dispatch deduplication,
token-checked persistence, stale attempts, failure/republication/timeout recovery,
one-time collection, bounded backfill, and shared request pacing. Compose
configuration validation passes. No application migrations, deployment, live
Store backfill, or live-worker concurrency QA was performed in this step.

## Local metadata deployment verification — October 24, 2026

Migrations 006 and 007 applied successfully to the local application database
following a custom-format backup at `/tmp/backlog-before-metadata.dump`.
Rebuilt and started the worker and scheduler without recreating other services.
The enrichment queue was empty before the upgrade. Worker inspection confirmed
prefork concurrency two and both execution slots processed live metadata jobs.

A bounded five-game backfill created and published five jobs (last catalog ID 5).
All five completed with no errors and persisted genres: Half-Life 2 and Portal 2
reported single-player support; Half-Life 2: Deathmatch, Half-Life Deathmatch:
Source, and Team Fortress 2 reported no declared single-player support. This was
a small live verification, not a full-catalog backfill or recommendation rollout.
The backup is local temporary storage; copy it to durable storage if retaining it.

Live scheduler verification: both HLTB and metadata maintenance tasks were
published at the first one-minute interval and completed successfully with zero
additional jobs dispatched.

## HLTB pacing and temporary-failure retries

Both ordinary enrichment and review refresh tasks share the Redis gate in
`enrichment_worker/hltb_pacing.py`. It permits one active whole lookup and at
least three seconds between lookup starts. An owner token protects release;
a 45-second lease outlasts the 35-second hard task limit and recovers crashes.
This limits wrapper lookups, not every underlying HTTP request: wrapper 1.0.24
performs discovery/auth/search requests and hides some HTTP failures as `None`.
Three seconds is our conservative setting, not a published provider quota.

Busy or unavailable gate: use Celery countdown retry with the same job/token,
remaining gate wait plus jitter (ten seconds if Redis is unavailable), without
claiming running or consuming a processing attempt. Redis failures fail closed.
Release the gate in `finally`. Pending PostgreSQL records remain recoverable by
maintenance if publishing a deferred task fails. Countdown tasks may be reserved
in worker memory until due; they do not occupy a processing slot while waiting.

Migration 008 adds `attempt_count` and `next_attempt_at` to HLTB jobs. A successful
pending-to-running claim increments the processing count. Identifiable temporary
failures (timeouts, connection errors, exposed 429/5xx responses) get at most
three processing attempts per cycle. The first failure sets ready with a due time
at least one minute away; the second waits at least five minutes. Maintenance
honors the due time, assigns a new token, and dispatches; its one-minute schedule
and queue backlog can add delay. The third failure stays failed. Manual
`jobs.retry_failed(game_id)` resets the count and due time for a fresh cycle.
No-match and ambiguous outcomes remain completed. Malformed data, opaque wrapper
failures, and other errors are terminal; they are not assumed temporary.

Identifiable temporary failures also set a shared provider cooldown of at least
one minute. Exposed Retry-After seconds/date values extend the retry delay and
global cooldown. The wrapper may hide the actual status/header, so this cannot
promise detection of every throttling response. Successful existing results and
personal overrides are preserved.

Review refreshes use the same gate and three-attempt policy, with a failure count
in their task arguments and a newly claimed per-game refresh token after each
failed lookup; busy-gate deferrals retain their current token. Review retry state
remains in Celery/Redis rather than the HLTB job table. Expired running jobs still
become failed through maintenance; crashes are not blindly classified transient.
Steam metadata retry behavior is unchanged (explicit retries only).

Apply migration 008 before deploying the updated worker. Existing failed games
are not automatically restarted or backfilled by the migration or deployment.

HLTB limiter validation/deployment (October 24, 2026): all 154 backend tests pass,
including Redis gate ownership/spacing/cooldown, busy-task deferral without claims,
three-attempt backoff/manual reset, retryable HTTP classification, review deferral,
and migration 008 reruns. Migration 008 was applied locally after backup to
`/tmp/backlog-before-hltb-retries.dump`; worker and scheduler were rebuilt and
restarted successfully. No automatic catalog rerun or reset of failed jobs was
performed. Live external HLTB load was not generated for verification.

### Queue split: step 1 (routing only)

The Celery app now declares `steam_metadata` alongside `enrichment` on the same
Redis broker. Metadata dispatch explicitly publishes `catalog.collect_metadata`
to `steam_metadata`; task routes and the Beat metadata-maintenance entry also
select that queue. Import events and HLTB tasks retain the `enrichment` default.
Backfill uses metadata dispatch and therefore follows the new queue automatically.

This is a staged source change: no metadata worker container has been added yet,
no concurrency or HLTB gate behavior changed, and the application remains stopped.
Do not deploy this intermediate configuration alone: metadata messages need the
separate consumer from the next step. Existing messages already in Redis are not
moved by changing routing. No migration is required.

### Queue split: step 2 (metadata worker configuration)

Compose now defines `metadata-worker`, built from the existing
`enrichment-worker/Dockerfile` and using the same Celery app/task modules.
It subscribes only to `steam_metadata`, uses concurrency one, and identifies
itself as `metadata@%h` with a targeted ping health check. It has database,
broker, and outbound network access; it does not require the HLTB review cache.
No separate HTTP API, broker, task implementation, or schema is added.

This step only adds container configuration. The app has not been started or
redeployed. The HLTB worker concurrency and gate changes remain step 3.

### Metadata source separation

Metadata code now lives in `backend/metadata-worker/metadata_worker/`: `tasks.py`,
`worker.py`, `metadata_jobs.py`, `steam_metadata.py`, `steam_pacing.py`, and
`backfill_metadata.py`. It has its own `backend/metadata-worker/Dockerfile`.
Compose's metadata worker and health check use `metadata_worker.worker:app`.
Task names, Redis queue names, database schemas, and attempt-token behavior are
unchanged. The existing Beat scheduler still routes metadata maintenance to
`steam_metadata`; no second scheduler is required.

The import-event handler remains in the enrichment package and imports metadata
job creation/dispatch helpers. Its image includes the metadata package for that
explicit dependency, but no longer defines metadata task execution in its own
`tasks.py`. The metadata image does not copy HLTB enrichment implementation.
The test environment includes the new metadata package path. No app containers
have been started or redeployed for this source move; the HLTB concurrency/gate
change remains a separate next step.

Metadata source move validation: 155 backend tests pass; both worker images
build and pass offline import smoke checks. Compose validation passes. The app
remains stopped; no migration or live task processing was performed.

### Queue split: step 3 — sequential HLTB execution

The HLTB Compose worker and Dockerfile default now use concurrency one.
Metadata remains in its separate concurrency-one container/queue. Run exactly
one HLTB worker instance: serialization now comes from its execution slot,
not a global active-lookup lease. Scaling HLTB requires revisiting that policy.

`hltb_pacing.wait_for_slot()` atomically enforces three-second start spacing
in Redis, sleeping locally only for short spacing. The obsolete 45-second
active-lookup key is ignored; there are no busy-worker gate deferrals, tokens,
or release calls. Database job tokens and per-game review leases remain intact.
HLTB still may issue several underlying HTTP requests per wrapper lookup.

Long provider cooldowns defer via Celery countdown with jitter, keeping the job
pending without consuming an attempt. This avoids sleeping past the existing
30/35-second task limits; it is distinct from the removed busy-gate retries.
Redis coordination failures also defer without calling HLTB. Temporary lookup
failure backoff, Retry-After handling, and the three-attempt budget are unchanged.
Metadata pacing is unchanged. No schema migration is needed.

This step changes source/configuration only. The app remains stopped. Existing
queued/ETA messages are not purged or moved; an old deferred message may retain
its previously assigned delay until delivered. Deploy both worker images and
queue consumers together in the next step.

Step 3 validation: all 156 backend tests pass, including local spacing, ignoring
the obsolete active lease, cooldown preservation, deferral without processing
claims, and Redis failure handling. Compose configuration and compilation pass.
The app remains stopped; live deployment verification is next.

Worker split deployed locally October 24, 2026: all app images rebuilt, local
application rows/IDs and Redis queue/cache state reset, and the full stack
started. Gateway returns HTTP 200. Live worker inspection confirms enrichment
consumes only `enrichment` and metadata consumes only `steam_metadata`, both
with concurrency one. Both workers and API/database/cache health checks pass.
The database was verified empty; a new account/link/sync is required to observe
fresh tasks. No live provider lookups were triggered during this reset check.

### Steam cover source correction

The metadata adapter now validates optional `header_image` URLs (HTTPS, explicit
Steam CDN host allowlist, no credentials or custom ports). Missing/invalid artwork
does not discard otherwise valid metadata. On successful collection, the task
stores the returned URL in `Game.cover_url` only if the previous cover is missing
or equals the constructed legacy fallback for that app. Other saved/custom covers
are preserved. Sync uses the constructed URL for new games and missing covers,
but no longer overwrites existing URLs on repeat imports.

This fixes newer Store asset paths where the legacy URL serves a gray placeholder
with HTTP 200. Existing completed metadata jobs are not automatically rerun; cover
repairs are explicit. No migration or S3 changes are required. Future image
processing can download this verified source and store an S3 key separately.
All 159 backend tests pass, covering URL validation, fallback replacement,
preservation of custom covers, and preservation across repeat syncs.

Cover fix deployed locally: sync and metadata images rebuilt, gateway reloaded,
and only Battlefield 6 (Steam app 2807960) explicitly repaired from its legacy
fallback to Steam's current header_image. Other existing covers were not backfilled.

### Library game-mode filters

The library toolbar now offers All game modes, Single-player, Multiplayer,
Single-player only, Multiplayer only, and Unknown mode, combined with progress,
search, and sort. It displays matching/total counts. Mixed-mode games appear
under both supported modes; exclusive filters require explicit false for the
other mode. Missing data is unknown and never inferred from names or failures.

Migration 009 adds nullable `game_metadata.multiplayer`. The Store adapter reads
category ID 1 independently of single-player category ID 2. Valid category arrays
produce booleans; absent categories preserve unknown. Library list and progress
update responses expose both fields without per-game queries or provider calls.

Existing completed metadata is not automatically refreshed. To explicitly fill
only mode fields for previously collected records, use a bounded batch:
`docker-compose exec metadata-worker python -m metadata_worker.backfill_modes --limit 100 --after-id 0`.
It uses shared Steam pacing, preserves covers/genres/progress/HLTB data, and
reports updated/failed counts plus a cursor. Normal metadata collection stores
both modes for new games. No HLTB re-enrichment is required.

Game-mode filtering validated/deployed October 24, 2026: 162 backend and 10
frontend tests pass. Migration 009 applied locally after backup at
`/tmp/backlog-before-game-modes.dump`; library and metadata services rebuilt.
Authenticated library verification returns HTTP 200 with both mode fields for
115 games. The explicit mode-only backfill filled 105 records with no request
failures; 10 remain unknown. Stored totals: 82 single-player, 81 multiplayer,
59 supporting both. Counts overlap by design. Covers/genres and HLTB work were
not backfilled. Visual browser QA remains pending.

## Recommendation service — step 1: request contract

`backend/recommendation_service/` now contains an independent FastAPI app with
`GET /internal/health` and a `RecommendationRequest` model in `api.py`.
Preferences require finite numeric weekly hours greater than zero and at most
168, and an integer target window of 1..52 weeks. Boolean/string numeric inputs
are rejected. The budget is their product. Completion goal defaults to
`main_story`, with `main_extra` and `completionist` supported. Up to 20 distinct
canonical positive string genre IDs are allowed; no selected genres is valid.
Unknown request fields are rejected. Genre membership validation against stored
metadata belongs to the future database-backed endpoint, not this syntax model.

The recommendation route, repository, ranking, Dockerfile, Compose/Nginx wiring,
and frontend are not implemented in this first step. No app deployment or schema
migration was performed. The agreed MVP uses request-only preferences, strict
time eligibility, and includes Play Next games without modifying their order.
History-based affinity and ratings are deferred.

Recommendation step 1 validation: all 167 backend tests pass, including preference
input boundaries and the standalone health endpoint. No recommendation endpoint
or runtime container has been deployed yet.

## Recommendation service — step 2: pure ranking

`recommendation_service/ranking.py` defines immutable Candidate/Recommendation
records and `rank_candidates(candidates, preferences)`. Candidates must already
be ownership-scoped and carry effective whole-set personal/shared estimates;
repository resolution is the next step. No database/provider calls occur here.

Eligibility requires not-started progress, explicitly verified single-player
support, a resolved match, and a finite positive estimate for the requested
completion category within the strict hours/week × weeks budget. Missing
categories are never substituted and estimates are not rounded before filtering.
Play Next is included; saved queue positions are returned without scoring or
mutation. Ranking sorts by descending fraction of preferred genres matched,
then descending budget utilization, then ascending game ID. No selected genres
means time-only ordering. At most three distinct games are returned. Identical
duplicates collapse; conflicting duplicate records raise rather than silently
selecting an arbitrary estimate.

Results include cover/title, source, matched genre IDs, hours, approximate weeks,
queue position, and factual reasons with the full-weekly-budget assumption.
The endpoint, database repository, container, gateway wiring, and frontend remain
unimplemented. This step has no deployment or schema changes.

Recommendation ranking validation: all 173 backend tests pass, including the
exact 12-hour boundary, invalid/missing estimates, category selection, mode and
progress eligibility, genre-first ordering, empty preferences, stable ties,
queued/personal estimates, and distinct results. No runtime deployment occurred.

## Recommendation service — step 3: repository

`recommendation_service/repository.py` loads ownership-scoped Candidate records
with one SELECT joining shared games, personal progress, Steam metadata, lookup
outcomes, personal HLTB overrides, and Play Next membership. Both personal joins
are constrained to the ownership user; another owner's override/queue position
cannot affect a candidate. Missing metadata stays unknown. Malformed genre IDs
are ignored, and candidates are ordered by catalog ID.

A personal override replaces the complete estimate set, including missing values.
It resolves shared ambiguity for that owner. Without an override, a shared HLTB
ID is resolved only if its lookup outcome is matched, or if no outcome row exists
for a legacy saved match. Explicit ambiguous/no-match/lookup-failed outcomes do
not resolve merely because an ID is present. Ranking remains responsible for
progress, mode, selected-category, and budget eligibility.

The repository performs no writes/provider calls or imports from other service
implementations. The authenticated endpoint, supported-genre membership check,
Dockerfile, Compose/Nginx, and frontend are still future steps. No app deployment
or schema change was performed in this repository step.

Recommendation repository validation: all 178 backend tests pass, including
user isolation, whole-set override precedence, ambiguity resolution, missing
metadata, one-SELECT loading, and unchanged personal queue/progress. No runtime
deployment occurred.

## Recommendation API and container

The read-only `recommendation` Compose service uses its own Dockerfile/package,
internal gateway/database networks, and a health check. Nginx exposes only the
exact protected `POST /api/v1/recommendations` route, using existing auth_request
identity forwarding and stripped downstream bearer credentials. No HTTP port is
published for the service; no schema migration or worker task is required.
Nginx startup waits for recommendation health. Start the service before reloading
Nginx when upgrading an existing deployment.

Example request (use a valid account bearer token):

```json
{
  "weekly_hours": 4,
  "target_weeks": 3,
  "preferred_genres": ["1", "25"],
  "completion_goal": "main_story"
}
```

The response includes `available_hours`, up to three `recommendations`,
`empty_reason`, and ownership-scoped `coverage`. Each recommendation contains
catalog ID/title/cover, selected estimate hours, approximate weeks, personal/shared
source, matched genre IDs, zero-based saved queue position (or null), and factual
reasons including the full-budget assumption. Queued games remain eligible; no
queue/progress changes occur. Genre fit sorts before time fit and game ID.
Preferences are request-only. Supported genres are canonical IDs present in stored
shared metadata; unsupported IDs return 422. Empty genre preferences are valid.

Empty reasons are `empty_library`, `no_eligible_games` (progress, mode, resolved
match, or selected estimate unavailable), or `no_games_within_budget`. Coverage
reports owned games, verified single-player games, eligible games before budget,
and games within budget, without silently relaxing preferences or time limits.
No Steam/HLTB calls or imports from other service implementations occur.
`schemas.py` holds request validation so pure ranking does not depend on routing.

Validation: all 183 backend tests pass. Live Nginx checks confirm unauthenticated
and invalid/spoofed credentials return 401, valid authentication returns 200,
client X-User-ID is overwritten, returned games are owned and within 12 hours,
invalid preferences/unknown genres return 422, and internal health stays unrouted
(404). The real library returned three suggestions from 19 within-budget games,
with 115 owned / 82 verified single-player / 41 eligible before budgeting.
The image builds and its container health check passes. No frontend is added yet.

After live verification, the temporarily started app dependencies and new
recommendation container were stopped to restore the original runtime state.
Existing worker containers were not started and no game jobs were resubmitted.

## Recommendation UI

The existing library page now includes a responsive Pick my next game panel.
It accepts weekly hours, target weeks, optional genre checkboxes, and completion
goal, with a live calculated budget. Authenticated GET /api/v1/recommendations
returns validated stored genre choices; POST retains its recommendation contract.
No provider calls occur. If genre loading fails, time-only recommendations remain
available and an explicit Reload genres action retries the choices request.

Results show up to three covers/titles, selected estimate hours, approximate
weeks, estimate source, factual reasons, and existing Start Playing / Add to Play
Next actions. Existing queued games are labelled, actions respect current progress,
and no queue reorder/start occurs without a click. Loading, API errors, empty
library, missing eligibility, and strict-budget empty results have explicit states.
Budget assumptions are visible and constraints are never relaxed automatically.

The UI uses text nodes for server-provided titles/reasons, accessible field labels,
status/error announcements, image fallbacks, and single-column mobile results.
Requests are scoped to account generation and submission sequence; editing
preferences invalidates previous results and in-flight responses, and logout
clears cards/preferences so late responses cannot restore another session's data.
The standalone recommendations.js module reuses the app's authenticated API and
existing ownership-checked plan actions.

Validation: 184 backend tests, 12 frontend tests, and isolated Playwright desktop/
mobile checks pass. Browser checks use mocked API responses and verify preference
payloads, cards, both plan actions, mobile overflow, errors/empty states, preference
edits during requests, logout races, and no browser errors. Screenshots are saved
at /tmp/backlog-recommendations-desktop.png and /tmp/backlog-recommendations-mobile.png.
Real-account recommendation API gateway checks passed in the earlier milestone;
a new real-account end-to-end browser run remains separate QA. No new migration.

## Recommendation pagination

POST /api/v1/recommendations accepts an optional strict nonnegative integer
`offset` (default 0, maximum 1000000) and returns three ranked games starting
there. Response `pagination` includes offset, limit (3), total qualifying games,
and has_more. An offset beyond the current list returns no cards with no false
claim that no games qualify; empty_reason describes eligibility only when the
qualifying total is zero. Eligibility, genre-first/time-fit ranking, identity
checks, and personal override precedence are unchanged.

The UI adds Back / Show next 3, shows the current range and total, and disables
navigation during requests and at boundaries. Page navigation reuses submitted
preferences, not unsaved edits, and never changes Play Next. Preference edits or
logout clear pagination. Progress changes invalidate navigation and prompt a
fresh search, avoiding offset shifts after starting/finishing a suggestion.
Each request reads current library data, so background metadata changes can
alter later results; this is deterministic offset pagination, not a persisted
ranking snapshot. A fresh search always starts at offset zero.

Validation: 186 backend tests, 12 frontend tests, and mocked desktop/mobile
browser checks pass. Navigation checks cover 1–3, 4–6, a final one-game page,
Back, disabled boundaries, preserved preferences, and existing action/race tests.
No schema change or external provider calls are added.

Pagination deployed locally: rebuilt recommendation service, validated/reloaded
Nginx, and verified authenticated offsets 0 and 3 return distinct game groups
from 19 qualifying real-library games. The app remains running.

## Recommendation exclusions: free-to-play and personal hiding

Migration 010 adds nullable Steam `is_free` metadata and a per-owner
`recommendation_hidden` boolean (default false). Steam metadata accepts explicit
boolean `is_free` only; absent/malformed fields remain unknown. Recommendations
exclude verified free games by default, with strict boolean request option
`include_free_games` to opt in. Unknown status is not guessed from genre, price,
name, ownership cost, or lookup failures. The UI explains that unknown pricing
may still appear. No automatic subjective quality classification is performed.

A recommendation card's Hide from recommendations action saves a persistent
ownership preference via the library-owned, gateway-protected
PUT /api/v1/games/{game_id}/recommendation-preference with {"hidden":true}.
Requests require ownership and strict booleans; repeated identical requests are
idempotent. Hidden games are always excluded, even when including free titles.
The game remains owned and its completion status, HLTB override, cover, and Play
Next membership/order are preserved. Repeat imports do not reset the preference.
Show in recommendations on the library card restores it with {"hidden":false}.
Hiding refills the recommendations from offset zero; it does not reorder queues.

The recommendation service remains read-only; the library service owns personal
preference mutations. Coverage and page totals use the same free/hidden filters
as ranking. Metadata persistence collects free status for future successful jobs.
Existing completed records can be explicitly upgraded with the bounded command:
`docker-compose exec metadata-worker python -m metadata_worker.backfill_free --limit 100 --after-id 0`.
This uses shared Steam pacing and updates only the free flag, preserving other
metadata. It reports updated/failed counts and a cursor. Failed/unavailable Store
entries stay unknown; no HLTB catalog rerun is triggered.

Exclusions validation: all 199 backend and 12 frontend tests pass, including
migration reruns/defaults, explicit free booleans, opt-in behavior, hidden-game
exclusion even with free games included, ownership isolation, idempotent hide,
restore, repeat-sync preservation, and field-only backfill preservation. Mocked
desktop/mobile browser checks cover the opt-in payload, Hide/refill, library
restore, and existing pagination/action/race flows. Migration 010 applied locally
after backup at /tmp/backlog-before-recommendation-exclusions.dump; library,
metadata worker, and recommendation services rebuilt and Nginx reloaded.
Live gateway hide/restore verification passed and restored the test preference.

Free-status backfill completed locally: 105 records updated, zero request
failures, 30 verified free titles, and 10 unknown records. Only the free flag
was updated; no automatic HLTB rerun or removal of ownership occurred.

## Library defaults and maintenance presentation

The library grid now defaults to Not started and hides Steam-verified free games.
Show free-to-play games is an explicit opt-in independent of recommendation
preferences; unknown free status stays visible rather than being guessed. The
library API exposes nullable `is_free` in list and completion-update responses.
Filtering is client-side so ownership, saved progress, Currently Playing, and
Play Next entries remain intact. Main library totals and maintenance counts use
the free-game visibility setting. Search, game mode, sort, and progress still
combine normally. Clear filters returns to Not started with free games hidden;
signing out resets these controls for the next account.

Needs review and Missing completion data now live inside a collapsed Library
maintenance section, selected by the user during implementation. Each view keeps
its count, explanatory row, candidate review flow, and existing assessment copy.
No new migrations or provider requests are needed.

Validation: 199 backend tests, 13 frontend tests, and mocked desktop/mobile
browser checks pass. Browser assertions cover the initial Not started selection,
collapsed maintenance, review access, free-game opt-in, All games/progress
combinations, and recommendation actions/pagination. The library image was
rebuilt so the frontend receives free flags; refresh to load the static UI changes.

### Conflicting free flags and explicitly named test builds

Shared `app/catalog_filters.py` treats either explicit Steam `is_free=true` or
Steam's Free To Play genre ID 37 as free-to-play. This deliberately resolves
cases such as AltspaceVR (`is_free=false`, genre 37 present). Raw stored Steam
flags are preserved; the library response and recommendation candidates expose
the effective visibility classification. Unknown status is not guessed from
missing prices or names.

Explicit title markers Public Test, Test Server, Playtest, Open Beta, and Closed
Beta identify test builds for filtering. This is a documented title-based rule,
not verified Steam app-type metadata; generic beta/test words alone do not qualify.
These entries are excluded from recommendations and hidden by default in the
library, with a separate Show test builds opt-in. Ownership, progress, and queues
remain unchanged. No migration, provider request, or catalog rerun is needed.

Filter refinement validation: 202 backend and 14 frontend tests pass. Library
and recommendation images rebuilt and gateway reloaded; no migration needed.

## Permanent recommendation trailer integration

The approved trailer prototype is promoted to `frontend/assets/trailers/`.
Migration 011 adds optional trailer URL, thumbnail, title, and format columns to
GameMetadata. The Steam adapter selects a usable highlighted trailer, supports
validated HTTPS MP4/HLS Steam CDN URLs, ignores malformed/absent video metadata,
and retains normal metadata success when no trailer exists. The existing task
persists trailer fields on normal collection; no worker/queue is added.

Repository, ranking records, and API pass trailer fields through without altering
eligibility/scoring. Both Tiles and Trailers now use stored API data. The deck
loads the first standard recommendation page and prefetches the next near its
end. It no longer scans hundreds of pages for sample IDs. Missing-video games
stay visible with cover fallback and an unavailable message. Explicit callbacks
reuse existing play-plan actions; the experimental event bridge, static sample
JSON, and prototype folder are removed.

Videos stream directly from Steam with a locally vendored HLS player and its
license. Only the active stream is attached, prior players are destroyed on
navigation/close, tab hiding pauses playback, and logout invalidates the deck.
There is no video hosting, S3, proxy, or transcoding. The one-active-game focus
flow remains separate work. Ordinary offset pagination is not a saved snapshot.

Existing metadata is populated once using a bounded operational script outside
the repository (limit 250), shared Steam pacing, and field-only writes. No
permanent backfill function/module was added. Other metadata, ownership, progress,
queue order, covers, overrides, and HLTB outcomes are preserved.

Permanent trailer validation/deployment (October 24, 2026): all 208 backend and
15 frontend tests pass, including additive migration reruns, parser selection/
URL safety, persistence, API pass-through, and unchanged ranking for missing
videos. Mocked API browser checks exercise actual Steam playback, incremental
pages, missing-video covers, old-stream cleanup, queue actions, mobile layout,
and direct integration. Real-account Chrome verification passed stored trailer
playback, next-game navigation, and close cleanup without changing play plans.

Migration 011 applied after backup at /tmp/backlog-before-permanent-trailers.dump;
metadata worker and recommendation images rebuilt and gateway reloaded. The
one-time operational pass checked 105 collected metadata records, stored 97
usable trailers, and had zero request failures (limit 250). The remaining games
retain their normal metadata/cover behavior. No permanent backfill module exists.
The approved prototype has been removed; videos continue streaming from Steam.
