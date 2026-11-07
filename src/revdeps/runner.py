"""Run each dependent's tests against the released library (baseline) and your local change (candidate)."""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from typing import Callable, Dict, List, Optional

from .config import Config, Dependent, Library, safe_dirname
from .junit import failed_tests

OK = "ok"
BROKEN = "broken"
FLAKY = "flaky"
FIXED = "fixed"
ALREADY_FAILING = "already-failing"
ERROR = "error"
SKIPPED = "skipped"
PASS = "pass"
FAIL = "fail"

WINDOWS = os.name == "nt"
IGNORE_COPY = shutil.ignore_patterns("node_modules", ".revdeps", ".revdeps-venv", "__pycache__", ".venv", ".tox")


@dataclass
class Phase:
    rc: Optional[int] = None
    seconds: float = 0.0
    timed_out: bool = False

    @property
    def passed(self) -> bool:
        return self.rc == 0 and not self.timed_out


@dataclass
class Result:
    name: str
    status: str
    source: str = ""
    commit: str = ""
    stage: str = ""
    detail: str = ""
    baseline: Optional[Phase] = None
    candidate: Optional[Phase] = None
    retries: int = 0
    new_failures: List[str] = field(default_factory=list)
    new_passes: List[str] = field(default_factory=list)
    messages: Dict[str, str] = field(default_factory=dict)
    log: str = ""
    tail: List[str] = field(default_factory=list)
    seconds: float = 0.0

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("baseline", "candidate"):
            if d[k] is not None:
                d[k]["passed"] = getattr(self, k).passed
        return d


@dataclass
class Options:
    workdir: str  # checkouts (keep outside your repo so parent configs don't leak in)
    outdir: str = ""  # logs + artifacts (default: workdir)
    jobs: int = 1
    retries: int = 1
    keep: bool = False
    candidate_only: bool = False
    only: Optional[List[str]] = None


class PrepareError(Exception):
    pass


def copy_tree(src: str, dest: str, patterns) -> None:
    """copytree that never descends into the folder it is copying into (workdir inside the source)."""
    dest_abs = os.path.abspath(dest)

    def ignore(d, names):
        out = set(patterns(d, names))
        for n in names:
            full = os.path.abspath(os.path.join(d, n))
            if dest_abs == full or dest_abs.startswith(full + os.sep):
                out.add(n)
        return out
    shutil.copytree(src, dest, ignore=ignore)


def rmtree(path: str) -> None:
    """shutil.rmtree that also removes read-only files (git objects on Windows)."""
    def onerror(func, p, _exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except OSError:
            pass
    if not os.path.isdir(path):
        return
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=onerror)
    else:  # pragma: no cover
        shutil.rmtree(path, onerror=onerror)


def quote(s: str) -> str:
    return subprocess.list2cmdline([s]) if WINDOWS else shlex.quote(s)


def fill(cmd: str, values: Dict[str, str]) -> str:
    for k, v in values.items():
        cmd = cmd.replace("{" + k + "}", quote(v))
    return cmd


