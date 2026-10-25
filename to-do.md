# Backlog Beater to-dos

## Top priority: review the Redis enrichment review flow

- [ ] Before implementing anything else, review the shared top-three HLTB suggestions flow: initial lookup, 24-hour Redis cache, refresh on expiry, and personal match selection. Follow [matching.py](backend/enrichment-service/enrichment_service/matching.py) → [worker tasks.py](backend/enrichment-worker/enrichment_worker/tasks.py) → [review_cache.py](backend/enrichment-service/enrichment_service/review_cache.py), then [api.py](backend/enrichment-service/enrichment_service/api.py) → [review_dispatch.py](backend/enrichment-service/enrichment_service/review_dispatch.py) for refresh requests, and [app.js](frontend/assets/app.js) → [storage.py](backend/enrichment-service/enrichment_service/storage.py) for selections. Review lease deduplication, cache-version validation, and how personal selections survive cache expiry without affecting other users.

## Primary next implementation

- [ ] Add persistent enrichment jobs and atomic per-game claims in `enrichment-worker/enrichment_worker/jobs.py`, backed by a shared database model and migration. Enforce one job row per catalog game; atomically claim scheduling and pending-to-running execution, pass an attempt token to each task, and verify it when saving outcomes. Keep job lifecycle separate from match outcomes. Add recovery for broker publication failures and expired running jobs; pending-job expiry must account for queue backlog. Test concurrent claims, duplicate deliveries, stale attempts, and recovery before using job state for UI progress.

Implemented job infrastructure: migration 006, per-game ready/pending/running/completed/failed
states, token-owned claims, durable pending dispatch, periodic recovery, and explicit retries.
Validated October 24, 2026: all 128 backend tests pass against fresh isolated
PostgreSQL/Redis, including concurrent claims, duplicate deliveries, publication
recovery, stale attempts, and migration 006 rerun/data-preservation/constraint checks.
Application migration/deployment and durable sync-to-import event delivery remain
separate work.

## Accounts and authentication

- [x] Register and log in with hashed passwords and JWT authentication.
- [x] Retrieve the authenticated user's public account details.
- [x] Configure the JWT secret for the auth service; reject missing secrets and the development default. Remove JWT secret configuration from library and sync when gateway identity forwarding is implemented.
- [x] Build registration, login, and account screens.
- [ ] Add "Login with Steam" as an account sign-in option using Steam OpenID: sign in to an existing linked account or create an account for a new Steam identity, issue the app JWT, and add the frontend login button. Preserve callback state, browser-proof, and replay protections; keep this distinct from linking Steam after password login.

## Steam account linking

- [x] Start linking and verify Steam OpenID callbacks.
- [x] Protect linking with expiring state, browser proof, and replay checks.
- [x] Build Connect Steam and callback success/error UI.
- [ ] Support unlinking and define what happens to imported ownership and progress.
- [ ] Clean up expired linking requests and old replay records safely.

## Steam library syncing

- [x] Add automatic retries for temporary Steam fetch failures: three retries after the initial attempt (four attempts total), then return an error. Use bounded delays and an overall time limit within the gateway timeout; preserve atomic imports and do not retry invalid responses or configuration errors.
- [x] Fetch the linked user's Steam library with response validation and timeouts.
- [x] Insert shared catalog games and user ownership atomically without duplicates.
- [x] Add tests for repeat imports, concurrency, and rollback.
- [x] Use an async sync endpoint: await the Steam API, update the database, and return success with insertion counts.
- [x] Build a Sync Library button with a loading state and success/error feedback for the request.
- [ ] Configure the Steam API key and public callback URLs.
- [x] Verify linking, syncing, and library retrieval with a real Steam account (confirmed by user).
- [ ] Show helpful errors for inaccessible/private libraries and upstream failures.
- [ ] Track last successful sync and add a sync cooldown.
- [ ] Decide how to handle games absent from later sync responses.

## Future: Steam sync background jobs

- [ ] Move Steam syncing to Celery workers backed by a dedicated Redis queue when background syncing is needed.
- [ ] Enqueue one sync task per user; allow concurrent users and prevent overlapping jobs for the same user.
- [ ] Return a job ID and expose queued, running, succeeded, and failed status to the owning user.
- [ ] Add bounded retries and timeouts while preserving atomic, duplicate-safe imports.
- [ ] Add Steam sync workers to service startup and include job schema changes in migrations if this feature is implemented.

