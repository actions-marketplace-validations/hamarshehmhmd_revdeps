const test = require('node:test');
const assert = require('node:assert');
const { slugify } = require('slugkit');

test('plain recipe names', () => {
  assert.strictEqual(slugify('Banana Bread'), 'banana-bread');
});
// Reported upstream as slugkit#12: accents get mangled.
test('accented recipe names', () => {
  assert.strictEqual(slugify('Crème brûlée'), 'creme-brulee');
});
