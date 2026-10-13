# Backlog Beater — project accomplishments and resume points

Last updated: October 11, 2026.

Use this as a running inventory of work you can discuss in resumes, portfolios,
and interviews. Checked items are implemented; unchecked items are opportunities
for future accomplishments. Keep implementation tasks in [to-do.md](to-do.md).
See [architecture.md](architecture.md) for implemented and planned service boundaries.

When adding an accomplishment, record what changed, why it matters, and evidence
such as a test, source file, or measured result. Choose 3–5 bullets for a resume
and tailor them to the role. Only claim performance improvements, user counts,
or production outcomes after measuring them.

## Project overview

- Built a full-stack Steam backlog tracker with account management, library imports, and per-user completion tracking.
- Organized the backend into independently containerized authentication, library, Steam sync, and enrichment APIs behind an Nginx gateway.
- Created a responsive browser interface for registration, login, Steam linking, library syncing, and progress management.
- Current stack: Python, FastAPI, Pydantic, SQLModel, SQLAlchemy, PostgreSQL, HTTPX, PyJWT, Argon2, Celery, Redis, Nginx, Docker Compose, JavaScript, HTML, and CSS.
- Current scope: local development and automated validation. Real-account Steam linking/import/UI validation was confirmed by the user; production deployment remains pending.

## Backend design and APIs

- [x] Separate authentication, library, and synchronization responsibilities into separate FastAPI applications, with a dedicated enrichment API and separately packaged worker.
- [x] Preserve a consistent `/api/v1` interface while routing requests to separate services.
- [x] Define typed request and response models with Pydantic.
- [x] Validate registration fields and completion-status updates before processing requests.
- [x] Return explicit HTTP errors for invalid credentials, duplicate usernames, unlinked Steam accounts, inaccessible ownership records, and upstream failures.
- [x] Share database models and identity helpers without duplicating application endpoints.
- [x] Separate Steam fetching, database import operations, and transaction management into focused functions.

Resume-ready options:

- Developed four containerized FastAPI services for authentication, game-library management, and Steam synchronization behind a unified API gateway.
- Designed typed REST endpoints for account management and per-user game progress, enforcing request validation and ownership access controls.

Evidence: [auth service](backend/auth_service/), [library service](backend/library_service/), [sync service](backend/sync_service/).

## Authentication and access control

- [x] Hash account passwords using Argon2 through pwdlib.
- [x] Issue signed JWT access tokens with user identifiers and one-hour expiration.
- [x] Validate token signatures, required claims, expiration, and account existence in the auth service.
- [x] Reject missing JWT secrets and the former development default.
- [x] Centralize protected-request authentication using Nginx `auth_request` subrequests.
- [x] Forward verified user IDs while overwriting client-supplied identity headers.
- [x] Strip bearer credentials before forwarding protected requests to application endpoints.
- [x] Remove redundant downstream JWT validation; resolve account records only where needed.
- [x] Query library ownership using verified user IDs without an additional user lookup.
- [x] Deny protected requests when the authentication service is unavailable.
- [x] Keep game ownership authorization inside the library service.

Resume-ready options:

- Implemented centralized JWT authentication with Nginx subrequests and verified identity forwarding, preventing client header impersonation without repeated token validation.
- Enforced per-user library access and progress updates using database ownership checks, with automated coverage for cross-user access attempts.

Evidence: [security.py](backend/app/core/security.py), [identity.py](backend/app/core/identity.py), [internal auth endpoint](backend/auth_service/internal.py), [gateway configuration](gateway/).

## Steam integration and account linking

- [x] Integrate Steam OpenID account linking after Backlog Beater account login.
- [x] Send users to Steam's official sign-in page instead of collecting Steam passwords.
- [x] Bind linking attempts to an authenticated local account using expiring random state.
- [x] Verify browser continuity with an HttpOnly cookie and hashed browser-proof token.
- [x] Verify Steam assertions with the fixed OpenID provider endpoint.
- [x] Validate signed identity fields, callback URLs, nonce timestamps, and Steam identifiers.
- [x] Prevent reused linking attempts through consumed-request tracking.
- [x] Record unique nonce hashes to reject reused Steam login responses.
- [x] Use row locks and database constraints when consuming linking requests and associating Steam accounts.
- [x] Redirect browser callbacks to frontend success/error feedback while preserving JSON responses for API clients.
- [ ] Complete successful real-account linking and import validation in the browser.

