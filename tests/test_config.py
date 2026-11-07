import json
import os

import pytest

from revdeps.config import ConfigError, detect_package, load, parse, safe_dirname


def lib_dir(tmp_path, kind="npm", name="mylib"):
    d = tmp_path / "lib"
    d.mkdir()
    if kind == "npm":
        (d / "package.json").write_text(json.dumps({"name": name}))
    elif kind == "pypi":
        (d / "pyproject.toml").write_text(f'[project]\nname = "{name}"\n')
    return d


def test_detect_npm_and_pypi(tmp_path):
    d = lib_dir(tmp_path, "npm", "left-pad")
    assert detect_package(str(d)) == ("npm", "left-pad")
    p = tmp_path / "py"
    p.mkdir()
    (p / "pyproject.toml").write_text('[project]\nname = "attrs"\n')
    assert detect_package(str(p)) == ("pypi", "attrs")
    s = tmp_path / "setup"
    s.mkdir()
    (s / "setup.py").write_text("")
    assert detect_package(str(s)) == ("pypi", None)
    assert detect_package(str(tmp_path)) == (None, None)


def test_detect_broken_files(tmp_path):
    (tmp_path / "package.json").write_text("{nope")
    assert detect_package(str(tmp_path)) == ("npm", None)


def test_parse_merges_defaults_and_resolves_paths(tmp_path):
    lib_dir(tmp_path)
    cfg = parse({
        "library": {"path": "lib"},
        "defaults": {"timeout": 60, "test": "npm run test:ci"},
        "dependent": [
            {"repo": "https://github.com/acme/web-app.git"},
            {"name": "local", "path": "../elsewhere", "test": "make test", "env": {"A": 1}},
        ],
    }, str(tmp_path))
    assert cfg.library.ecosystem == "npm" and cfg.library.name == "mylib"
    assert cfg.library.path == str(tmp_path / "lib")
    a, b = cfg.dependents
    assert a.name == "web-app" and a.timeout == 60 and a.test == "npm run test:ci"
    assert b.test == "make test" and b.env == {"A": "1"}
    assert b.path == os.path.normpath(str(tmp_path / ".." / "elsewhere"))


def test_explicit_ecosystem_and_name(tmp_path):
    cfg = parse({"library": {"ecosystem": "pypi", "name": "x", "build": "make"}, "dependent": []}, str(tmp_path))
    assert (cfg.library.ecosystem, cfg.library.name, cfg.library.build) == ("pypi", "x", "make")


@pytest.mark.parametrize("data,msg", [
    ({"library": {}}, "ecosystem must be one of"),
    ({"library": {"ecosystem": "cargo"}}, "ecosystem must be one of"),
    ({"library": {"ecosystem": "npm"}, "dependent": [{"name": "a"}]}, "exactly one of repo or path"),
    ({"library": {"ecosystem": "npm"}, "dependent": [{"name": "a", "repo": "r", "path": "p"}]}, "exactly one"),
    ({"library": {"ecosystem": "npm"}, "dependent": [{"name": "a", "path": "p"}, {"name": "a", "path": "q"}]},
     "duplicate"),
    ({"library": {"ecosystem": "npm"}, "dependent": [{"name": "a", "path": "p", "tset": "x"}]}, "unknown key"),
    ({"library": {"ecosystem": "npm"}, "dependent": [{"name": "a", "path": "p", "timeout": 0}]}, "timeout"),
    ({"library": {"ecosystem": "npm"}, "dependent": [{"name": "a b", "path": "p"}]}, "invalid name"),
    ({"library": {"ecosystem": "npm"}, "dependent": [{"path": "p"}]}, "needs a name"),
    ({"library": {"ecosystem": "npm"}, "dependent": [{"name": "a", "path": "p", "env": "x"}]}, "env must"),
    ({"library": {"ecosystem": "npm"}, "dependent": [{"name": "a", "path": "p", "venv_args": "x"}]}, "venv_args"),
    ({"library": {"ecosystem": "npm"}, "dependent": "x"}, "array of tables"),
    ({"library": "x"}, "must be a table"),
])
def test_parse_errors(tmp_path, data, msg):
    with pytest.raises(ConfigError, match=msg):
        parse(data, str(tmp_path))


def test_load_missing_and_invalid(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load(str(tmp_path / "revdeps.toml"))
    bad = tmp_path / "revdeps.toml"
    bad.write_text("[library\n")
    with pytest.raises(ConfigError):
        load(str(bad))


def test_load_example_configs():
    root = os.path.join(os.path.dirname(__file__), "..", "examples")
    npm = load(os.path.join(root, "npm", "revdeps.toml"))
    assert npm.library.name == "slugkit" and len(npm.dependents) == 4
    py = load(os.path.join(root, "pypi", "revdeps.toml"))
    assert py.library.ecosystem == "pypi" and py.library.name == "tinyconf"


def test_safe_dirname():
    assert safe_dirname("@scope/pkg") == "scope_pkg"
    assert safe_dirname("///") == "dependent"
