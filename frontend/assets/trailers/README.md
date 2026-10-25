# Recommendation trailers

`player.js` mounts tile previews, shared lazy HLS loading, and the deck using
explicit app callbacks. `deck.js` uses the normal authenticated recommendation
API, fetching one page initially and prefetching the next near the end. There is
no sample JSON, page-scanning filter, custom event bridge, or experimental import.

All qualifying games remain in the deck. Missing trailers display cover art and
an unavailable message; ranking and eligibility do not depend on video coverage.
Only the active stream is attached. Navigation and close destroy the old HLS
instance; hiding the tab pauses playback. Keyboard/button/scroll navigation and
muted initial playback remain supported. Videos stream directly from Steam.

`hls.min.js` is vendored hls.js; its Apache-2.0 license is in `HLS-LICENSE`.
Steam URLs are validated during ingestion and checked again in the player.
No S3, video proxy, transcoding, worker, or queue has been added.