Resume-ready options:

- Integrated Steam OpenID account linking with expiring state, HttpOnly browser-proof cookies, provider verification, and nonce-based replay protection.
- Built browser callback handling that returns users to the application while preserving the API's JSON response contract.

Evidence: [Steam linking](backend/auth_service/steam.py), [Steam linking tests](backend/tests/test_steam.py).

## Synchronization, transactions, and concurrency

- [x] Fetch Steam libraries using asynchronous HTTP requests with bounded timeouts.
- [x] Validate returned game identifiers and verify library counts before database writes.
- [x] Deduplicate and consistently order catalog insert batches.
- [x] Use PostgreSQL conflict handling to avoid duplicate catalog and ownership records during repeated or concurrent imports.
- [x] Commit game catalog and ownership inserts in one transaction; roll back the entire import on failure.
- [x] Preserve existing completion statuses during repeated synchronization.
- [x] Run synchronous database work in a worker thread so it does not block the async event loop.
- [x] Report library size and newly inserted catalog/ownership counts to clients.
- [x] Return distinct upstream timeout and unavailable/invalid-response errors.
- [ ] Add three retries after the initial Steam fetch attempt, with bounded delays and a total time limit.
- [ ] Track last successful sync, introduce cooldowns, and prevent overlapping user syncs.

Resume-ready options:

- Built duplicate-safe Steam library imports using PostgreSQL conflict handling and atomic transactions, preserving user progress across repeated synchronization.
- Combined asynchronous Steam API calls with threaded database operations to keep blocking persistence work off the request event loop.
- Tested concurrent imports and simulated transaction failures to verify duplicate prevention and rollback behavior.

Evidence: [Steam fetching/imports](backend/sync_service/steam.py), [sync endpoint](backend/sync_service/main.py), [sync tests](backend/tests/test_sync.py).

## Enrichment architecture and background processing

- [x] Separate user-facing HLTB review/selection endpoints from background enrichment execution.
- [x] Run a Celery worker with a dedicated private Redis broker/queue, persistent broker storage, and one processing slot.
- [x] Process one shared catalog game per task using bounded HLTB lookups, conservative matching, and persisted outcomes.
- [x] Close database sessions during provider requests, then lock and recheck the game before saving to reject stale results.
- [x] Reuse persisted matches across owners while preserving independent personal overrides and completion progress.
- [x] Preserve committed Steam imports when best-effort queue submission fails.
- [x] Replace sync's enrichment code import with a versioned catalog import event published by an independent messaging client.
- [x] Consume import events in enrichment and keep eligibility and task submission entirely within enrichment.
- [ ] Add atomic job claims, persistent job status, duplicate prevention, retries, stuck-job recovery, and durable dispatch.

Sync and enrichment now share a versioned Celery message contract rather than
service implementation imports. They still share PostgreSQL and model code.
Dispatch is best effort; job claims, recovery, and automatic retries are pending.

Resume-ready options for implemented work:

- Implemented Celery/Redis background game enrichment with shared PostgreSQL results, bounded provider lookups, and stale-result checks that preserve personal match selections.
- Separated HLTB review APIs from background lookup workers, validating persistence and import failure isolation with mocked provider responses.

Additional implemented architecture accomplishment:

- Implemented a versioned catalog import event boundary so sync announces committed imports while enrichment owns eligibility, job claims, and background processing.

Evidence: [architecture](architecture.md), [worker](backend/enrichment-worker/enrichment_worker/worker.py),
[tasks](backend/enrichment-worker/enrichment_worker/tasks.py),
[submission](backend/enrichment-worker/enrichment_worker/tasks.py),
[persistence tests](backend/tests/test_enrichment_storage.py), [Compose](compose.yml).
Latest recorded validation (October 11, 2026): 103 backend tests and five frontend
tests passed. The independent sync image contains no enrichment source; a live
catalog event published by sync was consumed by enrichment and scheduled zero
tasks for a missing app ID. Consumer scheduling/eligibility and provider lookups
were tested with mocks. Future job deduplication remains pending.

