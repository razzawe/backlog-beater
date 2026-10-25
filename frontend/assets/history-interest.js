export function visibleReason(reason, item) {
  return !reason.startsWith('Weeks assume') && !(item.history_game_count > 0 && item.history_genres?.length && reason.includes(': shares genres with '));
}

export function historyInterest(item) {
  if (!item.history_genres?.length || !(item.history_game_count > 0)) return null;
  const section = document.createElement('div'); section.className = 'history-interest';
  const title = document.createElement('strong'); title.textContent = 'Based on your finished games';
  const chips = document.createElement('div'); chips.className = 'history-genre-chips';
  for (const genre of item.history_genres) { const chip = document.createElement('span'); chip.textContent = genre; chips.append(chip); }
  const count = item.history_game_count;
  const description = document.createElement('small'); description.textContent = `Similar genres to ${count} game${count === 1 ? '' : 's'} you’ve completed.`;
  section.append(title, chips, description); return section;
}
