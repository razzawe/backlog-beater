import test from 'node:test';
import assert from 'node:assert/strict';
import { enrichmentProgress } from '../assets/enrichment.js';

test('counts saved outcomes, pending games and unnamed exclusions', () => {
  const games = ['matched', 'ambiguous', 'no_match', null].map((status, id) => ({ id, name: 'Game', enrichment_status: status }));
  games.push({ id: 9, name: ' ' });
  assert.deepEqual(enrichmentProgress(games), { processed: 3, total: 4, failed: 0 });
});
test('previous failures wait for a new attempt, fresh failures count as processed', () => {
  const games = [{ id: 1, name: 'Game', enrichment_status: 'lookup_failed', enrichment_last_lookup_at: 'old' }];
  assert.equal(enrichmentProgress(games, { 1: 'old' }).processed, 0);
  games[0].enrichment_last_lookup_at = 'new';
  assert.deepEqual(enrichmentProgress(games, { 1: 'old' }), { processed: 1, total: 1, failed: 1 });
});
test('personal overrides do not disguise pending shared enrichment', () => {
  assert.equal(enrichmentProgress([{ name: 'Game', hltb_id: 5, hltb_source: 'personal' }]).processed, 0);
  assert.deepEqual(enrichmentProgress([]), { processed: 0, total: 0, failed: 0 });
});