## Game catalog and covers

- [ ] Add verified Steam app-type metadata and backfill existing catalog entries; hide confirmed tools, dedicated servers, software, and other non-game entries from library responses and statistics without deleting ownership or completion progress.
- [ ] Keep unclassified or unavailable/delisted apps visible until their type is verified; do not infer type from names or absence from the current Steam Store catalog. Cache classification and perform bounded metadata lookups outside the timed sync request.
- [ ] Add a per-user hide/unhide control for unwanted entries Steam cannot classify; preserve the hidden preference across repeated syncs.
- [x] Store an optional cover_url on each shared catalog game.
- [x] Include cover_url in library responses.
- [x] Populate Steam cover URLs during import; existing games receive metadata on their next sync.
- [x] Store Steam game names and return names/cover URLs from library endpoints.
- [x] Display covers with a fallback for missing or broken images.

## Game enrichment service

- [x] Cache shared top-three plausible HLTB review candidates per catalog game in a separate Redis instance with a 24-hour TTL; refresh missing suggestions through a token-owned deduplicated worker task, validate personal selections by HLTB ID/cache version, and remove legacy PostgreSQL review candidates with migration 004.

Architecture direction: [catalog import events](architecture.md). Sync now publishes
versioned events without importing enrichment implementation code.

- [x] Replace sync-to-enrichment implementation imports with a versioned catalog import event; remove enrichment source from the sync image and consume events in enrichment before queuing game tasks.

- [x] Host candidate review and personal-selection validation in a dedicated enrichment API/container, with gateway authentication and routing; keep library responses/progress in the library service without importing enrichment implementation code.
- [x] Remove temporary inline enrichment and submit eligible catalog IDs to Celery after committed Steam imports; report queue submission separately from lookup outcomes.
- [x] Add enrichment worker infrastructure with Celery and a dedicated private Redis broker/queue; validate startup and queued healthcheck execution. Catalog tasks are now registered alongside the queue healthcheck.
- [x] Implement a one-game background enrichment task with bounded HLTB lookup, short database transactions, stale-result checks, and preserved personal overrides.
- [ ] Add persistent enrichment job state and reliable dispatch/recovery for imports interrupted before queue submission.
- [ ] Integrate a community-maintained HowLongToBeat (HLTB) API wrapper.
- [ ] Match catalog games to HLTB entries and handle ambiguous matches or missing results.
- [x] Add a user-triggered HLTB match-review modal for ambiguous/duplicate results, with candidate details, source links, and actions to select a match or decide later. Store selections as personal overrides only; preserve them across enrichment refreshes without changing shared automatic matches or other users' estimates. Enforce ownership and reject stale selections; no-match/lookup failures do not trigger the modal.
- [ ] Future: offer an opt-in option to contribute a personal HLTB selection to a shared suggestion cache keyed by Steam app ID and HLTB ID. Show cached selections as additional candidates for other users to consider, without exposing contributor identities or automatically applying them. Validate candidate IDs and define deduplication, freshness, and invalidation rules; keep personal overrides independent of cache entries.
- [x] Add PostgreSQL storage linked to shared catalog games for HLTB identifiers, completion-time estimates, automatic match outcomes, lookup timestamps/errors, and personal candidate snapshots; store personal overrides separately with ownership checks and preserve them across refreshes. Versioned migrations remain deferred to the accumulated migration work below.
- [x] Expose effective HLTB estimates in library and completion-status responses, preferring the user's complete personal override over shared Game metadata; display the three completion times on cards with missing-value placeholders, HLTB source links, and personal-match labels.
- [x] Queue eligible games after Steam imports, including previously imported games seen on repeat syncs.
- [ ] Backfill eligible catalog games independently of Steam syncs.
- [ ] Deduplicate enrichment jobs for games shared by multiple users.
- [ ] Optional future optimization: cache successful HLTB search candidate lists in Redis with expiring keys based on the normalized search title and lookup options. Cache successful empty results for a shorter duration; never cache lookup failures as empty results. Keep accepted matches, completion estimates, personal overrides, and contributed suggestions in PostgreSQL; Redis cache expiration must not remove persisted data.
- [ ] Add rate limiting, bounded retries, timeouts, and handling for wrapper/upstream changes.
- [ ] Test matching and enrichment failures with mocked HLTB responses.

