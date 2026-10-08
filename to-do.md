# Backlog Beater to-dos

## Accounts and authentication

- [x] Register and log in with hashed passwords and JWT authentication.
- [x] Retrieve the authenticated user's public account details.
- [ ] Configure a shared JWT secret for the API and sync service; reject the development default in production.
- [ ] Build registration, login, and account screens.

## Steam account linking

- [x] Start linking and verify Steam OpenID callbacks.
- [x] Protect linking with expiring state, browser proof, and replay checks.
- [ ] Build Connect Steam and callback success/error UI.
- [ ] Support unlinking and define what happens to imported ownership and progress.
- [ ] Clean up expired linking requests and old replay records safely.

## Steam library syncing

- [x] Fetch the linked user's Steam library with response validation and timeouts.
- [x] Insert shared catalog games and user ownership atomically without duplicates.
- [x] Add tests for repeat imports, concurrency, and rollback.
- [x] Use an async sync endpoint: await the Steam API, update the database, and return success with insertion counts.
- [ ] Build a Sync Library button with a loading state and success/error feedback for the request.
- [ ] Configure the Steam API key and public callback URLs.
- [ ] Verify linking, syncing, and library retrieval with a real Steam account.
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

- [x] Store an optional cover_url on each shared catalog game.
- [x] Include cover_url in library responses.
- [ ] Populate covers during import or metadata enrichment; backfill existing games.
- [ ] Add game names and decide which other metadata to retain.
- [ ] Display covers with a fallback for missing or broken images.

## Game enrichment service

- [ ] Build a separate enrichment service with Celery workers and a dedicated Redis queue.
- [ ] Integrate a community-maintained HowLongToBeat (HLTB) API wrapper.
- [ ] Match catalog games to HLTB entries and handle ambiguous matches or missing results.
- [ ] Store the matched HLTB identifier and completion-time estimates on shared catalog games.
- [ ] Queue enrichment for newly imported games and backfill existing catalog games.
- [ ] Deduplicate enrichment jobs for games shared by multiple users.
- [ ] Add rate limiting, bounded retries, timeouts, and handling for wrapper/upstream changes.
- [ ] Test matching and enrichment failures with mocked HLTB responses.

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

- [x] Return only the authenticated user's games.
- [x] Store completion_status per user and game: not started, in progress, or finished.
- [x] Default new ownership records to not started and expose status in library responses.
- [ ] Add an authenticated endpoint to update completion status on owned games.
- [ ] Build the library view and status controls.
- [ ] Add search, sorting, and completion-status filters.
- [ ] Verify repeated Steam syncs preserve personal completion status.

## Database, services, and validation

- [ ] At the end of implementation, create migrations for all accumulated schema changes, including Steam linking, cover URLs, completion status, HLTB enrichment, and S3 object keys.
- [ ] At the end of implementation, establish a versioned migration workflow and validate migrations preserve existing data; startup table creation does not alter existing tables.
- [ ] Configure startup and routing for both FastAPI services alongside PostgreSQL.
- [ ] Add Redis and the enrichment and image Celery workers to the service startup configuration.
- [ ] Configure queue routing, worker concurrency, and job-status persistence for each background workload.
- [ ] Add logging and monitoring for queue depth, job failures, and worker health.
- [ ] Document environment setup and service startup commands.
- [ ] Run the Docker test suite after schema changes; defer migration validation until migrations are created at the end.
- [ ] Add tests for completion-status updates, invalid values, and ownership access control when the update endpoint is built.
