import { safeCoverUrl, formatHours } from './library.js';

export function preferencesFromForm(form) {
  const hours = Number(form.get('weekly_hours'));
  const weeks = Number(form.get('target_weeks'));
  if (!Number.isFinite(hours) || hours <= 0 || hours > 168 || !Number.isInteger(weeks) || weeks < 1 || weeks > 52) throw new Error('Enter positive hours up to 168 and a whole number of weeks from 1 to 52.');
  return { include_free_games: form.get('include_free') === 'on', weekly_hours: hours, target_weeks: weeks, preferred_genres: form.getAll('genre'), completion_goal: form.get('completion_goal') };
}

export function emptyRecommendationMessage(reason) {
  return ({empty_library: 'Your library is empty. Connect Steam and sync your games first.',
    no_eligible_games: 'No eligible games yet. Suggestions need a not-started single-player game with a resolved match and an estimate for your chosen goal. Try another completion goal or review missing library data.',
    no_games_within_budget: 'No games fit this time budget. Try more hours per week or a longer completion window.'})[reason] || 'No suggestions available. Adjust your preferences and try again.';
}

export function mountRecommendations({ api, getState, startPlaying, addToQueue, hideRecommendation }) {
  const $ = id => document.getElementById(id);
  const form = $('recommendation-form');
  let generation = null, request = 0, genreRequest = 0, busy = false, genresReady = false, genresLoading = false;
  const node = (tag, text, className) => { const el = document.createElement(tag); el.textContent = text; if (className) el.className = className; return el; };
  function updateBudget() {
    const hours = Number(form.elements.weekly_hours.value), weeks = Number(form.elements.target_weeks.value);
    $('recommendation-budget').textContent = Number.isFinite(hours * weeks) && hours > 0 && weeks > 0 ? `${new Intl.NumberFormat('en', {maximumFractionDigits: 2}).format(hours * weeks)}-hour budget` : 'Set your time budget';
  }
  let submittedPreferences = null, pagination = null, progressSignature = null, retryOffset = null;
  const dropdown = $('genre-dropdown');
  function updateGenrePicker() {
    const inputs = [...$('recommendation-genres').querySelectorAll('input')];
    const selected = inputs.filter(input => input.checked);
    $('genre-summary').textContent = selected.length ? `${selected.length} genre${selected.length === 1 ? '' : 's'} selected` : 'Any genre';
    $('genre-clear').disabled = !selected.length;
    $('genre-chips').replaceChildren(...selected.map(input => {
      const button = node('button', `${input.dataset.name} ×`, 'genre-chip');
      button.type = 'button'; button.setAttribute('aria-label', `Remove ${input.dataset.name}`);
      button.addEventListener('click', () => {
        input.checked = false; updateGenrePicker(); input.dispatchEvent(new Event('input', {bubbles: true}));
        dropdown.querySelector('summary').focus();
      });
      return button;
    }));
    inputs.forEach(input => { input.disabled = !input.checked && selected.length >= 20; });
  }
  function filterGenres() {
    const query = $('genre-search').value.trim().toLowerCase();
    const options = [...$('recommendation-genres').querySelectorAll('label')];
    options.forEach(label => { label.hidden = !label.textContent.toLowerCase().includes(query); });
    $('genre-no-results').hidden = !options.length || options.some(label => !label.hidden);
  }
  $('genre-search').addEventListener('input', event => { event.stopPropagation(); filterGenres(); });
  $('recommendation-genres').addEventListener('change', updateGenrePicker);
  $('genre-clear').addEventListener('click', () => {
    $('recommendation-genres').querySelectorAll('input').forEach(input => { input.checked = false; });
    updateGenrePicker(); $('recommendation-genres').dispatchEvent(new Event('input', {bubbles: true}));
  });
  dropdown.addEventListener('toggle', () => { if (dropdown.open) $('genre-search').focus(); });
  dropdown.addEventListener('keydown', event => {
    if (event.key === 'Escape') { dropdown.open = false; dropdown.querySelector('summary').focus(); event.preventDefault(); }
  });
  document.addEventListener('click', event => { if (!dropdown.contains(event.target)) dropdown.open = false; });
  async function loadGenres() {
    const run = ++genreRequest, session = getState().generation;
    genresLoading = true;
    $('recommendation-genre-status').textContent = 'Loading genres…';
    $('recommendation-genres-retry').hidden = true;
    try {
      const response = await api('/recommendations');
      if (run !== genreRequest || session !== getState().generation || !getState().user) return;
      $('recommendation-genres').replaceChildren(...response.genres.map(genre => {
        const label = document.createElement('label'), input = document.createElement('input');
        input.type = 'checkbox'; input.name = 'genre'; input.value = genre.id; input.dataset.name = genre.name;
        label.append(input, node('span', genre.name)); return label;
      }));
      updateGenrePicker(); filterGenres();
      genresReady = true;
      $('recommendation-genre-status').textContent = response.genres.length ? 'Choose up to 20. Leave blank to rank by time fit.' : 'No genres collected yet. You can still get suggestions by time fit.';
    } catch (error) {
      if (run !== genreRequest || session !== getState().generation) return;
      genresReady = true;
      $('recommendation-genre-status').textContent = 'Genres could not load. You can still get suggestions by time fit.';
      $('recommendation-genres-retry').hidden = false;
    } finally { if (run === genreRequest) genresLoading = false; }
  }
  function refresh() {
    const state = getState();
    const signature = state.games.map(game => `${game.id}:${game.completion_status}`).join('|');
    if (progressSignature !== null && signature !== progressSignature && submittedPreferences) {
      request++; busy = false; retryOffset = null; submittedPreferences = null; pagination = null;
      $('recommendation-pages').hidden = true;
      $('recommendation-status').textContent = 'Your library progress changed. Find games again to refresh the ranking.';
    }
    progressSignature = signature;
    if (generation !== state.generation) {
      retryOffset = null; $('recommendation-results').style.minHeight = '';
      generation = state.generation; submittedPreferences = null; pagination = null; $('recommendation-pages').hidden = true;
      request++; genreRequest++; busy = false; genresReady = false; genresLoading = false;
      $('recommendation-results').replaceChildren(); $('recommendation-status').textContent = '';
      $('recommendation-error').hidden = true; $('recommendation-genres').replaceChildren();
      form.reset(); dropdown.open = false; updateGenrePicker(); filterGenres(); updateBudget();
    }
    form.hidden = !state.user; $('recommendation-signin').hidden = Boolean(state.user);
    if (state.user && !genresReady && !genresLoading) loadGenres();
    $('recommendation-submit').disabled = busy || state.loading || state.planBusy;
    $('recommendation-submit').textContent = busy && !pagination ? 'Finding games…' : 'Find my next game ↗';
    $('recommendation-back').disabled = busy || !pagination || pagination.offset === 0;
    $('recommendation-next').disabled = busy || !pagination?.has_more;
    $('recommendation-page-loading').hidden = !busy || !pagination;
    $('recommendation-page-retry').hidden = busy || retryOffset === null;
    $('recommendation-results').setAttribute('aria-busy', String(busy));
    document.querySelectorAll('#recommendation-results article').forEach(card => {
      const game = state.games.find(item => item.id === Number(card.dataset.gameId));
      const buttons = card.querySelectorAll('button');
      if (!game || buttons.length < 2) return;
      card.hidden = game.recommendation_hidden === true;
      if (buttons[2]) buttons[2].disabled = busy || state.planBusy || state.loading;
      buttons[0].disabled = busy || state.planBusy || state.loading || game.completion_status !== 'not started';
      buttons[0].textContent = game.completion_status === 'in progress' ? 'Currently playing' : game.completion_status === 'finished' ? 'Finished' : 'Start Playing';
      buttons[1].disabled = busy || state.planBusy || state.loading || state.queue.includes(game.id) || game.completion_status !== 'not started';
      buttons[1].textContent = state.queue.includes(game.id) ? 'In Play Next' : 'Add to Play Next';
    });
  }
  function renderResults(response) {
    $('recommendation-results').replaceChildren(...response.recommendations.map(item => {
      const card = document.createElement('article'); card.className = 'game-card recommendation-card'; card.dataset.gameId = item.game_id; card._trailerRecommendation = item;
      const cover = document.createElement('div'); cover.className = 'game-cover';
      const mark = node('span', '↗', 'recommendation-cover-mark'); mark.setAttribute('aria-hidden', 'true'); cover.append(mark);
      const url = safeCoverUrl(item.cover_url);
      if (url) { const img = document.createElement('img'); img.src = url; img.alt = ''; img.loading = 'lazy'; img.addEventListener('load', () => { mark.hidden = true; }, {once: true}); img.addEventListener('error', () => img.remove(), {once: true}); cover.append(img); }
      const info = document.createElement('div'); info.className = 'game-info';
      info.append(node('h3', item.title || `Steam game ${item.game_id}`), node('p', `${formatHours(item.estimated_hours)} · about ${new Intl.NumberFormat('en', {maximumFractionDigits: 1}).format(item.estimated_weeks)} weeks`));
      info.append(node('p', item.estimate_source === 'personal' ? 'Your HLTB selection' : 'Shared HLTB estimate', 'recommendation-source'));
      const reasons = document.createElement('ul');
      item.reasons.filter(reason => !reason.startsWith('Weeks assume')).forEach(reason => reasons.append(node('li', reason)));
      info.append(reasons);
      const actions = node('div', '', 'recommendation-actions');
      const game = () => getState().games.find(candidate => candidate.id === item.game_id);
      const start = node('button', 'Start Playing', 'button primary'); start.type = 'button';
      start.addEventListener('click', () => { if (game()) startPlaying(game()); });
      const queue = node('button', 'Add to Play Next', 'button secondary'); queue.type = 'button';
      queue.addEventListener('click', () => { if (game()) addToQueue(item.game_id); });
      const hide = node('button', 'Exclude game', 'recommendation-hide'); hide.type = 'button';
      hide.addEventListener('click', async () => {
        const session = getState().generation;
        if (!game() || busy) return;
        await hideRecommendation(game());
        if (getState().generation === session && game()?.recommendation_hidden && submittedPreferences) await requestPage(0);
      });
      actions.append(start, queue, hide); info.append(actions); card.append(cover, info); return card;
    }));
    pagination = response.pagination || {offset: 0, limit: 3, total: response.coverage.games_within_budget, has_more: false};
    $('recommendation-pages').hidden = pagination.total <= 3 && pagination.offset === 0;
    $('recommendation-page-label').textContent = response.recommendations.length ? `${pagination.offset + 1}–${pagination.offset + response.recommendations.length} of ${pagination.total}` : 'End of results';
    $('recommendation-status').textContent = response.recommendations.length ? `${response.recommendations.length} suggestions from ${response.coverage.games_within_budget} games within your budget.` : pagination.offset > 0 ? 'No more games on this page. Go back or find games again to refresh results.' : emptyRecommendationMessage(response.empty_reason);
    refresh();
  }
  form.addEventListener('input', () => {
    updateBudget();
    if (busy || $('recommendation-results').children.length) {
      retryOffset = null; $('recommendation-results').style.minHeight = '';
      request++; busy = false; pagination = null; submittedPreferences = null; $('recommendation-pages').hidden = true; $('recommendation-results').replaceChildren();
      $('recommendation-status').textContent = 'Preferences changed. Find games to update your suggestions.';
      refresh();
    }
  });
  $('recommendation-genres-retry').addEventListener('click', loadGenres);
  form.addEventListener('submit', async event => {
    event.preventDefault(); if (busy) return; if (!form.reportValidity() || !getState().user) return;
    let body;
    try { body = preferencesFromForm(new FormData(form)); if (body.preferred_genres.length > 20) throw new Error('Choose up to 20 preferred genres.'); }
    catch (error) { $('recommendation-error').textContent = error.message; $('recommendation-error').hidden = false; return; }
    submittedPreferences = body;
    await requestPage(0);
  });
  async function requestPage(offset) {
    if (!submittedPreferences || busy) return;
    const body = {...submittedPreferences, offset};
    const run = ++request, session = getState().generation;
    const results = $('recommendation-results');
    const hadResults = results.children.length > 0;
    const previousStatus = $('recommendation-status').textContent;
    if (hadResults) results.style.minHeight = `${results.getBoundingClientRect().height}px`;
    retryOffset = null; busy = true; $('recommendation-error').hidden = true;
    if (!hadResults) $('recommendation-status').textContent = 'Looking through your library…';
    refresh();
    try {
      const response = await api('/recommendations', {method: 'POST', body});
      if (run !== request || session !== getState().generation || !getState().user) return;
      renderResults(response);
    } catch (error) {
      if (run !== request || session !== getState().generation) return;
      $('recommendation-error').textContent = error.message; $('recommendation-error').hidden = false; retryOffset = offset; $('recommendation-status').textContent = hadResults ? previousStatus : '';
    } finally { if (run === request && session === getState().generation) { busy = false; refresh(); } }
  }
  $('recommendation-page-retry').addEventListener('click', () => requestPage(retryOffset));
  $('recommendation-back').addEventListener('click', () => requestPage(Math.max(0, pagination.offset - 3)));
  $('recommendation-next').addEventListener('click', () => requestPage(pagination.offset + 3));
  return {refresh};
}
