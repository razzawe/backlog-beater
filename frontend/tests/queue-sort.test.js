import test from 'node:test';
import assert from 'node:assert/strict';
import { sortPlayQueue } from '../assets/library.js';
const queue=[1,2,3,4,5,6];
const games=[{id:1,main_story_hours:10,main_extra_hours:2}, {id:2,main_story_hours:5,main_extra_hours:20},
 {id:3,main_story_hours:5}, {id:4,main_story_hours:null}, {id:5,main_story_hours:NaN}, {id:6,main_story_hours:0}];
test('queue sorting is stable and missing estimates remain last in either direction',()=>{
 assert.deepEqual(sortPlayQueue(queue,games,'shortest'),[2,3,1,4,5,6]);
 assert.deepEqual(sortPlayQueue(queue,games,'longest'),[1,2,3,4,5,6]);
 assert.deepEqual(sortPlayQueue(queue,games,'shortest','main_extra_hours'),[1,2,3,4,5,6]);
 assert.deepEqual(queue,[1,2,3,4,5,6]);
 assert.deepEqual(sortPlayQueue(queue,games),queue);
});
test('personal estimates can resolve shared ambiguity; selected categories are never substituted',()=>{
 const records=[{id:1,enrichment_status:'ambiguous',main_story_hours:1},
 {id:2,enrichment_status:'ambiguous',hltb_source:'personal',main_story_hours:2},
 {id:3,main_story_hours:3}];
 assert.deepEqual(sortPlayQueue([1,2,3],records,'shortest'),[2,3,1]);
 assert.deepEqual(sortPlayQueue([1,2,3],records,'shortest','completionist_hours'),[1,2,3]);
});
