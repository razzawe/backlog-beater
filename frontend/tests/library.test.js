import test from 'node:test';
import assert from 'node:assert/strict';
import { selectGames, summarize, safeCoverUrl, formatHours, enrichmentAssessment, enrichmentReason } from '../assets/library.js';
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


test('assessment views separate pending, review, missing and resolved games', () => {
  const cases = [
    [{}, 'pending'],
    [{ enrichment_status: 'lookup_failed' }, 'missing'],
    [{ enrichment_status: 'no_match' }, 'missing'],
    [{ enrichment_status: 'matched', hltb_id: 1 }, 'missing'],
    [{ enrichment_status: 'ambiguous', hltb_review_needed: true }, 'review'],
    [{ enrichment_status: 'matched', main_story_hours: 3 }, 'resolved'],
    [{ enrichment_status: 'ambiguous', hltb_source: 'personal', main_story_hours: 3 }, 'resolved'],
    [{ enrichment_status: 'ambiguous', hltb_source: 'personal' }, 'missing'],
    [{ enrichment_status: 'lookup_failed', hltb_id: 1, main_story_hours: 3 }, 'resolved'],
    [{ enrichment_status: 'matched', main_story_hours: NaN, main_extra_hours: 0 }, 'missing'],
  ];
  for (const [game, expected] of cases) assert.equal(enrichmentAssessment(game), expected);
  assert.match(enrichmentReason({}), /Pending/);
  assert.match(enrichmentReason({ enrichment_status: 'lookup_failed' }), /failed/);
  const data = cases.map(([game], i) => ({ ...game, id: i, steam_app_id: i, name: 'Example' }));
  assert.equal(selectGames(data, 'review', '', 'default').length, 1);
  assert.equal(selectGames(data, 'missing', 'Example', 'default').length, 5);
  assert.equal(selectGames(data, 'missing', 'unrelated', 'default').length, 0);
});
