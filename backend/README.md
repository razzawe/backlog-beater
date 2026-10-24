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
game tasks without automatic retries. A database or broker failure there fails
the background event task and is logged; it cannot alter the earlier sync response.
A later sync can resubmit eligible games. `CATALOG_BROKER_URL` configures the sync
producer; `ENRICHMENT_BROKER_URL` configures the enrichment worker.

Persistent job state, an atomic outbox, automatic task retries, queue-level
deduplication, backfill, and rate limiting remain pending. Concurrent submissions
can perform duplicate searches, but stale-result checks prevent a second lookup
from overwriting an intervening result. This is not durable exactly-once dispatch:
process failure between import commit and publication can leave games unqueued
until another sync. `inline.py` has been removed.

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
queue. Compose starts `enrichment-worker` with one process and a dedicated
`enrichment-redis` broker on a private network, without published ports.
Redis uses append-only persistence and the `enrichment_redis_data` volume;
`docker compose down` preserves that volume. The worker builds its own
`enrichment-worker/Dockerfile`, runs as a non-root user, and has database and outbound network access
for future catalog tasks. It waits for healthy PostgreSQL and Redis.

`enrichment.healthcheck` is a harmless task that logs completion.
`enrichment.enrich_game` processes one catalog game; sync publishes catalog import events
after import, and the enrichment event handler submits eligible games. Backfill, job deduplication, persistent job status, and automatic
task retries are not implemented yet. Celery results are disabled; Redis is the broker, not the future job-status
store. JSON serialization and explicit queue declarations follow the
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