## Enrichment assessment and matching follow-up

- [x] Redesign access to Needs Review and Missing Completion Data: the user dislikes the current buttons and wants them changed. Propose an alternative navigation/presentation before implementing the redesign; retain access to both assessment views and their counts.

- [x] Clean trademark symbols and whitespace before HLTB search while preserving meaningful title wording.
- [x] Ignore explicitly typed DLC/expansion competitors only when a unique exact base-game match exists; preserve review for uncertain near titles and prefix differences.
- [x] Add Needs Review and Missing Completion Data library views with counts, reasons, search, and the existing candidate-review modal. Pending games remain distinct; usable personal estimates resolve shared ambiguity.
- [ ] Add ownership-checked retry/reassessment endpoints with duplicate protection for failed, no-match, and missing-estimate outcomes.
- [ ] After recovery support is implemented, explicitly refresh Wildlands and Breakpoint; do not automatically rerun the catalog.
- [ ] Visually verify assessment views in a connected browser (no browser surface was available during implementation).

## Recommendation

MVP goal: help a user choose an owned single-player game that fits their weekly
time budget, target completion window, preferred genres, and completion goal.
Work through the checklist in order. Example acceptance case: 4 hours/week,
3 weeks, Action + Adventure, Main story produces suggestions estimated at no
more than 12 hours, with reasons and approximate weeks to finish.

### 1. Confirm product decisions

- [ ] Confirm whether games already in Play Next should be excluded from fresh recommendations or included with a queued label. Proposed default: exclude them from Pick my next game.
- [ ] Confirm whether preferred genres are ranking preferences or mandatory filters, and whether matching either selected genre is sufficient. Proposed default: rank by the fraction of preferred genres matched; allow partial/no matches below stronger matches and explain them accurately.
- [ ] Confirm whether the target completion window is mandatory and strict. Proposed default: require it for MVP and exclude games whose selected estimate exceeds weekly hours multiplied by target weeks.
- [ ] Confirm handling of unknown single-player support. Proposed default: omit unclassified games from recommendations, retain them in the library, and explain incomplete metadata without labeling them multiplayer-only.
- [ ] Confirm the desired time-ranking behavior: favor games that fill the available window or shorter games that are easier to finish. Proposed starting rule: genre fit dominates; within similar genre fits, favor games closer to the budget without exceeding it.
- [ ] Confirm whether first-version preferences are request-only or saved to the account. Proposed default: request-only; saved defaults are a follow-up.

### 2. Add shared catalog metadata

Implemented metadata models/migration 007, Store adapter, token-owned jobs and
persistence, import integration, shared Redis pacing, periodic recovery, bounded
backfill, and worker concurrency two. Successful metadata is collected once;
failed jobs require explicit retry. All 148 backend tests and Compose validation
pass. Migrations 006/007 applied locally and worker/scheduler rebuilt; five live
Store lookups completed successfully with persisted genres and single-player
classification. Worker concurrency two confirmed. Supported genre UI, unavailable
app live QA, and automatic delayed retry policy remain separate work.

- [x] Add shared genre IDs/names, nullable single-player support (yes/no/unknown), and metadata lookup/success timestamps; create a migration that preserves existing games, ownership, progress, HLTB data, and queues.
- [x] Implement a validated Steam Store metadata adapter using `GET https://store.steampowered.com/api/appdetails?appids=<steam_app_id>`; read `data.genres` and `data.categories`, check the per-app success flag, and classify mixed single-player/multiplayer games as single-player eligible.
- [x] Treat unavailable, missing, or malformed metadata as unknown rather than multiplayer-only; never infer game mode from a name or a failed Store lookup. Use stable genre IDs internally rather than localized display labels.
- [x] Add background metadata collection after imports and a bounded backfill command for existing catalog games. Reuse the shared catalog across owners; avoid external metadata requests during Steam sync or recommendation HTTP requests.
- [ ] Add metadata request pacing, timeouts, bounded delayed retries/cooldowns for failures, reuse of successfully collected metadata without automatic refresh, and per-game deduplication so repeated imports do not repeat unnecessary Store requests. Keep this separate from HLTB provider limits.
- [ ] Verify stored metadata for representative single-player, mixed-mode, multiplayer-only, and unavailable/delisted games; expose supported genre choices to the recommendation form from stored metadata or a defined taxonomy.

