import { safeCoverUrl } from './library.js';

// Try each distinct safe URL once, then leave the existing placeholder visible.
export function setCoverImage(img, game, onUnavailable = () => {}) {
  const urls = [...new Set([game.cover_url, game.cover_fallback_url]
    .map(safeCoverUrl).filter(Boolean))];
  let next = 0;
  const advance = () => {
    if (next < urls.length) img.src = urls[next++];
    else { img.onerror = null; onUnavailable(); }
  };
  img.onerror = advance;
  advance();
}
