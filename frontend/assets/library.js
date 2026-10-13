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
export function selectGames(games, filter, search, sort) {
  const query = search.trim().toLowerCase();
  const selected = games.filter(game => (filter === 'all' || game.completion_status === filter) && (`${game.name || ""} steam app ${game.steam_app_id}`).toLowerCase().includes(query));
  return selected.sort((a, b) => sort === 'appid' ? a.steam_app_id - b.steam_app_id : sort === 'status' ? STATUSES.indexOf(a.completion_status) - STATUSES.indexOf(b.completion_status) || b.id - a.id : b.id - a.id);
}
export function safeCoverUrl(value) {
  if (!value) return null;
  try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) ? url.href : null; } catch { return null; }
}
