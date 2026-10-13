import test from 'node:test';
import assert from 'node:assert/strict';
import { selectGames, summarize, safeCoverUrl, formatHours } from '../assets/library.js';
const games = [{ id: 1, steam_app_id: 730, name: "Counter-Strike", completion_status: 'finished' }, { id: 2, steam_app_id: 570, completion_status: 'in progress' }, { id: 3, steam_app_id: 440, completion_status: 'not started' }];
test('filters and search combine without changing the source library', () => { assert.deepEqual(selectGames(games, 'finished', '730', 'default').map(g => g.id), [1]); assert.deepEqual(selectGames(games, 'finished', '570', 'default'), []); assert.deepEqual(selectGames(games, 'all', '', 'appid').map(g => g.id), [3, 2, 1]); assert.deepEqual(games.map(g => g.id), [1, 2, 3]); });
test('completion rate handles both empty and populated libraries', () => { assert.equal(summarize([]).percent, 0); assert.deepEqual(summarize(games), { total: 3, playing: 1, finished: 1, percent: 33 }); });
test('cover URLs reject active and malformed URLs', () => { for (const url of ['javascript:alert(1)', 'data:text/html,test', 'file:///tmp/test', 'invalid', null]) assert.equal(safeCoverUrl(url), null); assert.equal(safeCoverUrl('https://example.com/cover.jpg'), 'https://example.com/cover.jpg'); });

test("search includes endpoint-provided names", () => { assert.deepEqual(selectGames(games, "all", "counter", "default").map(g => g.id), [1]); });
test('completion estimates preserve fractional hours and distinguish unavailable data', () => {
  assert.equal(formatHours(12), '12 h');
  assert.equal(formatHours(1.5), '1.5 h');
  assert.equal(formatHours(0.01), '0.01 h');
  for (const value of [null, undefined, 0, -1, NaN, Infinity, '12', true]) assert.equal(formatHours(value), '—');
});