## Database modeling

- [x] Separate shared game catalog records from user-specific ownership and completion progress.
- [x] Model many-to-many ownership with a composite user/game primary key.
- [x] Enforce unique usernames, linked Steam IDs, and Steam app IDs.
- [x] Use foreign keys to maintain ownership and account-link relationships.
- [x] Restrict completion status with enum validation, a database check constraint, and non-null defaults.
- [x] Store optional cover URLs shared by owners of the same catalog game.
- [x] Store linking expiration and consumption timestamps using timezone-aware database columns.
- [ ] Establish a versioned migration workflow and verify preservation of existing data.

Resume-ready option:

- Designed a PostgreSQL schema separating shared game metadata from per-user progress, using composite keys, foreign keys, uniqueness constraints, and validated completion states.

Evidence: [database models](backend/app/database.py).

## Frontend and user experience

- [x] Build a responsive dark game-library interface in HTML, CSS, and modular JavaScript.
- [x] Implement registration, login, account details, logout, and per-tab session restoration.
- [x] Connect UI requests to the existing gateway and backend APIs.
- [x] Implement Steam connection onboarding and callback success/error feedback.
- [x] Show synchronization loading states, insertion counts, and errors.
- [x] Render library cards with completion controls and Steam store links.
- [x] Support completion filters, client-side search by Steam app ID, and sorting.
- [x] Calculate library totals, currently playing/finished counts, and completion percentages.
- [x] Provide onboarding, empty-library, and no-match states.
- [x] Handle missing/broken cover images with CSS-generated placeholders.
- [x] Use labels, keyboard focus styles, and status announcements for accessible interactions.
- [x] Keep JWTs out of URLs and render API-derived text using safe DOM text operations.
- [x] Serve frontend and API from one origin through Nginx, keeping Steam cookie handling on that origin.
- [x] Import Steam names and persist cover URLs; library endpoints supply card metadata and subsequent syncs refresh existing records.

Resume-ready options:

- Developed a responsive game-library dashboard with account onboarding, Steam linking, completion tracking, search, filters, and aggregate progress statistics.
- Integrated frontend account and sync workflows with gateway-protected APIs, including session-expiry, loading, callback, and failure feedback.

Evidence: [frontend source](frontend/), [frontend documentation](frontend/README.md).

## Containers, networking, and gateway configuration

- [x] Create separate Dockerfiles for auth, library, and sync, each with its own entry point.
- [x] Run application containers under a non-root user.
- [x] Cache dependency installation independently of application source changes.
- [x] Exclude environment secrets and development artifacts from Docker build contexts.
- [x] Orchestrate application services, PostgreSQL, and Nginx with Docker Compose.
- [x] Publish only the local gateway port; keep backend and database ports private.
- [x] Configure private service/database networks and separate outbound networks for Steam access.
- [x] Add health checks and order service startup around database and auth readiness.
- [x] Reserve an IP range for automatic allocation to avoid conflicts with Nginx's fixed proxy address.
- [x] Configure trusted forwarded headers, proxy timeouts, cookie forwarding, and hidden internal routes.
- [x] Share common proxy settings across public and protected routes.
- [x] Document local startup, shutdown, environment configuration, and schema limitations.
- [ ] Deploy behind a public domain with HTTPS and production configuration.

Resume-ready option:

- Containerized a multi-service application with Docker Compose, configuring private networking, health-based startup dependencies, and an Nginx gateway serving frontend assets and protected APIs.

Evidence: [Compose stack](compose.yml), [gateway](gateway/), [backend setup](backend/README.md).

## Testing and validation

Last recorded successful checks: **114 backend tests**, **5 live gateway tests**,
and **8 frontend unit tests**, plus desktop/mobile browser QA and Nginx syntax
validation. Update these counts when the suite changes; they are test counts,
not performance or adoption metrics.

