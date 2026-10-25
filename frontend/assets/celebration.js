import { safeCoverUrl } from './library.js';
import { setCoverImage } from './covers.js';

export function celebrateCompletion(game, count) {
  document.querySelector('.completion-celebration')?.close();
  const dialog = document.createElement('dialog');
  dialog.className = 'completion-celebration';
  dialog.setAttribute('aria-labelledby', 'celebration-title');
  dialog.setAttribute('aria-describedby', 'celebration-game');
  const cover = document.createElement('div'); cover.className = 'celebration-cover';
  const url = safeCoverUrl(game.cover_url);
  if (url || safeCoverUrl(game.cover_fallback_url)) { const img = document.createElement('img'); img.alt = ''; img.referrerPolicy = 'no-referrer'; cover.append(img); setCoverImage(img, game, () => img.remove()); }
  const check = document.createElement('span'); check.className = 'celebration-check'; check.textContent = '✓'; check.setAttribute('aria-hidden', 'true'); cover.append(check);
  const heading = document.createElement('h2'); heading.id = 'celebration-title'; heading.textContent = 'Another one conquered.';
  const name = document.createElement('p'); name.id = 'celebration-game'; name.textContent = `You finished ${game.name || 'your game'}.`;
  const milestone = document.createElement('strong'); milestone.className = 'celebration-milestone'; milestone.textContent = `${count} game${count === 1 ? '' : 's'} finished!`;
  const button = document.createElement('button'); button.className = 'button primary'; button.textContent = 'Nice →'; button.onclick = () => dialog.close();
  const confetti = document.createElement('div'); confetti.className = 'celebration-confetti'; confetti.setAttribute('aria-hidden', 'true');
  if (!matchMedia('(prefers-reduced-motion: reduce)').matches) for (let i = 0; i < 28; i++) { const piece = document.createElement('i'); piece.style.setProperty('--x', `${Math.random() * 100}%`); piece.style.setProperty('--delay', `${Math.random() * .3}s`); piece.style.setProperty('--angle', `${Math.random() * 360}deg`); piece.style.background = ['#e9c56b','#baa2ff','#8bdcc4'][i % 3]; confetti.append(piece); }
  dialog.append(confetti, cover, heading, name, milestone, button);
  dialog.addEventListener('click', event => { if (event.target !== dialog) return; const rect = dialog.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close(); });
  dialog.addEventListener('close', () => dialog.remove(), { once: true });
  const dismissed = new Promise(resolve => dialog.addEventListener('close', resolve, { once: true }));
  document.body.append(dialog); dialog.showModal(); button.focus();
  return dismissed;
}
