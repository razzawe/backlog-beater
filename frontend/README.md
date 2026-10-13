See [architecture.md](../architecture.md) for background enrichment and API responsibilities.

# Backlog Beater frontend

A responsive dark library interface using plain HTML, CSS, and JavaScript.
No build step or frontend dependency installation is required. Nginx serves
this folder at the same origin as the API.

From the repository root, with the root `.env` configured:

```sh
docker-compose up --build -d
```

Open **http://localhost:8080**. Static source changes appear on refresh; changes
to Nginx or backend code require restarting/rebuilding the relevant containers.

## Included flows

- Register, sign in, view your account, and sign out.
- Connect Steam after account login using the backend's OpenID flow. The link
  request sets the HttpOnly browser-proof cookie; the browser visits Steam and
  returns through the public callback. Browser callbacks redirect to UI success
  or error feedback; API clients still receive JSON.
- Sync Steam with loading, insertion counts, and failure feedback.
- Sync announces committed imports for background HLTB enrichment. The notice
  reports event submission or submission failures, not game-task counts. A progress bar counts saved outcomes, including reused matches, and estimates
  refresh automatically every three seconds after submission; another sync can resubmit games whose lookups failed.
- View game cards, change completion status, and see completion statistics.
- Show main-story, main + extras, and completionist estimates in hours, with
  unavailable values shown as “—”. Matched games link to their HLTB entry;
  personal overrides are labeled “Your selected match”. Multiplayer times are
  excluded. Estimates come from stored data; background lookups run through the enrichment worker.
- Resolve ambiguous/duplicate HLTB matches through a user-triggered native
  dialog showing title, platform, year, type, estimates and source links.
  “Use this match” saves a personal override; “Decide later” or Escape closes
  the dialog without selecting. Candidate refreshes invalidate stale selections;
  close and reopen the dialog to fetch the current options. Successful selection
  refreshes the card and removes its review button. No-match and lookup-failure
  results do not trigger this modal. Shared automatic metadata stays unchanged.
  The gateway routes review and selection calls to the enrichment API; library
  reads and completion-progress updates remain in the library service.
  Review URLs use `/api/v1/enrichment/games/{id}/hltb-review` and selections
  use `/api/v1/enrichment/games/{id}/hltb-selection`.
- Search by Steam app ID, filter by completion status, and sort.
- Show onboarding, empty/filter states, session-expiry feedback, and cover fallbacks.

Tokens are kept in sessionStorage (per browser tab), never in URLs. All API
requests use the Nginx gateway. There is no separate frontend auth validation.
Steam passwords are entered only on Steam's official website.

Game names and cover URLs come from the library endpoint. Steam syncing requests
app metadata and refreshes existing catalog entries; sync again to fill older
records. Missing titles retain an app-ID fallback, and missing/broken cover
images retain a CSS placeholder. Search matches names and Steam app IDs.

## Files

- `index.html`: page structure and forms.
- `assets/styles.css`: responsive layout, colors, typography, and card styling.
- `assets/app.js`: API requests, account state, Steam linking, and interactions.
- `assets/library.js`: filtering, sorting, statistics, and cover URL handling.
- Completion-hour formatting is also provided by `assets/library.js`.

Change CSS custom properties at the start of `styles.css` to adjust colors.
Fonts come from Google Fonts, with local sans-serif fallbacks.

## Checks

```sh
node --test frontend/tests/*.test.js
node --check frontend/assets/app.js
python3 scripts/check_gateway.py
```

Browser QA covers real registration/login persistence, Steam navigation and
browser-proof cookie, callback error redirects, logout, and login errors. Card
editing, search, filtering, and sync-failure feedback were checked with mocked
API data. Full Steam sign-in and real-account import require manual completion
of the Steam login page.

Completion-time display validation: five frontend unit tests, JavaScript syntax
checks, and all 97 backend tests passed against fresh isolated PostgreSQL.
Earlier gateway and browser checks are described above. Reused test databases
with stale tables from model renames need a fresh test project or a migration.
Existing databases require `backend/migrations/003_add_hltb_enrichment.sql`
before updated backend services run; it has been applied to the local database.
Games receive estimates from queued background
enrichment tasks after syncing; successful no-match and ambiguous results do not
automatically receive estimates.

After successful sync event submission, the UI displays “Finding completion
times…” and processed/total progress for named library games. Saved matches,
ambiguous results, and no-match outcomes count as processed; previous failures
wait for a changed lookup timestamp, while new failures count as processed.
This is library coverage, not persistent job status. Games without names are
excluded because the worker cannot search them. Polling pauses in hidden tabs
and during card/review interaction, resumes across page reloads within the same
session, and stops at completion, sign-out, or after 15 minutes. API refresh
failures retry while the monitoring window remains active. Worker crashes or
unsent game tasks can leave progress incomplete; durable job recovery is pending.

HLTB review now reads shared top-three suggestions from a separate Redis cache
with a 24-hour TTL. Missing/expired entries show a loading modal while a worker
refreshes them. The modal polls for up to two minutes; closing cancels polling.
Selection submits HLTB ID plus cache version. Expired/changed versions require
reopening review. “None of these / decide later” applies nothing. Personal choices
remain in PostgreSQL and survive expiration of the shared suggestions.
