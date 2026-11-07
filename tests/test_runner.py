import os
import subprocess
import sys

import pytest

from conftest import PY, pycmd
from revdeps.config import Config, Dependent, Library
from revdeps import runner
from revdeps.runner import (ALREADY_FAILING, BROKEN, ERROR, FAIL, FIXED, FLAKY, OK, PASS, SKIPPED, Options,
                            PrepareError, default_commands, fill, run_all, run_dependent, tail)

SCRIPT = r'''
import os, sys, time
mode = open("mode.txt").read().strip()
cand = os.path.exists("INJECTED")
junit = sys.argv[1] if len(sys.argv) > 1 else ""
def write_junit(failed):
    if not junit:
        return
    cases = "".join(
        '<testcase classname="t" name="%s">%s</testcase>' % (n, '<failure message="boom %s"/>' % n if n in failed else "")
        for n in ("a", "b", "c"))
    open(junit, "w").write("<testsuite>%s</testsuite>" % cases)
if mode == "ok":
    sys.exit(0)
if mode == "broken":
    sys.exit(1 if cand else 0)
if mode == "fixed":
    sys.exit(0 if cand else 1)
if mode == "already":
    sys.exit(1)
if mode == "flaky":
    if not cand:
        sys.exit(0)
    n = int(open("count").read()) if os.path.exists("count") else 0
    open("count", "w").write(str(n + 1))
    sys.exit(1 if n == 0 else 0)
if mode == "junit":
    failed = {"a", "b"} if cand else {"a"}
    write_junit(failed)
    sys.exit(1)
if mode == "junit-ok":
    failed = set() if cand else {"c"}
    write_junit(failed)
    sys.exit(len(failed))
if mode == "env":
    sys.exit(0 if os.environ.get("CI") == "true" and os.environ.get("MY_VAR") == "x" else 1)
if mode == "sleep":
    time.sleep(30 if cand else 0)
    sys.exit(0)
'''


def q(s):
    return runner.quote(s)


def make_dep(tmp_path, mode, name="dep", **kw):
    d = tmp_path / ("src-" + name)
    d.mkdir()
    (d / "mode.txt").write_text(mode)
    (d / "t.py").write_text(SCRIPT)
    args = dict(name=name, path=str(d), install=pycmd("pass"),
                inject=pycmd("open('INJECTED','w').close()"), test=f"{q(PY)} t.py {{junit}}")
    args.update(kw)
    return Dependent(**args)


LIB = Library(ecosystem="npm", path=".", name="lib")


def run(tmp_path, dep, **opt):
    opts = Options(workdir=str(tmp_path / "work"), outdir=str(tmp_path / "out"), **opt)
    return run_dependent(dep, LIB, {"tarball": "/x.tgz", "lib": "/lib"}, opts, str(tmp_path))


@pytest.mark.parametrize("mode,status", [
    ("ok", OK), ("broken", BROKEN), ("fixed", FIXED), ("already", ALREADY_FAILING),
])
def test_classification(tmp_path, mode, status):
    r = run(tmp_path, make_dep(tmp_path, mode))
    assert r.status == status, open(r.log).read()
    assert r.baseline is not None and r.candidate is not None
    assert os.path.isfile(r.log)
    assert not (tmp_path / "work" / "work" / "dep").exists()  # cleaned up


def test_broken_keeps_log_tail_and_retries(tmp_path):
    r = run(tmp_path, make_dep(tmp_path, "broken"), retries=2)
    assert r.status == BROKEN and r.retries == 2
    assert "candidate retry 2" in open(r.log).read()


def test_flaky_is_separated(tmp_path):
    r = run(tmp_path, make_dep(tmp_path, "flaky"))
    assert r.status == FLAKY and "retry 1" in r.detail and r.retries == 1


def test_flaky_without_retries_is_broken(tmp_path):
    r = run(tmp_path, make_dep(tmp_path, "flaky"), retries=0)
    assert r.status == BROKEN


def test_junit_new_failures_turn_already_failing_into_broken(tmp_path):
    r = run(tmp_path, make_dep(tmp_path, "junit"))
    assert r.status == BROKEN
    assert r.new_failures == ["t::b"] and r.messages == {"t::b": "boom b"}
    assert "more tests fail" in r.detail


def test_junit_new_passes(tmp_path):
    r = run(tmp_path, make_dep(tmp_path, "junit-ok"))
    assert r.status == FIXED and r.new_passes == ["t::c"]


def test_candidate_only(tmp_path):
    r = run(tmp_path, make_dep(tmp_path, "broken"), candidate_only=True)
    assert r.status == FAIL and r.baseline is None
    r2 = run(tmp_path, make_dep(tmp_path, "ok", name="d2"), candidate_only=True)
    assert r2.status == PASS