### 3. Build the recommendation service

- [ ] Create `backend/recommendation_service/` with `main.py`, `api.py`, `repository.py`, `ranking.py`, and a Dockerfile. Add an internal health endpoint and document service ownership.
- [ ] Add `POST /api/v1/recommendations` accepting `weekly_hours`, `target_weeks`, `preferred_genres` (stable IDs), and `completion_goal` (`main_story`, `main_extra`, `completionist`). Validate finite positive hours, positive weeks, supported genre IDs/goals, and sensible documented upper bounds.
- [ ] Read the authenticated user's owned games, completion status, current games, queue membership, shared metadata, and effective HLTB estimates. Use the existing complete personal-override precedence; do not import library/enrichment implementation code or contact Steam/HLTB in this request.
- [ ] Filter to owned, verified single-player-capable, not-started games with a resolved HLTB match and a finite positive estimate for the selected completion goal. Apply the agreed queue rule; exclude ambiguous matches without a personal resolution. Do not substitute another completion category when the requested estimate is missing.
- [ ] Compute `available_hours = weekly_hours * target_weeks` and exclude estimates above this strict budget. Compute `estimated_weeks = estimated_hours / weekly_hours`; describe this as approximate total time assuming the full budget is dedicated to this game, not measured remaining time.
- [ ] Implement pure, deterministic scoring. Proposed initial formula, pending decision above: `genre_fit = matched preferred genre count / requested genre count`; `time_fit = estimated_hours / available_hours`; `score = 0.8 * genre_fit + 0.2 * time_fit`. Define empty-genre behavior explicitly and use a stable game-ID tie-breaker.
- [ ] Return at most three distinct games with catalog ID, title, cover, estimate hours/weeks, matched genres, estimate source, and factual reasons derived from eligibility/scoring. Return a clear empty-result reason and useful metadata-coverage information without exposing another user's data.
- [ ] Add the service to Compose and Nginx using existing verified gateway identity forwarding, internal service access, health checks, and protected routing. Recommendation service reads data; library service remains the owner of progress and queue mutations.

### 4. Build the user flow

- [ ] Add a Pick my next game form for weekly hours, target weeks, preferred genres, and completion goal; show the computed hour budget and validate inputs accessibly.
- [ ] Render up to three recommendation tiles using the existing visual design, with genre matches, estimated hours/weeks, and concise reasons. Clearly label estimates and the full-budget assumption.
- [ ] Wire Start Playing and Add to Play Next to existing library actions, preserving their ownership checks and atomic behavior. Never automatically reorder the queue or start a game just because it was recommended.
- [ ] Add loading, request-error, empty-library, missing-metadata, and no-qualifying-games states. Offer edits to time window/genres rather than silently relaxing constraints; ignore stale responses after logout or newer submissions.

### 5. Validate and finish

- [ ] Unit-test metadata parsing, unknown handling, mixed-mode eligibility, genre normalization, and cooldown/retry behavior with mocked Steam responses.
- [ ] Unit-test scoring/filtering with the 4-hour x 3-week example, exact 12-hour boundary, over-budget games, partial genre matches, deterministic ties, empty genres per agreed rules, and invalid numeric inputs.
- [ ] Integration-test authentication and ownership isolation, finished/current/queued exclusions, ambiguous matches, personal estimate precedence, missing selected-category estimates, and empty results. Verify requests perform no external provider calls.
- [ ] Verify recommendation actions through the gateway and browser on desktop/mobile, including keyboard access, errors, and data persistence after reload/sync. Run relevant backend/frontend suites and record results.
- [ ] Document API inputs/outputs, metadata backfill/startup commands, scoring choices, limitations, and deferred work. Mark the MVP complete only after a real user can import, set the example preferences, get qualifying suggestions, and start or queue a game.

### Later, outside the initial MVP

- [ ] Save account preference defaults if deferred above.
- [ ] Add Help me focus for currently playing games; define remaining-time estimation before making finish-date claims.
- [ ] Add recommendation feedback such as Not interested, Too long, and Already played elsewhere.
- [ ] Add finer preference tags (for example Cozy or Souls-like) through an additional verified metadata source; Steam Store genres are broad categories, not community tags.