- [x] Run backend integration tests against isolated PostgreSQL in Docker.
- [x] Test ownership isolation, invalid completion values, and independent progress for shared games.
- [x] Test repeat imports, concurrent inserts, rollback, and preserved completion status.
- [x] Mock Steam HTTP responses to exercise successful linking and upstream failures.
- [x] Test expired linking state, missing/wrong cookies, duplicate parameters, duplicate Steam associations, and replayed nonces.
- [x] Verify token rejection, identity forwarding, and service route boundaries.
- [x] Verify downstream endpoints do not decode JWTs again.
- [x] Exercise live gateway registration/login, spoofed identity headers, routing, cookies, and hidden internal endpoints.
- [x] Verify that an auth outage denies access and correctly signed expired tokens are rejected.
- [x] Unit-test frontend filtering, sorting, statistics, and cover URL handling.
- [x] Use headless Chrome to check real browser registration/session flows and Steam navigation/cookie setup.
- [x] Check library interactions and sync-failure feedback in the browser with mocked API data.
- [x] Check desktop/mobile layouts and absence of browser JavaScript errors.
- [ ] Verify full Steam sign-in and successful real-account imports through the frontend.

Resume-ready option:

- Validated authentication, ownership isolation, transactional imports, and callback replay protection with 103 backend tests, five live gateway checks, and desktop/mobile browser QA.

Evidence: [backend tests](backend/tests/), [live gateway checks](scripts/check_gateway.py), [frontend unit tests](frontend/tests/).

## Future accomplishments — promote only after implementation

- [ ] Celery/Redis background jobs with dedicated queues, bounded retries, job status, and per-user concurrency controls.
- [x] HLTB matching and shared completion-time metadata enrichment.
- [ ] Image workers with validated downloads, S3 uploads, stored object keys, and retry-safe processing.
- [ ] Game recommendations based on the user's backlog and progress.
- [ ] Versioned migrations tested against existing data.
- [ ] Production deployment, HTTPS, logging, and operational monitoring.
- [ ] Measured import latency and throughput, with recorded dataset size and environment.
- [ ] Measured query improvements using execution plans and before/after results.

## Accomplishment entry template

Copy this when recording a new piece of work:

```markdown
### Accomplishment title
- Status: planned / implemented / verified
- Built or changed:
- Problem solved:
- Technologies:
- Evidence: source, test, screenshot, or measured result
- Resume bullet:
- Verification date:
- Remaining limits:
```

### Live enrichment flow validation — October 11, 2026
- Verified the separated sync producer and worker through Redis with a real HLTB lookup for Red Faction; saved the shared match and confirmed a repeat event queued no additional lookups.
- Restarted the Compose stack preserving volumes; service health checks, Nginx syntax validation, and five gateway tests passed.
- Scope: one existing catalog game, without a new Steam fetch; this is validation evidence, not a throughput benchmark.

### Shared HLTB review cache — October 11, 2026
- Implemented shared HLTB review suggestions only for ambiguous games with candidates in a separate Redis cache: up to three ranked plausible titles, deduplicated by HLTB ID, with a 24-hour TTL. Automatic matches and no-match outcomes remain in PostgreSQL; resolved lookups remove older shortlists.
- Added token-owned refresh claims, duplicate-delivery protection, stale-worker fencing, and refresh-on-expiry through the separately deployed Celery worker.
- Validated selections by cached version and candidate ID; personal snapshots remain durable in PostgreSQL and independent across owners.
- Removed PostgreSQL's legacy suggestion column with an idempotent migration. Local migration fingerprints preserved all 115 catalog and 115 ownership/progress records; personal selection data was unchanged.
- Evidence: 113 backend tests, eight frontend tests, five live gateway checks, Chrome modal validation, and a real cached review refresh for Half-Life 2: Deathmatch. Repeat review reused the same cache version.
- Follow-up validation: all 114 backend tests passed after restricting suggestion caching to ambiguous games; regression coverage verifies matched/no-match results clear older shortlists. Rebuilt the enrichment API and worker containers with this change.
- Limits: similarity ranking is a heuristic, general persistent job recovery remains pending, and a shortlist may omit the correct match.
