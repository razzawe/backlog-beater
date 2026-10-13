// Progress describes saved library outcomes, not Redis queue/job state.
export function enrichmentProgress(games, previousFailures = {}) {
  const supported = games.filter(game => game.name?.trim());
  let processed = 0, failed = 0;
  for (const game of supported) {
    if (['matched', 'ambiguous', 'no_match'].includes(game.enrichment_status)
        || game.hltb_source === 'shared') processed++;
    else if (game.enrichment_status === 'lookup_failed'
        && game.enrichment_last_lookup_at
        && game.enrichment_last_lookup_at !== previousFailures[game.id]) {
      processed++; failed++;
    }
  }
  return { processed, total: supported.length, failed };
}
