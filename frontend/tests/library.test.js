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

test('game mode filters distinguish mixed games and unknown metadata', () => {
  const modes = [
    {id: 1, single_player: true, multiplayer: false},
    {id: 2, single_player: false, multiplayer: true},
    {id: 3, single_player: true, multiplayer: true},
    {id: 4, single_player: null, multiplayer: null},
    {id: 5, single_player: false, multiplayer: false},
  ].map(g => ({...g, name: 'Example', steam_app_id: g.id, completion_status: 'not started'}));
  const ids = mode => selectGames(modes, 'all', '', 'appid', mode).map(g => g.id);
  assert.deepEqual(ids('single'), [1, 3]);
  assert.deepEqual(ids('multi'), [2, 3]);
  assert.deepEqual(ids('single-only'), [1]);
  assert.deepEqual(ids('multi-only'), [2]);
  assert.deepEqual(ids('unknown'), [4]);
  assert.equal(ids('all').length, 5);
  assert.equal(selectGames(modes, 'finished', '', 'appid', 'single').length, 0);
});

test('library hides verified free games unless explicitly included', () => {
  const entries=[
    {id:1,steam_app_id:1,name:'Paid',completion_status:'not started',is_free:false},
    {id:2,steam_app_id:2,name:'Free',completion_status:'not started',is_free:true},
    {id:3,steam_app_id:3,name:'Unknown',completion_status:'finished',is_free:null},
  ];
  assert.deepEqual(selectGames(entries,'all','','appid').map(g=>g.id),[1,3]);
  assert.deepEqual(selectGames(entries,'not started','','appid').map(g=>g.id),[1]);
  assert.deepEqual(selectGames(entries,'not started','','appid','all',true).map(g=>g.id),[1,2]);
  assert.equal(entries.length,3);
});

test('test builds have an independent opt-in', () => {
  const games=[{id:1,steam_app_id:1,name:'Game',completion_status:'not started',is_test_build:false},
    {id:2,steam_app_id:2,name:'Game Public Test',completion_status:'not started',is_test_build:true}];
  assert.deepEqual(selectGames(games,'all','','appid','all',true).map(g=>g.id),[1]);
  assert.deepEqual(selectGames(games,'all','','appid','all',false,true).map(g=>g.id),[1,2]);
});


test('personal exclusions can be restored through an independent filter', () => {
  const entries=[{id:1,steam_app_id:1,name:'Game',completion_status:'not started',recommendation_hidden:true}];
  assert.deepEqual(selectGames(entries,'all','','appid'),[]);
  assert.equal(selectGames(entries,'all','','appid','all',false,false,true).length,1);
  assert.equal(entries[0].recommendation_hidden,true);
});
