const test = require('node:test');
const assert = require('node:assert');
const { slugify } = require('slugkit');

const short = (title) => slugify(title, { maxLength: 12 });

test('short slug is capped at 12 chars', () => {
  assert.strictEqual(short('A very long article title about caching'), 'a-very-long');
});
test('short slug never ends with a dash', () => {
  assert.ok(!short('Release notes for v2').endsWith('-'));
  assert.ok(short('Release notes for v2').length <= 12);
});
test('short titles are untouched', () => {
  assert.strictEqual(short('Hi there'), 'hi-there');
});
