# Backlog Beater

Backlog Beater imports a public Steam library and enriches it with HowLongToBeat completion estimates and artwork.

## Architecture

```text
Next.js frontend
  ├── User service        registration, login, JWT issuance
  ├── Backlog service     user backlog and imported playtime
  └── Integration service Steam import and provider boundary
          │
          ├── Redis       HowLongToBeat match and miss cache
          └── PostgreSQL  current shared persistence
```

The repository currently uses three independently running FastAPI services. They share a database, so this is intentionally described as an early distributed architecture rather than fully autonomous microservices.

## Run locally

1. Copy `.env.example` to `.env` and provide a Steam API key.
2. Start the backend infrastructure and services:

   ```bash
   docker compose up --build
   ```

3. In `frontend`, copy `.env.example` to `.env.local`, install dependencies, and start Next.js:

   ```bash
   npm install
   npm run dev
   ```

4. Open `http://localhost:3000`, register, and import a public Steam library using its Steam ID.

Backend ports are `8001` (users), `8002` (backlog), and `8003` (integrations).

## Data semantics

- `hours_played` is the account's recorded Steam playtime.
- Steam hours are not treated as completion progress.
- Completion estimates are best-effort community data. Matches below 85% title similarity are discarded.
- Steam trademark symbols and decorative punctuation are removed only from provider search queries; hyphens and colons become spaces. Displayed titles remain unchanged.
- If an exact search misses, platform-only `Enhanced`, `Legacy`, and `Steam Edition` suffixes are removed for one fallback search. Content-significant edition names are preserved.
- A provider result may extend the complete searched title with a subtitle; the general 85% threshold still applies to unrelated candidates.

## Current tradeoffs

- Services share PostgreSQL tables and a symmetric JWT secret.
- Steam imports run synchronously and can take time for large libraries.
- HowLongToBeat has no official public API. Enrichment uses the unofficial `howlongtobeatpy` package and can break if the site changes.
- Provider failures do not block Steam imports; missing estimates remain empty.
- Successful provider results are cached for seven days under `hltb:steam:v1`; misses are cached for one hour.
- Database initialization is SQL-based rather than migration-based.
- Browser tokens are stored in local storage, which is suitable for this prototype but not the desired production session design.

## Planned evolution

1. Consolidate the backend into a modular monolith to make module boundaries explicit and add tests.
2. Add the custom metadata provider behind an explicit adapter and cache.
3. Add durable import jobs, bounded retries, idempotency keys, and failure status reporting.
4. Extract services only when they receive independent deployment and data ownership.
5. Introduce a gateway so the frontend uses one public API origin.

The integration worker is the first natural extraction because external API calls have distinct latency, rate-limit, retry, and failure characteristics.
