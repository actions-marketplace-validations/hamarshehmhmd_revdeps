const test = require('node:test');
const assert = require('node:assert');
const { slugify } = require('slugkit');

test('post url from title', () => {
  assert.strictEqual('/posts/' + slugify('Hello, World!'), '/posts/hello-world');
});
test('tag slugs use underscores', () => {
  assert.strictEqual(slugify('Open Source', { separator: '_' }), 'open_source');
});
test('collapses whitespace', () => {
  assert.strictEqual(slugify('  many   spaces  '), 'many-spaces');
});
