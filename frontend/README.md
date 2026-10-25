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

### Compact genre picker

Recommendation genres now use a native expandable dropdown with search,
selected checkboxes inside the menu, removable chips outside it, and Clear
selection. The closed control shows Any genre or a selection count; no genres
remains a valid time-only preference. Selection is capped at 20. Search text
filters choices without changing the request or invalidating existing results;
actual selection changes retain normal stale-result protection. Escape closes
the menu and restores summary focus, and outside clicks close it. Recommendation
tiles and API/ranking behavior are unchanged.

Validation: 12 frontend tests and mocked desktop/mobile browser checks pass,
including genre search/no matches, chip removal, keyboard Escape, preference
payloads, result actions, and stale responses. Static assets are served directly
by the running frontend; refresh the page to load the picker. No container or
backend changes are required.

Genre dropdown alignment fix: option rows use a two-column grid with 16px
checkboxes, zero inherited input/label margins, and 42px minimum row heights.
Opened-menu desktop/mobile screenshots and browser assertions verify checkbox
size, row alignment, scrolling, and no horizontal overflow. Selection, chips,
Escape dismissal, and recommendation actions still pass browser checks.

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

### Smooth recommendation page loading

Pagination keeps current cards, page range, and result summary visible during
requests. Navigation and card actions disable briefly, with a small reserved-width
Loading indicator beside the range. The results container retains its measured
minimum height across page changes, preventing collapse on short/final pages.
Cards swap together after a successful response. Failed requests preserve the
current results and range, display an inline error, and provide Retry loading
results for the requested offset. Preference edits/logout clear retained results
and height; stale responses remain rejected. No API or ranking changes.

Validation: all 12 frontend tests and mocked desktop/mobile browser checks pass,
including delayed Next requests retaining card IDs/height and disabled navigation,
failed Next preserving cards/range, explicit retry, page boundaries, plan actions,
preference edits, and logout races. Static frontend changes are available on refresh.

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

Library visibility controls are grouped into one collapsed Visibility dropdown
(free-to-play, test builds, excluded games). Existing defaults/filter behavior
remain unchanged. Escape and outside-click dismissal are supported. All 15
frontend tests pass.

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

## Play Next completion-time sorting

The Play Next section now offers Manual order, Shortest → longest, and Longest
→ shortest, plus Main story / Story + extras / Completionist estimate selection.
Sorting previews the displayed queue only; Apply order persists the exact
permutation through the existing atomic, ownership-checked queue PUT endpoint.
Returning to Manual order discards the preview. Equal estimates retain their
saved relative order; missing, invalid, or unresolved estimates stay last in both
directions. Personal resolved estimates retain their existing whole-set priority,
and a missing selected category is never substituted. Manual arrow reordering
is disabled during preview to avoid changing the wrong saved position.

No backend/migration is added. Explicit start/remove actions still work; the
currently playing game is not included or changed by sorting. Successful Apply
returns the control to Manual order; failures retain existing error/reload behavior.
Signing out resets sort controls. Sort preferences themselves are not saved.
All 17 frontend tests and mocked desktop/mobile browser checks pass, including
no PUT before Apply, stable/missing ordering, category selection, saved order,
and unchanged active games. Static assets are available on browser refresh.