def test_candidate_only_lists_all_junit_failures(tmp_path):
    r = run(tmp_path, make_dep(tmp_path, "junit"), candidate_only=True)
    assert r.status == FAIL and r.new_failures == ["t::a", "t::b"]


def test_install_failure_is_error(tmp_path):
    r = run(tmp_path, make_dep(tmp_path, "ok", install=pycmd("import sys; print('E404 nope'); sys.exit(3)")))
    assert r.status == ERROR and r.stage == "install" and "exit 3" in r.detail
    assert any("E404" in t for t in r.tail)


def test_inject_failure_is_broken(tmp_path):
    r = run(tmp_path, make_dep(tmp_path, "ok", inject=pycmd("import sys; sys.exit(1)")))
    assert r.status == BROKEN and r.stage == "inject"


def test_env_and_ci_flag(tmp_path):
    r = run(tmp_path, make_dep(tmp_path, "env", env={"MY_VAR": "x"}))
    assert r.status == OK


def test_timeout(tmp_path):
    r = run(tmp_path, make_dep(tmp_path, "sleep", timeout=2), retries=0)
    assert r.status == BROKEN and r.candidate.timed_out and "timed out" in r.detail


def test_skip_and_missing_path_and_subdir(tmp_path):
    assert run(tmp_path, make_dep(tmp_path, "ok", skip=True)).status == SKIPPED
    r = run(tmp_path, Dependent(name="ghost", path=str(tmp_path / "nope")))
    assert r.status == ERROR and r.stage == "clone"
    r = run(tmp_path, make_dep(tmp_path, "ok", name="sd", subdir="packages/x"))
    assert r.status == ERROR and "subdir" in r.detail


def test_subdir_and_keep(tmp_path):
    d = make_dep(tmp_path, "ok", name="mono")
    sub = os.path.join(d.path, "packages", "core")
    os.makedirs(sub)
    for f in ("mode.txt", "t.py"):
        os.replace(os.path.join(d.path, f), os.path.join(sub, f))
    d.subdir = "packages/core"
    r = run(tmp_path, d, keep=True)
    assert r.status == OK
    assert (tmp_path / "work" / "work" / "mono" / "packages" / "core" / "INJECTED").exists()


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                            GIT_COMMITTER_EMAIL="t@t"))


def test_clone_from_git_repo_with_ref(tmp_path):
    d = make_dep(tmp_path, "ok", name="gitdep")
    git("init", "-q", "-b", "main", cwd=d.path)
    git("add", ".", cwd=d.path)
    git("commit", "-q", "-m", "one", cwd=d.path)
    git("tag", "v1", cwd=d.path)
    (open(os.path.join(d.path, "mode.txt"), "w")).write("broken")
    git("commit", "-q", "-am", "two", cwd=d.path)
    repo = d.path
    dep = Dependent(name="gitdep", repo=repo, install=d.install, inject=d.inject, test=d.test)
    assert run(tmp_path, dep).status == BROKEN
    sha = subprocess.run(["git", "rev-parse", "--short", "v1"], cwd=repo, capture_output=True, text=True).stdout.strip()
    dep.ref = "v1"
    r = run(tmp_path, dep)
    assert r.status == OK and r.commit == sha
    dep.ref = sha  # a bare sha can't be passed to --branch; falls back to full clone + checkout
    r = run(tmp_path, dep)
    assert r.status == OK and r.commit == sha


def test_clone_failure(tmp_path):
    r = run(tmp_path, Dependent(name="nope", repo=str(tmp_path / "missing-repo")))
    assert r.status == ERROR and r.stage == "clone"


def test_fill_quotes_values():
    out = fill("npm i {tarball} && echo {missing}", {"tarball": "/a b/c.tgz"})
    assert "{missing}" in out
    assert ("'/a b/c.tgz'" in out) if os.name != "nt" else ('"/a b/c.tgz"' in out)


def test_default_commands(tmp_path, monkeypatch):
    monkeypatch.setattr(runner.shutil, "which", lambda name: "/usr/bin/" + name)
    npm = Library("npm", ".")
    assert default_commands(npm, str(tmp_path))["install"].startswith("npm install")
    (tmp_path / "package-lock.json").write_text("{}")
    c = default_commands(npm, str(tmp_path))
    assert c["install"].startswith("npm ci") and "{tarball}" in c["inject"] and c["test"] == "npm test"
    (tmp_path / "yarn.lock").write_text("")
    assert default_commands(npm, str(tmp_path))["inject"] == "yarn add {tarball}"
    (tmp_path / "pnpm-lock.yaml").write_text("")
    assert default_commands(npm, str(tmp_path))["install"] == "pnpm install --frozen-lockfile"
    py = default_commands(Library("pypi", "."), str(tmp_path))
    monkeypatch.setattr(runner.shutil, "which", lambda name: None)
    assert default_commands(npm, str(tmp_path))["install"].startswith("npm ci")
    os.remove(tmp_path / "package-lock.json")
    assert "--legacy-peer-deps" in default_commands(npm, str(tmp_path))["install"]
    assert "{lib}" in py["inject"] and "--junitxml={junit}" in py["test"] and "{python}" in py["install"]


