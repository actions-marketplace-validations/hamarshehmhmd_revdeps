"""revdeps command line."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from typing import List, Optional

from . import __version__
from .config import DEFAULT_CONFIG, ConfigError, detect_package, load
from .discover import DiscoverError, dependents, render_config
from .report import counts, details, line, markdown, paint, summary, use_color
from .runner import BROKEN, ERROR, FAIL, FLAKY, Options, PrepareError, run_all, write_json

FAIL_CHOICES = {"broken": {BROKEN, FAIL}, "error": {ERROR}, "flaky": {FLAKY}}


def _out(text: str = "") -> None:
    print(text, flush=True)


def cmd_discover(args) -> int:
    color = use_color(args.color)
    eco, name = args.ecosystem, args.package
    if not name or not eco:
        det_eco, det_name = detect_package(args.path)
        eco = eco or det_eco
        name = name or det_name
    if not name or not eco:
        print("revdeps: give a package name (and --ecosystem npm|pypi), or run inside a package directory",
              file=sys.stderr)
        return 2
    if args.write and os.path.exists(args.write) and not args.force:
        print(f"revdeps: {args.write} exists (use --force to overwrite)", file=sys.stderr)
        return 2
    try:
        found = dependents(name, eco, top=args.top)
    except DiscoverError as exc:
        print(f"revdeps: {exc}", file=sys.stderr)
        return 2
    if args.json:
        _out(json.dumps(found, indent=2))
    else:
        total = found.get("dependent_packages")
        _out(paint(f"{name} ({eco}): {total if total is not None else '?'} dependent packages; "
                   f"top {len(found['results'])} by downloads with a public repo:", "1", color))
        width = max([len(r["name"]) for r in found["results"]] + [10])
        for r in found["results"]:
            _out(f"  {r['name']:<{width}}  {r['downloads']:>12,}/mo  {paint(r['repo'], '2', color)}")
        if not found["results"]:
            _out("  (none found)")
    if args.write:
        path = args.write
        lib_rel = os.path.relpath(os.path.abspath(args.path), os.path.dirname(os.path.abspath(path)) or ".")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(render_config(found, lib_rel.replace(os.sep, "/")))
        print(f"wrote {path} with {len(found['results'])} dependents - review it, then run: revdeps run",
              file=sys.stderr)
    return 0


def cmd_run(args) -> int:
    color = use_color(args.color)
    try:
        cfg = load(args.config)
    except ConfigError as exc:
        print(f"revdeps: {exc}", file=sys.stderr)
        return 2
    cfg_abs = os.path.abspath(args.config)
    outdir = os.path.abspath(args.outdir or os.path.join(cfg.base_dir, ".revdeps"))
    workdir = os.path.abspath(args.workdir or os.path.join(
        tempfile.gettempdir(), "revdeps-" + hashlib.sha1(cfg_abs.encode()).hexdigest()[:10]))
    opts = Options(workdir=workdir, outdir=outdir, jobs=args.jobs, retries=args.retries, keep=args.keep,
                   candidate_only=args.candidate_only,
                   only=[s.strip() for s in args.only.split(",") if s.strip()] if args.only else None)
    lib = cfg.library
    n = len(opts.only) if opts.only else len(cfg.dependents)
    mode = "candidate only" if opts.candidate_only else "baseline vs candidate"
    _out(paint(f"revdeps: testing {n} dependent(s) of {lib.name or lib.path} ({lib.ecosystem}) — {mode}", "1", color))
    _out(paint(f"         candidate: {lib.path}", "2", color))

    def show(r):
        _out(line(r, color))
        for d in details(r, color):
            _out(d)

    try:
        results = run_all(cfg, opts, on_result=show)
    except PrepareError as exc:
        print(f"revdeps: {exc}", file=sys.stderr)
        return 2
    _out()
    _out(summary(results, color))
    _out(paint(f"logs: {os.path.join(outdir, 'logs')}" + (f"   checkouts kept in: {workdir}" if opts.keep else ""),
               "2", color))
    meta = {"tool": "revdeps", "version": __version__, "library": lib.name, "ecosystem": lib.ecosystem,
            "candidate": lib.path, "mode": "candidate-only" if opts.candidate_only else "baseline-vs-candidate"}
    if args.json:
        write_json(results, args.json, meta)
    md = markdown(results, lib.name or os.path.basename(lib.path))
    if args.markdown:
        with open(args.markdown, "w", encoding="utf-8") as fh:
            fh.write(md)
    gh = os.environ.get("GITHUB_STEP_SUMMARY")
    if gh and os.environ.get("GITHUB_ACTIONS") == "true" and not args.no_step_summary:
        with open(gh, "a", encoding="utf-8") as fh:
            fh.write(md + "\n")
    failing = set()
    for key in args.fail_on.split(","):
        key = key.strip()
        if key:
            failing |= FAIL_CHOICES[key]
    bad = sum(v for k, v in counts(results).items() if k in failing)
    return 1 if bad else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="revdeps",
        description="Run the test suites of projects that depend on your library against your unreleased change.",
    )
    p.add_argument("--version", action="version", version=f"revdeps {__version__}")
    sub = p.add_subparsers(dest="cmd")

    d = sub.add_parser("discover", help="list the most-downloaded dependents of a package (ecosyste.ms)")
    d.add_argument("package", nargs="?", help="package name (default: read package.json / pyproject.toml)")
    d.add_argument("-e", "--ecosystem", choices=["npm", "pypi"])
    d.add_argument("--path", default=".", help="library directory (for auto-detect and the written config)")
    d.add_argument("-n", "--top", type=int, default=10)
    d.add_argument("--write", nargs="?", const=DEFAULT_CONFIG, metavar="FILE",
                   help=f"write a config with the results (default {DEFAULT_CONFIG})")
    d.add_argument("--force", action="store_true", help="overwrite an existing config")
    d.add_argument("--json", action="store_true")
    d.add_argument("--color", choices=["auto", "always", "never"], default="auto")
    d.set_defaults(func=cmd_discover)

    r = sub.add_parser("run", help="test every dependent: released library (baseline) vs your change (candidate)")
    r.add_argument("-c", "--config", default=DEFAULT_CONFIG)
    r.add_argument("-j", "--jobs", type=int, default=2, help="dependents to test in parallel (default 2)")
    r.add_argument("--only", help="comma-separated dependent names")
    r.add_argument("--retries", type=int, default=1,
                   help="re-run a newly failing candidate test this many times to separate flakes (default 1)")
    r.add_argument("--candidate-only", action="store_true", help="skip the baseline run (faster, less precise)")
    r.add_argument("--workdir", help="where dependents are checked out (default: a folder in your temp dir)")
    r.add_argument("--outdir", help="logs, JUnit files and the packed candidate (default .revdeps next to the config)")
    r.add_argument("--keep", action="store_true", help="keep checkouts for debugging")
    r.add_argument("--json", metavar="FILE", help="write machine-readable results")
    r.add_argument("--markdown", metavar="FILE", help="write a Markdown report (PR comment / step summary)")
    r.add_argument("--no-step-summary", action="store_true", help="don't append to $GITHUB_STEP_SUMMARY")
    r.add_argument("--fail-on", default="broken",
                   help="comma list of statuses that make the exit code 1: broken,error,flaky (default broken)")
    r.add_argument("--color", choices=["auto", "always", "never"], default="auto")
    r.set_defaults(func=cmd_run)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    if getattr(args, "fail_on", None):
        bad = [k for k in args.fail_on.split(",") if k.strip() and k.strip() not in FAIL_CHOICES]
        if bad:
            parser.error(f"--fail-on: unknown status {bad[0]!r} (choose from broken,error,flaky)")
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
