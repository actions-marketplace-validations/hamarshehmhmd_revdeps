import json
import os
import shutil
import subprocess
import sys

import pytest

from conftest import EXAMPLES, needs_network, needs_node, pycmd
from revdeps import __version__, cli


def copy_example(tmp_path, name):
    dst = tmp_path / name
    shutil.copytree(os.path.join(EXAMPLES, name), dst, ignore=shutil.ignore_patterns(".revdeps", "node_modules"))
    return dst


@needs_node
def test_npm_example_end_to_end(tmp_path, capsys, monkeypatch):
    ex = copy_example(tmp_path, "npm")
    monkeypatch.chdir(ex)
    rc = cli.main(["run", "--color", "never", "--json", "out.json", "--markdown", "out.md", "-j", "4",
                   "--workdir", str(tmp_path / "w")])
    out = capsys.readouterr().out
    assert rc == 1
    data = json.loads((ex / "out.json").read_text(encoding="utf-8"))
    status = {r["name"]: r["status"] for r in data["results"]}
    assert status == {"blog-engine": "ok", "url-shortener": "broken", "recipe-site": "fixed",
                      "legacy-cms": "already-failing"}
    broken = next(r for r in data["results"] if r["name"] == "url-shortener")
    assert broken["new_failures"] == ["short slug is capped at 12 chars", "short slug never ends with a dash"]
    assert broken["baseline"]["passed"] and not broken["candidate"]["passed"]
    fixed = next(r for r in data["results"] if r["name"] == "recipe-site")
    assert fixed["new_passes"] == ["accented recipe names"]
    assert "4 dependents: 1 broken, 1 ok, 1 fixed, 1 already failing" in out
    assert "| ❌ BROKEN | url-shortener |" in (ex / "out.md").read_text(encoding="utf-8")
    assert (ex / ".revdeps" / "artifacts").is_dir()
    # --fail-on: nothing counts as failure when we only fail on errors
    rc = cli.main(["run", "--color", "never", "--fail-on", "error", "--only", "blog-engine,url-shortener",
                   "--workdir", str(tmp_path / "w")])
    assert rc == 0


@needs_node
def test_npm_example_candidate_only(tmp_path, capsys, monkeypatch):
    ex = copy_example(tmp_path, "npm")
    monkeypatch.chdir(ex)
    rc = cli.main(["run", "--candidate-only", "--color", "never", "--only", "blog-engine,legacy-cms",
                   "--workdir", str(tmp_path / "w")])
    out = capsys.readouterr().out
    assert rc == 1 and "1 fail, 1 pass" in out and "candidate only" in out


@needs_node
@needs_network
def test_pypi_example_end_to_end(tmp_path, capsys):
    ex = copy_example(tmp_path, "pypi")
    rc = cli.main(["run", "-c", str(ex / "revdeps.toml"), "--color", "never", "--json", str(tmp_path / "o.json"),
                   "--workdir", str(tmp_path / "w")])
    data = json.loads((tmp_path / "o.json").read_text(encoding="utf-8"))
    assert rc == 1
    status = {r["name"]: r["status"] for r in data["results"]}
    assert status == {"deploy-tool": "ok", "feature-flags": "broken"}
    ff = data["results"][1]
    # the library checkout must stay clean: builds happen in per-dependent copies
    assert not (ex / "tinyconf" / "build").exists()
    assert ff["new_failures"] == ["test_flags::test_flag_enabled"]
    assert "'bool' object has no attribute 'lower'" in ff["messages"]["test_flags::test_flag_enabled"]


def test_step_summary(tmp_path, monkeypatch, capsys):
    from revdeps import runner
    monkeypatch.setattr(runner, "prepare_library", lambda lib, artifacts, log: {"lib": "/x"})
    dep = tmp_path / "dep"
    dep.mkdir()
    (tmp_path / "revdeps.toml").write_text(
        '[library]\necosystem = "pypi"\nname = "x"\n'
        '[[dependent]]\nname = "d"\npath = "dep"\nvenv = false\n'
        f'install = {json.dumps(pycmd("pass"))}\ninject = {json.dumps(pycmd("pass"))}\n'
        f'test = {json.dumps(pycmd("pass"))}\n')
    summ = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summ))
    rc = cli.main(["run", "-c", str(tmp_path / "revdeps.toml"), "--workdir", str(tmp_path / "w")])
    assert rc == 0 and "| ✅ ok | d |" in summ.read_text(encoding="utf-8")


def test_config_errors_exit_2(tmp_path, capsys):
    assert cli.main(["run", "-c", str(tmp_path / "missing.toml")]) == 2
    assert "not found" in capsys.readouterr().err
    (tmp_path / "revdeps.toml").write_text('[library]\necosystem = "npm"\n[[dependent]]\nname="a"\npath="."\n')
    assert cli.main(["run", "-c", str(tmp_path / "revdeps.toml"), "--only", "zzz"]) == 2
    assert "unknown dependent" in capsys.readouterr().err


def test_bad_fail_on_and_no_command(capsys):
    with pytest.raises(SystemExit):
        cli.main(["run", "--fail-on", "broken,nope"])
    assert cli.main([]) == 2


def test_version(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--version"])
    assert __version__ in capsys.readouterr().out


FOUND = {"package": "mylib", "ecosystem": "npm", "repository": None, "dependent_packages": 2,
         "skipped_without_repo": 0,
         "results": [{"name": "big", "repo": "https://github.com/a/big", "downloads": 5000, "dependents": 3}]}


def test_discover_autodetect_write_force(tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cli, "dependents", lambda name, eco, top: calls.append((name, eco, top)) or FOUND)
    (tmp_path / "package.json").write_text('{"name": "mylib"}')
    monkeypatch.chdir(tmp_path)
    assert cli.main(["discover", "--write", "--color", "never", "-n", "3"]) == 0
    assert calls == [("mylib", "npm", 3)]
    out = capsys.readouterr()
    assert "mylib (npm): 2 dependent packages" in out.out and "5,000/mo" in out.out
    text = (tmp_path / "revdeps.toml").read_text(encoding="utf-8")
    assert 'repo = "https://github.com/a/big.git"' in text and 'path = "."' in text
    assert cli.main(["discover", "--write"]) == 2
    assert cli.main(["discover", "--write", "--force", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["package"] == "mylib"


def test_discover_needs_a_package(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["discover"]) == 2
    from revdeps.discover import DiscoverError

    def boom(*a, **k):
        raise DiscoverError("package not found")
    monkeypatch.setattr(cli, "dependents", boom)
    assert cli.main(["discover", "nope", "-e", "npm"]) == 2
    assert "package not found" in capsys.readouterr().err


@needs_network
@pytest.mark.skipif(not os.environ.get("REVDEPS_LIVE"), reason="set REVDEPS_LIVE=1 to hit ecosyste.ms")
def test_live_discover(capsys):
    assert cli.main(["discover", "escape-html", "-e", "npm", "-n", "3", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert len(data["results"]) == 3 and all(r["repo"].startswith("https://") for r in data["results"])


def test_module_entrypoint():
    p = subprocess.run([sys.executable, "-m", "revdeps", "--version"], capture_output=True, text=True)
    assert p.returncode == 0 and "revdeps" in p.stdout