def test_tail_skips_markers(tmp_path):
    p = tmp_path / "l.log"
    p.write_text("===== [x] $ cmd\n     (cwd: /x)\n\nhello\nworld\n")
    assert tail(str(p)) == ["hello", "world"]
    assert tail(str(tmp_path / "nope")) == []
    p.write_text("===== [install] $ a\nnoise\n===== [candidate test] $ t\nok 1\n  1 failing\n  1) boom\nat x\n")
    assert tail(str(p), section="candidate test") == ["  1 failing", "  1) boom", "at x"]
    assert tail(str(p), n=2, section="nope") == ["  1 failing", "  1) boom"]


def test_run_all_parallel_order_and_filters(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "prepare_library", lambda lib, artifacts, log: {"tarball": "/x.tgz"})
    deps = [make_dep(tmp_path, m, name=m) for m in ("ok", "broken", "fixed")]
    cfg = Config(library=LIB, dependents=deps, base_dir=str(tmp_path))
    seen = []
    res = run_all(cfg, Options(workdir=str(tmp_path / "w"), jobs=3), on_result=lambda r: seen.append(r.name))
    assert [r.name for r in res] == ["ok", "broken", "fixed"]
    assert [r.status for r in res] == [OK, BROKEN, FIXED] and sorted(seen) == sorted(["ok", "broken", "fixed"])
    only = run_all(cfg, Options(workdir=str(tmp_path / "w2"), only=["fixed"]))
    assert [r.name for r in only] == ["fixed"]
    with pytest.raises(PrepareError, match="unknown"):
        run_all(cfg, Options(workdir=str(tmp_path / "w3"), only=["nope"]))
    with pytest.raises(PrepareError, match="no dependents"):
        run_all(Config(library=LIB, dependents=[], base_dir="."), Options(workdir=str(tmp_path / "w4")))


def test_run_all_survives_internal_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "prepare_library", lambda lib, artifacts, log: {})

    def boom(*a, **k):
        raise RuntimeError("kaput")
    monkeypatch.setattr(runner, "run_dependent", boom)
    cfg = Config(library=LIB, dependents=[Dependent(name="x", path=".")], base_dir=str(tmp_path))
    (r,) = run_all(cfg, Options(workdir=str(tmp_path / "w")))
    assert r.status == ERROR and r.stage == "internal" and "kaput" in r.detail


def test_library_build_failure(tmp_path):
    lib = Library("pypi", str(tmp_path), build=pycmd("import sys; sys.exit(2)"))
    log = tmp_path / "lib.log"
    log.write_text("")
    with pytest.raises(PrepareError, match="build failed"):
        runner.prepare_library(lib, str(tmp_path / "art"), str(log))
    ok = Library("pypi", str(tmp_path), build=pycmd("pass"))
    assert runner.prepare_library(ok, str(tmp_path / "art"), str(log)) == {"lib": str(tmp_path)}


def test_rmtree_removes_read_only_files(tmp_path):
    import stat
    d = tmp_path / "ro"
    (d / "sub").mkdir(parents=True)
    f = d / "sub" / "pack"
    f.write_text("x")
    os.chmod(f, stat.S_IREAD)
    runner.rmtree(str(d))
    assert not d.exists()
    runner.rmtree(str(d))  # missing is fine


def test_pypi_dependent_gets_private_library_copy(tmp_path):
    lib = tmp_path / "mylib"
    (lib / "build").mkdir(parents=True)
    (lib / "pyproject.toml").write_text("[project]\nname='mylib'\n")
    dep = make_dep(tmp_path, "ok", venv=False,
                   inject=pycmd("import sys, os; sys.exit(0 if os.path.isfile(os.path.join(sys.argv[1], 'pyproject.toml')) "
                                "and not os.path.isdir(os.path.join(sys.argv[1], 'build')) else 1)") + " {lib}")
    opts = Options(workdir=str(tmp_path / "work"), outdir=str(tmp_path / "out"), keep=True)
    r = run_dependent(dep, Library("pypi", str(lib)), {"lib": str(lib)}, opts, str(tmp_path))
    assert r.status == OK, open(r.log).read()
    assert (tmp_path / "work" / "work" / "dep.lib" / "pyproject.toml").exists()


def test_path_dependent_containing_workdir_does_not_recurse(tmp_path):
    d = make_dep(tmp_path, "ok", name="self")
    opts = Options(workdir=os.path.join(d.path, ".scratch"), outdir=str(tmp_path / "out"))
    r = run_dependent(d, LIB, {"tarball": "/x.tgz"}, opts, str(tmp_path))
    assert r.status == OK, open(r.log).read()