def run_shell(cmd: str, cwd: str, env: Dict[str, str], timeout: int, log_path: str, label: str) -> Phase:
    start = time.monotonic()
    with open(log_path, "a", encoding="utf-8", errors="replace") as log:
        log.write(f"\n===== [{label}] $ {cmd}\n     (cwd: {cwd})\n")
        log.flush()
        kwargs = {}
        if not WINDOWS:
            kwargs["start_new_session"] = True
        proc = subprocess.Popen(cmd, shell=True, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, **kwargs)
        try:
            rc = proc.wait(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            timed_out = True
            if WINDOWS:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
            else:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            rc = proc.wait()
        secs = time.monotonic() - start
        log.write(f"===== [{label}] exit {rc}{' (timed out after %ds)' % timeout if timed_out else ''} "
                  f"in {secs:.1f}s\n")
    return Phase(rc=rc, seconds=round(secs, 2), timed_out=timed_out)


FAIL_HINT = re.compile(r"\b\d+ (failing|failed)\b|^\s*(FAIL|✖|✗|●|not ok)\b|AssertionError|^E\s|Error:|ERR!|npm error")


def tail(path: str, n: int = 15, section: str = "") -> List[str]:
    """Most useful lines of a log: from the first failure hint in the last `section`, else the last n lines."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return []
    if section:
        marker = f"===== [{section}] $"
        if marker in text:
            text = text[text.rindex(marker):]
    lines = [ln for ln in text.splitlines() if not ln.startswith(("===== [", "     (cwd: ")) and ln.strip()]
    for i, ln in enumerate(lines):
        if FAIL_HINT.search(ln):
            return lines[i:i + n]
    return lines[-n:]


def prepare_library(lib: Library, artifacts: str, log_path: str) -> Dict[str, str]:
    """Build the candidate once. Returns placeholder values ({lib}, {tarball})."""
    os.makedirs(artifacts, exist_ok=True)
    env = dict(os.environ)
    values = {"lib": lib.path}
    if lib.build:
        ph = run_shell(lib.build, lib.path, env, 1800, log_path, "library build")
        if not ph.passed:
            raise PrepareError(f"library build failed ({lib.build!r}); see {log_path}")
    if lib.ecosystem == "npm":
        for old in os.listdir(artifacts):
            if old.endswith(".tgz"):
                os.remove(os.path.join(artifacts, old))
        cmd = f"npm pack --json --pack-destination {quote(artifacts)}"
        out_path = os.path.join(artifacts, "pack.json")
        with open(out_path, "w", encoding="utf-8") as out, open(log_path, "a", encoding="utf-8") as log:
            log.write(f"\n===== [library pack] $ {cmd}\n")
            log.flush()
            rc = subprocess.run(cmd, shell=True, cwd=lib.path, stdout=out, stderr=log, env=env).returncode
        if rc != 0:
            raise PrepareError(f"npm pack failed in {lib.path}; see {log_path}")
        tgz = [f for f in os.listdir(artifacts) if f.endswith(".tgz")]
        if len(tgz) != 1:
            raise PrepareError(f"npm pack produced {len(tgz)} tarballs in {artifacts}")
        values["tarball"] = os.path.join(artifacts, tgz[0])
    return values


def default_commands(lib: Library, project_dir: str) -> Dict[str, str]:
    if lib.ecosystem == "npm":
        # Use the project's package manager when it is installed; otherwise npm can usually cope.
        if os.path.isfile(os.path.join(project_dir, "pnpm-lock.yaml")) and shutil.which("pnpm"):
            return {"install": "pnpm install --frozen-lockfile", "inject": "pnpm add {tarball}",
                    "test": "pnpm test"}
        if os.path.isfile(os.path.join(project_dir, "yarn.lock")) and shutil.which("yarn"):
            return {"install": "yarn install", "inject": "yarn add {tarball}", "test": "yarn test"}
        if not os.path.isfile(os.path.join(project_dir, "package-lock.json")) and any(
                os.path.isfile(os.path.join(project_dir, f)) for f in ("yarn.lock", "pnpm-lock.yaml")):
            # yarn/pnpm project but that tool isn't installed: npm with relaxed peer-dep resolution
            return {"install": "npm install --no-audit --no-fund --legacy-peer-deps",
                    "inject": "npm install --no-save --no-audit --no-fund --legacy-peer-deps {tarball}",
                    "test": "npm test"}
        lock = os.path.isfile(os.path.join(project_dir, "package-lock.json"))
        return {"install": "npm ci --no-audit --no-fund || npm install --no-audit --no-fund" if lock else "npm install --no-audit --no-fund",
                "inject": "npm install --no-save --no-audit --no-fund {tarball}",
                "test": "npm test"}
    return {"install": "{python} -m pip install -q -e . pytest",
            "inject": "{python} -m pip install -q --force-reinstall --no-deps {lib}",
            "test": "{python} -m pytest -q -p no:cacheprovider --rootdir={workdir} --junitxml={junit}"}


def _venv_bin(venv_dir: str) -> str:
    return os.path.join(venv_dir, "Scripts" if WINDOWS else "bin")


def fetch_source(dep: Dependent, dest: str, log_path: str) -> str:
    """Clone or copy the dependent into dest. Returns a short commit id ('' if unknown)."""
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    if dep.path:
        if not os.path.isdir(dep.path):
            raise PrepareError(f"path {dep.path} does not exist")
        copy_tree(dep.path, dest, IGNORE_COPY)
    else:
        q = quote
        cmd = f"git clone --quiet --depth 1 {'--branch ' + q(dep.ref) + ' ' if dep.ref else ''}{q(dep.repo)} {q(dest)}"
        ph = run_shell(cmd, os.path.dirname(dest), env, 600, log_path, "clone")
        if not ph.passed and dep.ref:
            rmtree(dest)
            cmd = f"git clone --quiet --filter=blob:none {q(dep.repo)} {q(dest)} && git -C {q(dest)} checkout --quiet {q(dep.ref)}"
            ph = run_shell(cmd, os.path.dirname(dest), env, 900, log_path, "clone")
        if not ph.passed:
            raise PrepareError(f"git clone failed for {dep.repo}")
    if os.path.isdir(os.path.join(dest, ".git")):
        p = subprocess.run(["git", "-C", dest, "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
        if p.returncode == 0:
            return p.stdout.strip()
    return ""


def run_dependent(dep: Dependent, lib: Library, values: Dict[str, str], opts: Options,
                  config_dir: str = "") -> Result:
    config_dir = config_dir or os.getcwd()
    started = time.monotonic()
    sd = safe_dirname(dep.name)
    logs = os.path.join(opts.outdir or opts.workdir, "logs")
    os.makedirs(logs, exist_ok=True)
    log_path = os.path.join(logs, f"{sd}.log")
    res = Result(name=dep.name, status=ERROR, source=dep.repo or dep.path or "", log=log_path)
    if dep.skip:
        res.status = SKIPPED
        return res
    open(log_path, "w").close()
    root = os.path.join(opts.workdir, "work", sd)
    rmtree(root)
    os.makedirs(os.path.dirname(root), exist_ok=True)
    try:
        try:
            res.commit = fetch_source(dep, root, log_path)
        except PrepareError as exc:
            res.stage, res.detail = "clone", str(exc)
            return res
        project = os.path.join(root, dep.subdir) if dep.subdir else root
        if not os.path.isdir(project):
            res.stage, res.detail = "clone", f"subdir {dep.subdir!r} not found"
            return res
        cmds = default_commands(lib, project)
        for key in ("install", "inject", "test"):
            if getattr(dep, key):
                cmds[key] = getattr(dep, key)

        env = dict(os.environ)
        env.update({"CI": "true", "REVDEPS": "1", "REVDEPS_DEPENDENT": dep.name})
        env.pop("NODE_OPTIONS", None)
        python = dep.python or sys.executable
        if lib.ecosystem == "pypi" and dep.venv:
            venv_dir = os.path.join(project, ".revdeps-venv")
            argv = [python, "-m", "venv", *dep.venv_args, venv_dir]
            ph = run_shell(subprocess.list2cmdline(argv) if WINDOWS else " ".join(quote(a) for a in argv),
                           project, env, 300, log_path, "venv")
            if not ph.passed:
                res.stage, res.detail = "venv", "could not create virtualenv"
                return res
            bindir = _venv_bin(venv_dir)
            env["VIRTUAL_ENV"] = venv_dir
            env["PATH"] = bindir + os.pathsep + env.get("PATH", "")
            env.pop("PYTHONHOME", None)
            python = os.path.join(bindir, "python.exe" if WINDOWS else "python")
        env.update(dep.env)

        lib_values = dict(values)
        if lib.ecosystem == "pypi" and os.path.isdir(lib.path):
            # Each dependent installs from its own copy: parallel pip builds of one source tree collide
            # (shared build/ dir), and this keeps build artifacts out of your checkout.
            lib_copy = os.path.join(opts.workdir, "work", sd + ".lib")
            rmtree(lib_copy)
            copy_tree(lib.path, lib_copy, shutil.ignore_patterns(
                ".git", "build", "dist", "*.egg-info", ".revdeps", ".revdeps-venv", ".venv", "venv",
                "node_modules", "__pycache__", ".tox", ".nox", ".pytest_cache"))
            lib_values["lib"] = lib_copy

        def vals(phase: str) -> Dict[str, str]:
            v = dict(lib_values)
            v.update({"python": python, "workdir": project, "config_dir": config_dir, "junit": os.path.join(logs, f"{sd}-{phase}.xml")})
            return v

        for stale in ("baseline", "candidate"):
            p = vals(stale)["junit"]
            if os.path.exists(p):
                os.remove(p)

        ph = run_shell(fill(cmds["install"], vals("install")), project, env, dep.timeout, log_path, "install")
        if not ph.passed:
            res.stage, res.detail = "install", f"install failed (exit {ph.rc})"
            res.tail = tail(log_path)
            return res

        if not opts.candidate_only:
            res.baseline = run_shell(fill(cmds["test"], vals("baseline")), project, env, dep.timeout,
                                     log_path, "baseline test")

        ph = run_shell(fill(cmds["inject"], vals("inject")), project, env, dep.timeout, log_path,
                       "inject candidate")
        if not ph.passed:
            res.status, res.stage = BROKEN, "inject"
            res.detail = f"installing your candidate build failed (exit {ph.rc})"
            res.tail = tail(log_path, section="inject candidate")
            return res

        res.candidate = run_shell(fill(cmds["test"], vals("candidate")), project, env, dep.timeout,
                                  log_path, "candidate test")
        base_fail = failed_tests(vals("baseline")["junit"])
        cand_fail = failed_tests(vals("candidate")["junit"])
        if base_fail is not None and cand_fail is not None:
            res.new_failures = sorted(set(cand_fail) - set(base_fail))
            res.new_passes = sorted(set(base_fail) - set(cand_fail))
        elif cand_fail is not None and opts.candidate_only:
            res.new_failures = sorted(cand_fail)
        if cand_fail:
            res.messages = {t: cand_fail[t] for t in res.new_failures if cand_fail.get(t)}

        if opts.candidate_only:
            res.status = PASS if res.candidate.passed else FAIL
            if not res.candidate.passed:
                res.tail = tail(log_path, section="candidate test")
            return res

        b, c = res.baseline.passed, res.candidate.passed
        if b and c:
            res.status = OK
        elif not b and c:
            res.status = FIXED
        elif not b and not c:
            res.status = ALREADY_FAILING
            if res.baseline.rc == 127:
                res.detail = "test command not found (exit 127): install the tool or set test = ..."
            if res.new_failures:
                res.status = BROKEN
                res.detail = "already failing before your change, but more tests fail with it"
                res.tail = tail(log_path, section="candidate test")
        else:
            res.status = BROKEN
            res.tail = tail(log_path, section="candidate test")
            for attempt in range(1, opts.retries + 1):
                res.retries = attempt
                ph = run_shell(fill(cmds["test"], vals(f"retry{attempt}")), project, env, dep.timeout,
                               log_path, f"candidate retry {attempt}")
                if ph.passed:
                    res.status = FLAKY
                    res.detail = f"failed once, passed on retry {attempt}"
                    res.tail = []
                    break
        if res.candidate.timed_out and res.status == BROKEN:
            res.detail = f"candidate test timed out after {dep.timeout}s"
        return res
    finally:
        res.seconds = round(time.monotonic() - started, 1)
        if not opts.keep:
            rmtree(root)
            rmtree(os.path.join(opts.workdir, "work", sd + ".lib"))


def run_all(cfg: Config, opts: Options, on_result: Optional[Callable[[Result], None]] = None) -> List[Result]:
    os.makedirs(opts.workdir, exist_ok=True)
    deps = cfg.dependents
    if opts.only:
        wanted = set(opts.only)
        missing = wanted - {d.name for d in deps}
        if missing:
            raise PrepareError(f"unknown dependent(s): {', '.join(sorted(missing))}")
        deps = [d for d in deps if d.name in wanted]
    if not deps:
        raise PrepareError("no dependents configured (run: revdeps discover --write)")
    out = opts.outdir or opts.workdir
    os.makedirs(os.path.join(out, "logs"), exist_ok=True)
    lib_log = os.path.join(out, "logs", "_library.log")
    open(lib_log, "w").close()
    values = prepare_library(cfg.library, os.path.join(out, "artifacts"), lib_log)
    lock = threading.Lock()
    results: Dict[str, Result] = {}

    def one(dep: Dependent) -> Result:
        try:
            r = run_dependent(dep, cfg.library, values, opts, cfg.base_dir)
        except Exception as exc:  # never let one dependent kill the run
            r = Result(name=dep.name, status=ERROR, stage="internal", detail=f"{type(exc).__name__}: {exc}")
        with lock:
            results[dep.name] = r
            if on_result:
                on_result(r)
        return r

    with ThreadPoolExecutor(max_workers=max(1, opts.jobs)) as pool:
        list(pool.map(one, deps))
    return [results[d.name] for d in deps]


def write_json(results: List[Result], path: str, meta: dict) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({**meta, "results": [r.to_dict() for r in results]}, fh, indent=2)
        fh.write("\n")
