'use strict';
// slugkit 1.0.0 (the currently released version)
function slugify(input, opts = {}) {
  const sep = opts.separator === undefined ? '-' : opts.separator;
  let s = String(input).toLowerCase().replace(/[^a-z0-9]+/g, sep);
  s = s.split(sep).filter(Boolean).join(sep);
  if (opts.maxLength) {
    s = s.slice(0, opts.maxLength).split(sep).filter(Boolean).join(sep);
  }
  return s;
}
module.exports = { slugify };
