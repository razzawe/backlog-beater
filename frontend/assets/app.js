import { celebrateCompletion } from './celebration.js';
import { setCoverImage } from './covers.js';
import { mountTrailers } from './trailers/player.js';
import { mountRecommendations } from './recommendations.js';
import { enrichmentProgress } from './enrichment.js';
import { STATUSES, summarize, selectGames, sortPlayQueue, safeCoverUrl, formatHours, enrichmentAssessment, enrichmentReason } from './library.js';
const $ = id => document.getElementById(id);
const state = { token: sessionStorage.getItem('backlog-token'), user: null, games: [], queue: [], planBusy: false, filter: 'not started', mode: 'login', generation: 0, loading: false };
const recommendationUI = mountRecommendations({ api, getState: () => state, startPlaying, hideRecommendation: game => setRecommendationHidden(game, true), addToQueue: id => changeQueue('/games/queue/' + id, 'POST') });
mountTrailers({api,getState:()=>state,performAction:async(action,id)=>{
 const game=state.games.find(g=>g.id===id);if(!game)return {ok:false};
 if(action==='start')await startPlaying(game);
 else if(action==='queue')await changeQueue('/games/queue/'+id,'POST');
 else if(action==='exclude')await setRecommendationHidden(game,true);
 return {ok:action==='start'?game.completion_status==='in progress':action==='queue'?state.queue.includes(id):game.recommendation_hidden===true};
}});
const labels = { 'not started': 'Not started', 'in progress': 'Playing', finished: 'Finished' };
function notice(message, error = false) { $('notice').textContent = message; $('notice').classList.toggle('error', error); $('notice').hidden = !message; }
let enrichmentTimer;
let enrichmentRun;
function stopEnrichment() {
  clearTimeout(enrichmentTimer); enrichmentRun = null;
  sessionStorage.removeItem('backlog-enrichment');
}
function showEnrichment(run) {
  const progress = enrichmentProgress(state.games, run.previousFailures);
  $('enrichment-progress').hidden = false;
  $('enrichment-bar').max = Math.max(1, progress.total);
  $('enrichment-bar').value = progress.processed;
  const done = progress.processed === progress.total;
  $('enrichment-message').textContent = done
    ? `Completion lookups finished. ${progress.processed} of ${progress.total} games processed.${progress.failed ? ` ${progress.failed} lookups could not finish.` : ''}`
    : `Finding completion times… ${progress.processed} of ${progress.total} games processed.`;
  return done;
}
function startEnrichment(previousFailures, started = Date.now()) {
  stopEnrichment();
  const run = { userId: state.user.id, previousFailures, started, generation: state.generation };
  enrichmentRun = run;
  sessionStorage.setItem('backlog-enrichment', JSON.stringify(run));
  if (showEnrichment(run)) { stopEnrichment(); return; }
  async function poll() {
    if (enrichmentRun !== run || state.generation !== run.generation) return;
    if (Date.now() - run.started > 15 * 60 * 1000) {
      $('enrichment-message').textContent = 'Completion lookups are taking longer. Automatic updates paused; refresh later to check results.';
      stopEnrichment(); return;
    }
    try {
      // Do not replace controls during a status edit, sync, or match review.
      if (!document.hidden && !state.loading && !$('hltb-review').open
          && !$('library-view').contains(document.activeElement)) {
        const games = await api('/games');
        if (enrichmentRun !== run || state.generation !== run.generation) return;
        if (state.loading || $('hltb-review').open || $('library-view').contains(document.activeElement)) {
          enrichmentTimer = setTimeout(poll, 3000); return;
        }
        const old = new Map(state.games.map(game => [game.id, game]));
        state.games = games.map(game => Object.assign(old.get(game.id) || {}, game));
        renderLibrary();
        if (showEnrichment(run)) { stopEnrichment(); return; }
      }
    } catch (error) {
      if (enrichmentRun !== run) return;
      $('enrichment-message').textContent = 'Could not refresh completion times. Retrying automatically…';
    }
    if (enrichmentRun === run) enrichmentTimer = setTimeout(poll, 3000);
  }
  enrichmentTimer = setTimeout(poll, 3000);
}
function view(name) {
  $('library-view').hidden = name !== 'library'; $('account-view').hidden = name !== 'account';
  $('page-label').textContent = name === 'library' ? 'My library' : 'Account & Steam';
  document.querySelectorAll('[data-view]').forEach(button => button.classList.toggle('active', button.dataset.view === name));
}
function signout(message = '') {
  document.querySelector('.completion-celebration')?.close();
  stopEnrichment(); $('enrichment-progress').hidden = true;
  if ($('hltb-review').open) $('hltb-review').close();
  $('queue-sort').value = 'manual'; $('queue-sort-goal').value = 'main_story_hours';
  state.filter = 'not started'; $('library-include-free').checked = false; $('library-include-tests').checked = false; $('library-include-excluded').checked = false; document.querySelector('.library-maintenance').open = false;
  state.generation++; state.token = null; state.user = null; state.games = []; state.queue = []; state.planBusy = false; state.loading = false;
  sessionStorage.removeItem('backlog-token'); refreshAccount(); renderLibrary(); view('account'); notice(message);
}
async function api(path, { method = 'GET', body, authenticated = true } = {}) {
  const headers = {};
  if (authenticated && state.token) headers.Authorization = `Bearer ${state.token}`;
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  const response = await fetch(`/api/v1${path}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body), credentials: 'same-origin' });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401 && authenticated) signout('Your session expired. Sign in to continue.');
    let message = typeof data.detail === 'string' ? data.detail : response.status === 502 || response.status === 504 ? 'Steam is unavailable right now. Please try again later.' : response.status >= 500 ? 'The service is unavailable. Please try again shortly.' : 'The request could not be completed.';
    if (data.detail && typeof data.detail.message === 'string') message = data.detail.message;
    if (Array.isArray(data.detail)) message = data.detail.map(item => item.msg).join('. ');
    throw new Error(message);
  }
  return data;
}
function refreshAccount() {
  const user = state.user;
  $('profile-name').textContent = user?.username || 'Your next chapter';
  $('profile-caption').textContent = user ? (user.steam_id ? 'Steam connected' : 'Steam not connected') : 'Sign in to get started';
  $('avatar').textContent = user ? user.username.slice(0, 1).toUpperCase() : '?';
  $('signout').hidden = !user; $('auth-card').hidden = !!user; $('profile-card').hidden = !user;
  $('account-name').textContent = user?.username || '';
  $('connection-label').textContent = user?.steam_id ? 'Steam connected' : 'Your games. Your pace.';
  $('steam-status').textContent = user?.steam_id ? 'CONNECTED' : 'NOT CONNECTED';
  $('steam-copy').textContent = user?.steam_id ? `Steam account ${user.steam_id} is connected. Bring your games into your library whenever you’re ready.` : 'Connect through Steam’s official sign-in page. Your Steam password never comes to Backlog Beater.';
  $('steam-button').disabled = !user || !!user.steam_id;
  $('steam-button').textContent = user?.steam_id ? 'Steam connected ✓' : 'Connect Steam ↗';
  $('steam-note').textContent = user?.steam_id ? 'Your completion progress stays yours when you sync.' : user ? 'You’ll return here after signing in on Steam.' : 'Sign in to your Backlog Beater account to connect.';
  $('sync-button').disabled = !user?.steam_id || state.loading;
}
function renderLibrary() {
  if (state.user && document.querySelector('.completion-celebration[open]')) return;
  $('library-view').classList.toggle('short-hero', Boolean(state.user));
  recommendationUI.refresh();
  renderPlan();
  const libraryGames = state.games.filter(game => ($('library-include-free').checked || game.is_free !== true) && ($('library-include-tests').checked || game.is_test_build !== true) && ($('library-include-excluded').checked || game.recommendation_hidden !== true));
  const stats = summarize(libraryGames);
  for (const [id, value] of Object.entries({ 'total-count': stats.total, 'progress-count': stats.playing, 'finished-count': stats.finished, 'library-badge': stats.total, 'completion-count': `${stats.percent}%` })) $(id).textContent = value;
  $('completion-bar').style.width = `${stats.percent}%`;
  for (const kind of ['review', 'missing']) {
    $(kind + '-count').textContent = libraryGames.filter(game => enrichmentAssessment(game) === kind).length;
  }
  $('assessment-copy').hidden = !['review', 'missing'].includes(state.filter);
  $('assessment-copy').textContent = state.filter === 'review'
    ? 'Choose an HLTB match for these games. Your progress and Play Next order stay saved.'
    : 'Games with failed lookups, no match, or no usable completion estimates.';
  document.querySelectorAll('[data-filter]').forEach(button => { const selected = button.dataset.filter === state.filter; button.setAttribute('aria-pressed', String(selected)); button.classList.toggle('selected', selected); });
  const selected = selectGames(state.games, state.filter, $('search').value, $('sort').value, $('game-mode').value, $('library-include-free').checked, $('library-include-tests').checked, $('library-include-excluded').checked);
  $('game-grid').replaceChildren(...selected.map(gameCard));
  $('mode-result-count').textContent = `${selected.length} of ${libraryGames.length} games`;
  $('empty-state').hidden = selected.length > 0;
  let title, copy, action;
  if (state.loading && !state.games.length) { title = 'Loading your library…'; copy = 'Getting your games ready.'; }
  else if (!state.user) { title = 'Your next chapter starts here'; copy = 'Sign in, connect Steam, and bring your library together. Your backlog is full of possibilities.'; action = 'Get started ↗'; }
  else if (!state.user.steam_id) { title = 'Bring your Steam library along'; copy = 'Connect Steam to import your games and start tracking your progress.'; action = 'Connect Steam ↗'; }
  else if (!state.games.length) { title = 'Ready when you are'; copy = 'Your Steam account is connected. Sync your library to bring in your games.'; action = 'Sync Steam library ↻'; }
  else if (['review', 'missing'].includes(state.filter)) { title = state.filter === 'review' ? 'No games need review' : 'No missing completion data'; copy = $('search').value ? 'No games in this area match your search.' : 'Games will appear here when they need attention.'; action = 'Clear filters'; }
  else { title = 'No games match just yet'; copy = 'Try All games, another progress filter, or Show free-to-play games.'; action = 'Clear filters'; }
  $('empty-title').textContent = title; $('empty-copy').textContent = copy; $('empty-action').hidden = !action; $('empty-action').textContent = action || '';
}
async function saveCompletion(game, status, control) {
  if (control.disabled) return;
  control.disabled = true;
  const generation = state.generation;
  const previousStatus = game.completion_status;
  const originalText = control.textContent;
  if (control.tagName === 'BUTTON') control.textContent = 'Saving…';
  try {
    const updated = await api(`/games/${game.id}`, { method: 'PATCH', body: { completion_status: status } });
    if (generation !== state.generation) return;
    Object.assign(game, updated);
    state.queue = state.queue.filter(id => id !== game.id || updated.completion_status !== 'in progress');
    if (previousStatus !== 'finished' && updated.completion_status === 'finished') {
      await celebrateCompletion(game, state.games.filter(item => item.completion_status === 'finished').length);
      if (generation !== state.generation) return;
    }
    renderLibrary(); notice('Progress saved. One game at a time.');
  } catch (error) {
    if (generation !== state.generation) return;
    if (control.tagName === 'SELECT') control.value = game.completion_status;
    notice(error.message, true);
  } finally {
    control.disabled = false;
    if (control.tagName === 'BUTTON') control.textContent = originalText;
  }
}
function gameCard(game, { activeCard = false } = {}) {
  const card = document.createElement('article'); card.className = 'game-card';
  const cover = document.createElement('div'); cover.className = 'game-cover'; cover.style.setProperty('--hue', String((game.steam_app_id * 47) % 360));
  const fallback = document.createElement('span'); fallback.className = 'cover-placeholder'; fallback.textContent = String(game.steam_app_id); cover.append(fallback);
  const url = safeCoverUrl(game.cover_url);
  if (url || safeCoverUrl(game.cover_fallback_url)) { const img = document.createElement('img'); img.alt = `Cover for ${game.name || `Steam app ${game.steam_app_id}`}`; img.loading = 'lazy'; img.referrerPolicy = 'no-referrer'; fallback.hidden = true; cover.append(img); setCoverImage(img, game, () => { img.remove(); fallback.hidden = false; }); }
  const tag = document.createElement('span'); tag.className = 'tag'; tag.textContent = labels[game.completion_status]; cover.append(tag);
  const info = document.createElement('div'); info.className = 'game-info';
  const title = document.createElement('h3'); title.textContent = game.name || `Steam app ${game.steam_app_id}`;
  const link = document.createElement('a'); link.href = `https://store.steampowered.com/app/${game.steam_app_id}/`; link.target = '_blank'; link.rel = 'noopener noreferrer'; link.textContent = 'View on Steam ↗';
  const select = document.createElement('select'); select.setAttribute('aria-label', `Completion status for Steam app ${game.steam_app_id}`);
  STATUSES.forEach(status => { const option = document.createElement('option'); option.value = status; option.textContent = labels[status]; select.append(option); }); select.value = game.completion_status; select.disabled = state.planBusy || state.loading;
  select.addEventListener('change', async () => {
    await saveCompletion(game, select.value, select);
  });
  const estimates = document.createElement('dl'); estimates.className = 'completion-times';
  estimates.setAttribute('aria-label', 'Estimated completion times');
  for (const [label, field] of [['Main story', 'main_story_hours'], ['Main + extras', 'main_extra_hours'], ['Completionist', 'completionist_hours']]) {
    const row = document.createElement('div');
    const term = document.createElement('dt'); term.textContent = label;
    const value = document.createElement('dd'); value.textContent = formatHours(game[field]);
    row.append(term, value); estimates.append(row);
  }
  const source = document.createElement('div'); source.className = 'completion-source';
  if (Number.isSafeInteger(game.hltb_id) && game.hltb_id > 0) {
    const hltb = document.createElement('a'); hltb.href = `https://howlongtobeat.com/game/${game.hltb_id}`;
    hltb.target = '_blank'; hltb.rel = 'noopener noreferrer'; hltb.textContent = 'HowLongToBeat ↗';
    source.append(hltb);
    if (game.hltb_source === 'personal') { const note = document.createElement('span'); note.textContent = 'Your selected match'; source.append(note); }
  } else { source.textContent = 'Completion estimates unavailable'; }
  info.append(title, link, estimates, source);
  const reason = enrichmentReason(game);
  if (reason) {
    const assessment = document.createElement('p'); assessment.className = 'assessment-reason';
    assessment.textContent = reason; info.append(assessment);
  }
  if (game.hltb_review_needed) {
    const review = document.createElement('button'); review.className = 'button secondary review-button';
    review.textContent = 'Resolve HLTB match'; review.addEventListener('click', () => openHLTBReview(game, review));
    info.append(review);
  }
  let activeMenu;
  if (activeCard) {
    const actions = document.createElement('div'); actions.className = 'active-game-actions';
    const menu = document.createElement('details'); menu.className = 'active-game-menu';
    const toggle = document.createElement('summary'); toggle.textContent = '⋯'; toggle.setAttribute('aria-label', `More actions for ${game.name || 'this game'}`);
    activeMenu = document.createElement('div'); activeMenu.className = 'active-game-menu-items';
    menu.append(toggle, activeMenu);
    menu.addEventListener('keydown', event => { if (event.key === 'Escape') { menu.open = false; toggle.focus(); event.stopPropagation(); } });
    menu.addEventListener('click', event => { if (event.target.closest('button')) menu.open = false; });
    const finish = planButton('✓ Finish game', () => saveCompletion(game, 'finished', finish));
    finish.classList.add('finish-game'); actions.append(finish, menu); info.append(actions);
    activeMenu.append(planButton('Move back to Play Next', () => stopPlaying(game, true)));
    activeMenu.append(planButton('Stop playing', () => stopPlaying(game, false)));
  } else info.append(select);
  if (game.completion_status !== 'in progress') {
    info.append(planButton('Start Playing', () => startPlaying(game)));
    const queued = state.queue.includes(game.id);
    info.append(planButton(queued ? 'In Play Next' : 'Add to Play Next', () => changeQueue('/games/queue/' + game.id, 'POST'), queued));
  }
  const exclude = planButton(game.recommendation_hidden ? 'Restore game' : 'Exclude game',
    () => setRecommendationHidden(game, !game.recommendation_hidden));
  exclude.classList.add('recommendation-hide');
  exclude.title = game.recommendation_hidden ? 'Show in your library and recommendations again' : 'Hide from your library and recommendations; keep progress and queue entries';
  (activeMenu || info).append(exclude);
  card.append(cover, info); return card;
}
function planButton(label, action, disabled = false) {
  const button = document.createElement('button'); button.type = 'button';
  button.className = 'button secondary'; button.textContent = label;
  button.disabled = disabled || state.planBusy || state.loading;
  button.addEventListener('click', action); return button;
}
async function planAction(action, successMessage = 'Play plan saved.') {
  if (state.planBusy) return;
  const generation = state.generation;
  state.planBusy = true; renderLibrary();
  try { await action(generation); if (generation === state.generation) notice(successMessage); }
  catch (error) {
    if (generation === state.generation) {
      notice(error.message, true);
      try { await loadLibrary(); } catch { /* Preserve action error. */ }
    }
  } finally { if (generation === state.generation) { state.planBusy = false; renderLibrary(); } }
}
function changeQueue(path, method, body) {
  return planAction(async generation => {
    const queue = await api(path, { method, body });
    if (generation === state.generation) state.queue = queue;
  });
}
function setRecommendationHidden(game, hidden) {
  return planAction(async generation => {
    const response = await api(`/games/${game.id}/recommendation-preference`, {method: 'PUT', body: {hidden}});
    if (generation === state.generation) game.recommendation_hidden = response.hidden;
  }, hidden ? 'Game excluded. Use Show excluded games to restore it.' : 'Game restored to your library and recommendations.');
}
function startPlaying(game) {
  return planAction(async generation => {
    const updated = await api('/games/' + game.id, { method: 'PATCH', body: { completion_status: 'in progress' } });
    if (generation !== state.generation) return;
    Object.assign(game, updated); state.queue = state.queue.filter(id => id !== game.id);
  });
}
function stopPlaying(game, enqueue) {
  return planAction(async generation => {
    const updated = await api(`/games/${game.id}`, { method: 'PATCH', body: { completion_status: 'not started' } });
    if (generation !== state.generation) return;
    Object.assign(game, updated);
    try {
      const queue = await api(`/games/queue/${game.id}`, { method: enqueue ? 'POST' : 'DELETE' });
      if (generation === state.generation) state.queue = queue;
    } catch (error) {
      throw new Error(`Game returned to Not started, but ${enqueue ? 'adding it to' : 'removing it from'} Play Next failed. ${error.message}`);
    }
  }, enqueue ? 'Moved back to the end of Play Next.' : 'Stopped playing. Game remains in your library, outside Play Next.');
}
function moveQueued(index, offset) {
  const game_ids = [...state.queue];
  [game_ids[index], game_ids[index + offset]] = [game_ids[index + offset], game_ids[index]];
  return changeQueue('/games/queue', 'PUT', { game_ids });
}
const discoveryDialog = $('recommendation-discovery');
$('find-game').addEventListener('click', () => discoveryDialog.showModal());
$('close-discovery').addEventListener('click', () => discoveryDialog.close());
discoveryDialog.addEventListener('click', event => { if (event.target === discoveryDialog) { const rect = discoveryDialog.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) discoveryDialog.close(); } });
function renderPlan() {
  $('play-plan').hidden = !state.user;
  const playing = state.games.filter(game => game.completion_status === 'in progress');
  $('currently-playing').replaceChildren(...playing.map(game => gameCard(game, { activeCard: true })));
  $('playing-empty').hidden = playing.length > 0;
  $('discovery-card').classList.toggle('is-playing', playing.length > 0);
  if (!state.user && discoveryDialog.open) discoveryDialog.close();
  const games = new Map(state.games.map(game => [game.id, game]));
  const preview = $('queue-sort').value !== 'manual';
  const displayQueue = sortPlayQueue(state.queue, state.games, $('queue-sort').value, $('queue-sort-goal').value);
  const changed = displayQueue.some((id, index) => id !== state.queue[index]);
  $('queue-apply-sort').disabled = !preview || !changed || state.planBusy || state.loading;
  $('queue-sort').disabled = $('queue-sort-goal').disabled = state.planBusy || state.loading || !state.queue.length;
  $('queue-sort-status').textContent = !state.queue.length ? '' : preview ? (changed ? 'Preview · Apply order to save. Missing estimates are placed last.' : 'This matches your saved order. Missing estimates are placed last.') : 'Your saved order. Preview a time-based order above.';
  $('play-next').replaceChildren(...displayQueue.flatMap((id, index) => {
    const game = games.get(id); if (!game) return [];
    const item = document.createElement('li'); item.className = 'queue-item';
    const name = game.name || 'Steam app ' + game.steam_app_id;
    const cover = document.createElement('div'); cover.className = 'game-cover queue-cover';
    cover.style.setProperty('--hue', String((game.steam_app_id * 47) % 360));
    const fallback = document.createElement('span'); fallback.className = 'cover-placeholder';
    fallback.textContent = String(game.steam_app_id); cover.append(fallback);
    const url = safeCoverUrl(game.cover_url);
    if (url || safeCoverUrl(game.cover_fallback_url)) {
      const img = document.createElement('img'); img.alt = 'Cover for ' + name;
      img.loading = 'lazy'; img.referrerPolicy = 'no-referrer'; fallback.hidden = true;
      cover.append(img); setCoverImage(img, game, () => { img.remove(); fallback.hidden = false; });
    }
    const rank = document.createElement('span'); rank.className = 'queue-rank';
    rank.textContent = String(index + 1); rank.setAttribute('aria-label', (preview ? 'Preview position ' : 'Queue position ') + (index + 1)); cover.append(rank);
    const info = document.createElement('div'); info.className = 'queue-info';
    const title = document.createElement('h3'); title.textContent = name; title.title = name;
    const estimates = document.createElement('dl'); estimates.className = 'completion-times queue-times';
    estimates.setAttribute('aria-label', 'Estimated completion times for ' + name);
    for (const [label, field] of [['Main story', 'main_story_hours'], ['Main + extras', 'main_extra_hours'], ['Completionist', 'completionist_hours']]) {
      const row = document.createElement('div');
      const term = document.createElement('dt'); term.textContent = label;
      const value = document.createElement('dd'); value.textContent = formatHours(game[field]);
      row.append(term, value); estimates.append(row);
    }
    const estimateNote = document.createElement('p'); estimateNote.className = 'queue-estimate-note';
    estimateNote.textContent = game.hltb_source === 'personal' ? 'HLTB estimates · Your selected match' : game.hltb_id ? 'HLTB estimates' : 'Completion estimates unavailable';
    const start = planButton('Start Playing', () => startPlaying(game)); start.classList.add('queue-start');
    const actions = document.createElement('div'); actions.className = 'queue-actions';
    const earlier = planButton('\u2190', () => moveQueued(index, -1), preview || index === 0);
    const later = planButton('\u2192', () => moveQueued(index, 1), preview || index === state.queue.length - 1);
    const remove = planButton('\u00d7', () => changeQueue('/games/queue/' + id, 'DELETE'));
    for (const [button, label] of [[earlier, 'Move ' + name + ' earlier'], [later, 'Move ' + name + ' later'], [remove, 'Remove ' + name + ' from Play Next']]) {
      button.setAttribute('aria-label', label); button.title = label;
    }
    remove.classList.add('queue-remove'); actions.append(earlier, later, remove);
    info.append(title, estimates, estimateNote, start, actions); item.append(cover, info); return [item];
  }));
  $('queue-empty').hidden = state.queue.length > 0;
}
let reviewSequence = 0;
$('hltb-review').addEventListener('close', () => { reviewSequence++; });
$('hltb-review-later').addEventListener('click', () => $('hltb-review').close());
async function openHLTBReview(game, trigger) {
  const generation = state.generation; const sequence = ++reviewSequence;
  trigger.disabled = true;
  try {
    let review = await api(`/enrichment/games/${game.id}/hltb-review`);
    if (generation !== state.generation || sequence !== reviewSequence) return;
    if (review.status === 'refreshing') {
      $('hltb-review-title').textContent = `Finding suggestions: ${game.name || game.steam_app_id}`;
      $('hltb-review-reason').textContent = 'Suggestions expired or are not cached yet. Searching for fresh matches…';
      $('hltb-review-error').hidden = true; $('hltb-review-candidates').replaceChildren();
      if (!$('hltb-review').open) $('hltb-review').showModal();
      const deadline = Date.now() + 2 * 60 * 1000;
      while (review.status === 'refreshing') {
        await new Promise(resolve => setTimeout(resolve, 3000));
        if (generation !== state.generation || sequence !== reviewSequence) return;
        if (Date.now() >= deadline) throw new Error('Suggestions are still processing. Close this review and try again later.');
        review = await api(`/enrichment/games/${game.id}/hltb-review`);
      }
    }
    if (review.status === 'lookup_failed') throw new Error(review.reason);
    if (!review.candidates.length) {
      $('hltb-review-title').textContent = 'No suggested matches found';
      $('hltb-review-reason').textContent = 'No plausible HLTB candidates were found. Your personal selections are unchanged.';
      $('hltb-review-candidates').replaceChildren(); $('hltb-review-error').hidden = true;
      if (!$('hltb-review').open) $('hltb-review').showModal();
      return;
    }
    if (generation !== state.generation || sequence !== reviewSequence) return;
    $('hltb-review-title').textContent = `Resolve match: ${game.name || `Steam app ${game.steam_app_id}`}`;
    const reasons = { multiple_exact_candidates: 'Several candidate entries share this title.', platform_conflict: 'The listed platforms differ.', release_year_conflict: 'The release years differ.', entry_type_requires_review: 'The HLTB entry type needs your confirmation.', similar_title_candidates: 'These entries have similar titles. Check the edition, year, and platforms before choosing.', review_refreshed: 'Fresh suggestions are ready for your confirmation.' };
    $('hltb-review-reason').textContent = reasons[review.reason] || 'This match needs your confirmation.';
    $('hltb-review-error').hidden = true;
    $('hltb-review-candidates').replaceChildren();
    review.candidates.forEach(candidate => {
      const item = document.createElement('article'); item.className = 'hltb-candidate';
      const title = document.createElement('h3'); title.textContent = candidate.name;
      const details = document.createElement('p'); details.textContent = `${candidate.platforms?.join(', ') || 'Platform unavailable'} · ${candidate.release_year || 'Year unavailable'} · ${candidate.game_type || 'Type unavailable'}`;
      const times = document.createElement('p'); times.textContent = `Main story: ${formatHours(candidate.main_story_hours)} · Main + extras: ${formatHours(candidate.main_extra_hours)} · Completionist: ${formatHours(candidate.completionist_hours)}`;
      item.append(title, details, times);
      if (Number.isSafeInteger(candidate.hltb_id) && candidate.hltb_id > 0) {
        const link = document.createElement('a'); link.href = `https://howlongtobeat.com/game/${candidate.hltb_id}`;
        link.target = '_blank'; link.rel = 'noopener noreferrer'; link.textContent = 'View on HowLongToBeat ↗'; item.append(link);
      }
      const choose = document.createElement('button'); choose.className = 'button primary'; choose.textContent = 'Use this match';
      choose.addEventListener('click', async () => {
        const buttons = [...$('hltb-review-candidates').querySelectorAll('button')];
        buttons.forEach(button => { button.disabled = true; }); $('hltb-review-error').hidden = true;
        try {
          const updated = await api(`/enrichment/games/${game.id}/hltb-selection`, { method: 'PUT', body: { hltb_id: candidate.hltb_id, cache_version: review.cache_version } });
          if (generation !== state.generation) return;
          Object.assign(game, updated); renderLibrary();
          if (sequence === reviewSequence) { $('hltb-review').close(); notice('Your HLTB match was saved.'); }
        } catch (error) {
          if (sequence === reviewSequence && generation === state.generation) { $('hltb-review-error').textContent = `${error.message} Close and reopen this review to load current candidates.`; $('hltb-review-error').hidden = false; }
        } finally { buttons.forEach(button => { button.disabled = false; }); }
      });
      item.append(choose); $('hltb-review-candidates').append(item);
    });
    if (!$('hltb-review').open) $('hltb-review').showModal();
  } catch (error) { if (generation === state.generation && sequence === reviewSequence) { notice(error.message, true); if ($('hltb-review').open) { $('hltb-review-error').textContent = error.message; $('hltb-review-error').hidden = false; } } }
  finally { trigger.disabled = false; }
}
async function loadLibrary() {
  const generation = state.generation;
  state.loading = true; refreshAccount(); renderLibrary();
  try { const [games, queue] = await Promise.all([api('/games'), api('/games/queue')]); if (generation === state.generation) { state.games = games; state.queue = queue; } }
  finally { if (generation === state.generation) { state.loading = false; refreshAccount(); renderLibrary(); } }
}
async function sync() {
  if (state.loading || !state.user?.steam_id) return;
  const previousFailures = Object.fromEntries(state.games.filter(game => game.enrichment_status === 'lookup_failed').map(game => [game.id, game.enrichment_last_lookup_at]));
  stopEnrichment(); $('enrichment-progress').hidden = true;
  const generation = state.generation; state.loading = true; refreshAccount(); $('sync-button').textContent = 'Syncing your games…'; $('sync-button').classList.add('busy');
  try {
    const result = await api('/steam/sync', { method: 'POST' });
    if (generation !== state.generation) return;
    await loadLibrary();
    let message = `Library synced. ${result.game_count} games found · ${result.ownerships_inserted_count} added to your library.`;
    const enrichment = result.enrichment;
    if (enrichment?.submitted) { message += ' Finding completion times…'; startEnrichment(previousFailures); }
    if (enrichment?.unavailable) message += ' Background enrichment could not be requested; your Steam import was saved. Sync again to retry.';
    notice(message);
  }
  catch (error) { notice(error.message, true); }
  finally { state.loading = false; refreshAccount(); $('sync-button').textContent = '↻ Sync Steam library'; $('sync-button').classList.remove('busy'); renderLibrary(); }
}
function authMode(mode) {
  state.mode = mode; $('login-tab').classList.toggle('selected', mode === 'login'); $('register-tab').classList.toggle('selected', mode === 'register');
  $('auth-title').textContent = mode === 'login' ? 'Welcome back.' : 'Start your next chapter.';
  $('auth-copy').textContent = mode === 'login' ? 'Your backlog will be right where you left it.' : 'Create your account, then connect your Steam library.';
  $('auth-submit').textContent = mode === 'login' ? 'Sign in ↗' : 'Create account ↗';
  $('password').autocomplete = mode === 'login' ? 'current-password' : 'new-password'; $('password').minLength = mode === 'register' ? 8 : 1; $('auth-error').hidden = true;
}
$('auth-form').addEventListener('submit', async event => {
  event.preventDefault(); $('auth-submit').disabled = true; $('auth-error').hidden = true;
  const body = { username: $('username').value.trim(), password: $('password').value };
  try {
    if (state.mode === 'register') await api('/auth/register', { method: 'POST', body, authenticated: false });
    const login = await api('/auth/login', { method: 'POST', body, authenticated: false });
    state.token = login.access_token; sessionStorage.setItem('backlog-token', state.token); state.generation++;
    state.user = await api('/auth/me'); $('password').value = ''; refreshAccount(); view('library'); await loadLibrary(); notice(`Welcome, ${state.user.username}. Let’s find your next game.`);
  } catch (error) { $('auth-error').textContent = error.message; $('auth-error').hidden = false; notice(error.message, true); }
  finally { $('auth-submit').disabled = false; }
});
$('steam-button').addEventListener('click', async () => {
  $('steam-button').disabled = true; $('steam-button').textContent = 'Opening Steam…';
  try { const data = await api('/steam/link', { method: 'POST' }); const url = new URL(data.login_url); if (url.origin !== 'https://steamcommunity.com' || url.pathname !== '/openid/login') throw new Error('Steam returned an unexpected sign-in URL.'); window.location.assign(url.href); }
  catch (error) { notice(error.message, true); refreshAccount(); }
});
$('sync-button').addEventListener('click', sync);
$('empty-action').addEventListener('click', () => {
  if (!state.user || !state.user.steam_id) view('account');
  else if (!state.games.length) sync();
  else { state.filter = 'not started'; $('search').value = ''; $('game-mode').value = 'all'; $('library-include-free').checked = false; $('library-include-tests').checked = false; $('library-include-excluded').checked = false; document.querySelectorAll('[data-filter]').forEach(button => button.classList.toggle('selected', button.dataset.filter === 'not started')); renderLibrary(); }
});
for (const id of ['signout', 'profile-signout']) $(id).addEventListener('click', () => signout('Signed out. See you next chapter.'));
$('account-button').addEventListener('click', () => view('account'));
$('login-tab').addEventListener('click', () => authMode('login')); $('register-tab').addEventListener('click', () => authMode('register'));
document.querySelectorAll('[data-view]').forEach(button => button.addEventListener('click', () => view(button.dataset.view)));
document.querySelectorAll('[data-filter]').forEach(button => button.addEventListener('click', () => { state.filter = button.dataset.filter; document.querySelectorAll('[data-filter]').forEach(item => item.classList.toggle('selected', item === button)); renderLibrary(); }));
$('search').addEventListener('input', renderLibrary); $('sort').addEventListener('change', renderLibrary);
refreshAccount(); renderLibrary(); authMode('login');
const callback = new URLSearchParams(location.search);
if (callback.has('steam')) history.replaceState(null, '', '/');
if (state.token) {
  try { state.user = await api('/auth/me'); refreshAccount(); await loadLibrary(); if (callback.get('steam') === 'linked' && state.user.steam_id) notice('Steam connected. Your next adventure is ready to sync.'); }
  catch (error) { notice(error.message, true); }
}
if (state.user) {
  try {
    const saved = JSON.parse(sessionStorage.getItem('backlog-enrichment') || 'null');
    if (saved?.userId === state.user.id && Date.now() - saved.started < 15 * 60 * 1000) startEnrichment(saved.previousFailures, saved.started);
  } catch { sessionStorage.removeItem('backlog-enrichment'); }
}
if (callback.get('steam') === 'error') { view('account'); notice(callback.get('message') || 'Steam linking failed. Please try connecting again.', true); }

