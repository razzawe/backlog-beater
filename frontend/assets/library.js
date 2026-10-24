export const STATUSES = ['not started', 'in progress', 'finished'];
export function formatHours(value) {
  if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) return '—';
  // Retain short estimates rather than rounding them to zero hours.
  return `${new Intl.NumberFormat('en', { maximumFractionDigits: 2 }).format(value)} h`;
}
export function summarize(games) {
  const playing = games.filter(game => game.completion_status === 'in progress').length;
  const finished = games.filter(game => game.completion_status === 'finished').length;
  return { total: games.length, playing, finished, percent: games.length ? Math.round(finished / games.length * 100) : 0 };
}
export function enrichmentAssessment(game) {
  const usable = [game.main_story_hours, game.main_extra_hours, game.completionist_hours]
    .some(value => typeof value === 'number' && Number.isFinite(value) && value > 0);
  if (game.hltb_source === 'personal') return usable ? 'resolved' : 'missing';
  if (game.hltb_review_needed || game.enrichment_status === 'ambiguous') return 'review';
  if (usable) return 'resolved';
  if (['lookup_failed', 'no_match', 'matched'].includes(game.enrichment_status) || game.hltb_id) return 'missing';
  return 'pending';
}
export function enrichmentReason(game) {
  const assessment = enrichmentAssessment(game);
  if (assessment === 'review') return 'Needs review: choose your HLTB match.';
  if (assessment === 'pending') return 'Pending: awaiting a completion-time lookup.';
  if (assessment !== 'missing') return '';
  if (game.hltb_source === 'personal') return 'Your selected match has no completion estimates.';
  if (game.enrichment_status === 'lookup_failed') return 'The last completion-time lookup failed.';
  if (game.enrichment_status === 'no_match') return 'No plausible HLTB match was found.';
  return 'The matched HLTB entry has no completion estimates.';
}
export function selectGames(games, filter, search, sort) {
  const query = search.trim().toLowerCase();
  const selected = games.filter(game => (filter === 'all' || (filter === 'review' || filter === 'missing' ? enrichmentAssessment(game) === filter : game.completion_status === filter)) && (`${game.name || ""} steam app ${game.steam_app_id}`).toLowerCase().includes(query));
  return selected.sort((a, b) => sort === 'appid' ? a.steam_app_id - b.steam_app_id : sort === 'status' ? STATUSES.indexOf(a.completion_status) - STATUSES.indexOf(b.completion_status) || b.id - a.id : b.id - a.id);
}
export function safeCoverUrl(value) {
  if (!value) return null;
  try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) ? url.href : null; } catch { return null; }
}
