import test from 'node:test';
import assert from 'node:assert/strict';
import { setCoverImage } from '../assets/covers.js';

test('expired S3 link falls back once, then shows placeholder', () => {
  const img = {}; let failures = 0;
  setCoverImage(img, {cover_url:'https://s3.test/a?signature=x', cover_fallback_url:'https://steam.test/a'}, () => failures++);
  assert.equal(img.src, 'https://s3.test/a?signature=x');
  img.onerror(); assert.equal(img.src, 'https://steam.test/a');
  img.onerror(); assert.equal(failures, 1); assert.equal(img.onerror, null);
});

test('identical and unsafe URLs cannot cause fallback loops', () => {
  for (const game of [
    {cover_url:'https://steam.test/a', cover_fallback_url:'https://steam.test/a'},
    {cover_url:'javascript:alert(1)', cover_fallback_url:'data:image/png,x'},
  ]) {
    const img = {}; let failures = 0;
    setCoverImage(img, game, () => failures++);
    if (img.onerror) img.onerror();
    assert.equal(failures, 1); assert.equal(img.onerror, null);
  }
});