$('game-mode').addEventListener('change', renderLibrary);

$('library-include-free').addEventListener('change', renderLibrary);

$('library-include-tests').addEventListener('change', renderLibrary);

$('library-include-excluded').addEventListener('change', renderLibrary);

const visibilityMenu = $('library-visibility');
visibilityMenu.addEventListener('keydown', event => {
  if (event.key === 'Escape') { visibilityMenu.open = false; visibilityMenu.querySelector('summary').focus(); }
});
document.addEventListener('click', event => {
  if (!visibilityMenu.contains(event.target)) visibilityMenu.open = false;
});

$('queue-sort').addEventListener('change', renderPlan);
$('queue-sort-goal').addEventListener('change', renderPlan);
$('queue-apply-sort').addEventListener('click', () => {
  const game_ids = sortPlayQueue(state.queue, state.games, $('queue-sort').value, $('queue-sort-goal').value);
  return planAction(async generation => {
    const queue = await api('/games/queue', {method: 'PUT', body: {game_ids}});
    if (generation === state.generation) { state.queue = queue; $('queue-sort').value = 'manual'; }
  }, 'Queue order saved.');
});

document.addEventListener('click', event => { document.querySelectorAll('.active-game-menu[open]').forEach(menu => { if (!menu.contains(event.target)) menu.open = false; }); });
