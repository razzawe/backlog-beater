import test from 'node:test';
import assert from 'node:assert/strict';
import { preferencesFromForm, emptyRecommendationMessage } from '../assets/recommendations.js';
function form(values) { return {get: key => values[key], getAll: key => values[key] || []}; }
test('recommendation preferences use numeric hours and strict integer weeks', () => {
  assert.deepEqual(preferencesFromForm(form({weekly_hours:'4',target_weeks:'3',genre:['1'],completion_goal:'main_story'})),
    {include_free_games:false,weekly_hours:4,target_weeks:3,preferred_genres:['1'],completion_goal:'main_story'});
  for (const values of [{weekly_hours:'0',target_weeks:'3'}, {weekly_hours:'Infinity',target_weeks:'3'},
      {weekly_hours:'4',target_weeks:'1.5'}, {weekly_hours:'169',target_weeks:'3'}, {weekly_hours:'4',target_weeks:'53'}]) {
    assert.throws(() => preferencesFromForm(form(values)));
  }
});
test('empty states explain constraints without silently relaxing them', () => {
  assert.match(emptyRecommendationMessage('empty_library'), /sync/i);
  assert.match(emptyRecommendationMessage('no_eligible_games'), /single-player/);
  assert.match(emptyRecommendationMessage('no_games_within_budget'), /longer/);
});
