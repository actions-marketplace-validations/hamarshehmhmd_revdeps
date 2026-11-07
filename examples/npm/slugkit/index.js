'use strict';
// slugkit 2.0.0-dev (your unreleased change):
//  - strips accents: "Crème brûlée" -> "creme-brulee" (was "cr-me-br-l-e")
//  - renames the `maxLength` option to `limit` ("cleaner API")
function slugify(input, opts = {}) {
  const sep = opts.separator === undefined ? '-' : opts.separator;
  let s = String(input).normalize('NFKD').replace(/[\u0300-\u036f]/g, '');
  s = s.toLowerCase().replace(/[^a-z0-9]+/g, sep);
  s = s.split(sep).filter(Boolean).join(sep);
  if (opts.limit) {
    s = s.slice(0, opts.limit).split(sep).filter(Boolean).join(sep);
  }
  return s;
}
module.exports = { slugify };
