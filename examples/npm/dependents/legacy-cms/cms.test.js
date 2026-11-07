const test = require('node:test');
const assert = require('node:assert');
const { slugify } = require('slugkit');

test('page slug', () => {
  assert.strictEqual(slugify('About Us'), 'about-us');
});
// Broken for years, nothing to do with slugkit.
test('legacy date formatter', () => {
  assert.strictEqual(new Date(0).getUTCFullYear(), 1969);
});