## Image processing and S3 storage

- [ ] Build an image worker with Celery and a dedicated Redis queue.
- [ ] Queue image processing when import or enrichment discovers a game cover.
- [ ] Download images with URL validation, timeouts, content-type checks, and size limits.
- [ ] Upload downloaded images to S3 and save the S3 object key in the game database record after a successful upload.
- [ ] Configure the S3 bucket, region, and worker credentials.
- [ ] Define how library responses resolve stored object keys into displayable cover URLs.
- [ ] Make image jobs safe to retry and handle failed downloads, uploads, and database writes.
- [ ] Backfill existing covers and define cleanup for replaced or orphaned S3 objects.
- [ ] Test image download, upload, and object-key persistence with mocked external services.

## Personal library and completion tracking

### Currently playing and play-next queue

Functional goal: users can choose the single-player games they are currently playing and maintain a personal, ordered queue of games to play next.

- [x] Let users select multiple owned single-player games for a dedicated Currently Playing view; reuse the existing in-progress completion status where possible.
- [x] Let users add owned games to a personal Play Next queue, view its saved order, reorder entries, and remove entries without removing games from their library.
- [x] Let users start a queued game: mark it as currently playing and remove it from Play Next. Do not automatically start another game when one is finished.
- [x] Prevent duplicate entries in a user's queue; keep each user's current games and queue independent, persistent across sessions, and preserved through Steam syncs.
- [ ] Decide how single-player eligibility is identified; allow manual selection while verified game-mode metadata is unavailable.
- [x] Verify ownership checks, user isolation, saved queue order, and transitions between queued, currently playing, and finished games. Validated October 23, 2026: 118 backend tests and eight frontend tests pass; migration 005 applied locally and library service rebuilt. Visual browser QA remains pending.

- [x] Return only the authenticated user's games.
- [x] Store completion_status per user and game: not started, in progress, or finished.
- [x] Default new ownership records to not started and expose status in library responses.
- [x] Add an authenticated endpoint to update completion status on owned games.
- [x] Build the library view and status controls.
- [x] Add search, sorting, and completion-status filters.
- [x] Verify repeated Steam syncs preserve personal completion status.

## API gateway (Nginx Open Source)

### Containers and networking

- [x] Split accounts and Steam linking into an auth service and game endpoints into a library service; provide separate Dockerfiles for auth, library, and sync.
  - All three service images built successfully; startup commands are documented in backend/README.md. Reserved a separate Docker IP allocation range to prevent conflicts with Nginx's fixed address.
  - All 40 backend tests pass in Docker against isolated PostgreSQL, including service boundaries, token validation, ownership, concurrent imports, rollback, and preserved completion status.
- [x] Add the auth and library services, sync service, and Nginx to Docker Compose alongside PostgreSQL on an internal network.
- [x] Expose Nginx at localhost:8080 and keep backend HTTP ports internal.
- [x] Configure database health checks and service startup dependencies.

### Environment configuration

- [x] Configure the database URL for all three API services and the JWT secret for auth; library and sync will use gateway-provided identity instead of JWT validation.
- [x] Configure the Steam API key, APP_PUBLIC_URL, and STEAM_CALLBACK_URL to use the gateway origin.
- [x] Update .env.example with gateway configuration placeholders without real secrets.

### Request routing

- [x] Add an exact Nginx route for /api/v1/steam/sync to the sync service.
- [x] Route /api/v1/auth/*, /api/v1/steam/link, and /api/v1/steam/callback to auth, and /api/v1/games routes to library, without rewriting paths or query strings.
- [x] Serve the frontend at / and static assets through Nginx; keep API/internal routing isolated.

### Headers, cookies, and timeouts

- [x] Forward bearer credentials only to the auth check, strip them from protected downstream requests, and preserve request cookies and backend Set-Cookie headers.
- [x] Set forwarded host, protocol, and client-IP headers and configure FastAPI to trust the intended proxy.
- [x] Configure a proxy timeout suitable for the current Steam sync request flow and preserve backend error responses.

### Gateway authentication

- [x] Remove unused bearer parsing dependencies, share Nginx proxy settings, use verified IDs directly in library routes, and remove redundant persisted-user ID checks. All 40 backend tests and five live gateway tests pass after cleanup.

- [x] Add an internal auth-service check endpoint using existing JWT validation and user lookup; return the verified user ID in a response header for Nginx.
- [x] Use Nginx auth_request to authenticate protected API and sync requests before forwarding them.
- [x] Keep registration, login, and the Steam callback accessible without a bearer token; preserve the callback's state and browser-proof checks.
- [x] Have Nginx overwrite client-supplied X-User-ID with the verified user ID from the auth subrequest, and omit bearer credentials when forwarding to library and sync.
- [x] Replace JWT validation in library and sync with gateway identity parsing and database user lookup as part of the gateway integration; reject missing, malformed, or unknown user IDs and keep ownership checks.
- [x] Restrict library and sync access to the trusted gateway network; do not publish their HTTP ports or expose the internal auth check through public Nginx routes.
- [x] Apply the same verified identity flow to protected auth-service routes so token validation runs once per protected request; preserve independent Steam callback validation.
- [x] Verify invalid, expired, and missing tokens are rejected at the gateway, and authentication-service failures deny access.
- [x] Verify spoofed X-User-ID headers cannot impersonate another user, ownership checks still hold, and downstream endpoints do not revalidate JWTs.

### Integration validation

- Nginx configuration validation and all five live gateway tests pass. Auth outage denies access with HTTP 500; auth was restored afterward. A correctly signed expired JWT is rejected with HTTP 401.

- [x] Verify registration, login, and account retrieval through Nginx.
- [ ] Verify library retrieval and completion-status updates through Nginx.
- [x] Verify Steam sync requests reach the sync service through Nginx.
- [x] Verify Steam linking cookies and public callback routing through the gateway; real Steam callback success remains a separate check.
- [x] Verify gateway behavior when a backend service is unavailable.
- [x] Run the existing Docker backend test suite after gateway integration.

### Documentation

- [x] Document environment setup, startup, shutdown, logs, gateway URL, and Steam callback configuration.
- [x] Document handling of existing database schemas; startup table creation does not migrate existing tables.

### Future frontend and production setup

- [x] Follow-up: route or serve the frontend through Nginx once the frontend setup is chosen.
- [ ] Follow-up: configure a public domain and HTTPS when production hosting is chosen.

## Database, services, and validation

- [ ] At the end of implementation, create migrations for all accumulated schema changes, including Steam linking, cover URLs, completion status, HLTB enrichment, and S3 object keys.
- [ ] At the end of implementation, establish a versioned migration workflow and validate migrations preserve existing data; startup table creation does not alter existing tables.
- [ ] Configure startup and routing for auth, library, and sync services alongside PostgreSQL.
- [ ] Add Redis and the enrichment and image Celery workers to the service startup configuration.
- [ ] Configure queue routing, worker concurrency, and job-status persistence for each background workload.
- [ ] Add logging and monitoring for queue depth, job failures, and worker health.
- [ ] Document environment setup and service startup commands.
- [ ] Run the Docker test suite after schema changes; defer migration validation until migrations are created at the end.
- [x] Add tests for completion-status updates, invalid values, and ownership access control when the update endpoint is built.
  - Tests also cover repeated Steam sync preserving updated status; all 40 Docker backend tests pass.

## Frontend validation

- [x] Show post-sync enrichment feedback, saved-outcome progress, and automatic library updates every three seconds; pause during interactions and stop when complete or after a bounded monitoring window. Persistent job-status tracking remains separate work.

- [x] Add a responsive dark library UI in frontend/ using existing APIs, with account login followed by Steam linking.
- [x] Verify browser account flows, Steam redirect/cookie setup, callback errors, responsive layout, and mocked library interactions; three frontend unit tests, five gateway checks, and all 42 backend tests pass against a fresh isolated test database.
- [x] Complete real Steam browser sign-in and verify library import and status updates through the UI (confirmed by user).

## HLTB pacing and retry follow-up

- [x] Add one shared HLTB lookup lease, three-second start spacing, and safe
  countdown deferral for enrichment and review refreshes.
- [x] Add migration 008 for HLTB attempt counts/due times; retry identifiable
  temporary failures after at least one/five minutes, three total attempts.
- [x] Preserve terminal no-match/ambiguous results, respect exposed Retry-After,
  use shared cooldowns, and reset the retry budget only on manual retry.
- [ ] Broaden throttling detection if the wrapper can expose currently hidden
  HTTP status/headers; do not classify opaque or malformed results as transient.

HLTB limiter validated: 154 backend tests pass; migration 008 applied locally and
worker/scheduler rebuilt. Existing failed jobs were not reset or rerun.

## Separate metadata queue and worker

- [x] Declare `steam_metadata` and route metadata collection/maintenance to it.
- [x] Add a dedicated metadata worker using the existing image (configuration only; not started).
- [x] Set HLTB worker concurrency to one; remove the active gate and busy deferrals. Short spacing waits locally; long provider cooldowns defer without consuming attempts.
- [x] Deploy the complete split locally: all images rebuilt, database/Redis reset, gateway HTTP 200, both workers healthy with correct queues and concurrency one. Fresh user sync remains the live task check.

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

- [x] Collect validated Steam `header_image` URLs with metadata; replace only
  missing/legacy fallback covers and preserve saved covers across repeated syncs.
  All 159 backend tests pass; no schema migration or automatic metadata rerun.

- [x] Add game-mode filters with verified single/multiplayer flags, mixed-mode
  support, unknown handling, combined search/progress, and result counts.

Game-mode filters deployed: 162 backend/10 frontend tests pass; migration 009
applied; 105 existing records filled, 10 unknown. Visual browser QA pending.

## Barebones recommendation service milestones

- [x] Step 1: service package, internal health endpoint, and validated request
  model (hours/week, target weeks, genre IDs, completion goal).
- [x] Step 2: pure eligibility/ranking with strict budget and queued games included; deterministic top three and factual reasons.
- [x] Step 3: ownership-scoped repository with complete personal HLTB override precedence, mode/genre metadata, and user-specific Play Next positions.
- [x] Step 4: authenticated endpoint, response reasons/coverage, Dockerfile, Compose/Nginx. All 183 backend tests and live auth/ownership/budget gateway checks pass.
- [x] Recommendation frontend: preference form, genre choices, budget, cards, plan actions, loading/error/empty states, and stale-response protection. 184 backend/12 frontend tests and mocked desktop/mobile browser checks pass.
- [ ] Later: real-account browser acceptance, history-based interests and user feedback.

Play Next is included in the agreed barebones MVP and its saved order is never
mutated by recommendations. Earlier exclusion suggestions are superseded.

- [x] Replace recommendation genre checkbox wall with searchable dropdown,
  removable chips, Clear selection, and keyboard/outside-click dismissal.

- [x] Add recommendation Back/Show next 3 navigation, page ranges, and validated
  offsets; 186 backend/12 frontend tests and mocked browser pagination checks pass.

- [x] Smooth recommendation pagination: retain cards/height during loading,
  stable inline loading state, and preserve results with Retry on page errors.

- [x] Exclude Steam-verified free games by default (optional opt-in), and add
  persistent per-user Hide/Show in recommendations without removing ownership,
  progress, overrides, or queue entries. Add migration 010 and field-only backfill.

- [x] Default library to Not started, hide verified free games with an opt-in,
  and replace prominent assessment tabs with collapsed Library maintenance.

- [x] Resolve conflicting Steam free flags using explicit genre 37, and hide
  explicitly named test builds by default with a library opt-in. This title rule
  does not replace future verified Steam app-type classification.

Catalog classification constraint: no hardcoded per-game bans or app-ID overrides.
Use generic metadata rules; personal exclusions remain user-controlled.

- [x] Promote trailers to stored metadata/API fields, ordinary paginated deck,
  direct app callbacks, and missing-video fallbacks; remove static prototype.
- [ ] Future: trailer URL freshness/recovery policy if Steam changes assets;
  keep video delivery direct and the single-active-game focus flow separate.

- [x] Add Play Next shortest/longest previews by completion goal, stable ties,
  missing estimates last, and explicit Apply order via the existing queue API.

- [x] Move recommendation discovery into a modal opened by a visible card above Play Next; shorten the signed-in hero and align recommendation preference inputs.

- [x] Add a successful-completion celebration with cover, gold checkmark, finished-game milestone, brief confetti, and reduced-motion support.
- [x] Add a dedicated Finish game action on Currently Playing and defer page updates until the completion celebration is dismissed.
- [x] Add active-game actions to return to the end of Play Next or stop playing without queuing.
