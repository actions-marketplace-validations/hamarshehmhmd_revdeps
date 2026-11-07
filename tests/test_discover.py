import json

import pytest

from revdeps import discover
from revdeps.config import parse
from revdeps.discover import DiscoverError, clean_repo, dependents, render_config


def fake_fetch(pages):
    calls = []

    def fetch(url):
        calls.append(url)
        for key, val in pages.items():
            if url.endswith(key) or key in url:
                return val
        raise AssertionError(url)
    fetch.calls = calls
    return fetch


PKG = {"repository_url": "https://github.com/me/mylib", "dependent_packages_count": 42}
DEPS = [
    {"name": "big", "downloads": 900, "repository_url": "git+https://github.com/acme/big.git",
     "dependent_packages_count": 5},
    {"name": "norepo", "downloads": 800, "repository_url": None},
    {"name": "big-plugin", "downloads": 700, "repository_url": "https://github.com/acme/big/tree/main/plugin"},
    {"name": "self-test", "downloads": 650, "repository_url": "https://github.com/me/mylib"},
    {"name": "gone", "downloads": 600, "repository_url": "https://github.com/x/gone", "status": "removed"},
    {"name": "gl", "downloads": 500, "repository_url": "https://gitlab.com/team/gl", "dependent_packages_count": 1},
    {"name": "small", "downloads": 10, "repository_url": "https://github.com/acme/small"},
    "junk",
]


def test_dependents_filters_and_dedupes():
    f = fake_fetch({"/dependent_packages?per_page=20&sort=downloads&order=desc": DEPS, "/packages/mylib": PKG})
    found = dependents("mylib", "npm", top=3, fetch=f)
    assert [d["name"] for d in found["results"]] == ["big", "gl", "small"]
    assert found["results"][0]["repo"] == "https://github.com/acme/big"
    assert found["dependent_packages"] == 42 and found["skipped_without_repo"] == 3
    assert "registries/npmjs.org/packages/mylib" in f.calls[0]


def test_scoped_name_is_url_encoded_and_pypi_registry():
    f = fake_fetch({"dependent_packages": [], "/packages/": {}})
    dependents("@scope/pkg", "npm", fetch=f)
    assert "%40scope%2Fpkg" in f.calls[0]
    f2 = fake_fetch({"dependent_packages": [], "/packages/": {}})
    dependents("attrs", "pypi", fetch=f2)
    assert "registries/pypi.org/" in f2.calls[0]


def test_bad_responses():
    with pytest.raises(DiscoverError):
        dependents("x", "cargo")
    with pytest.raises(DiscoverError):
        dependents("x", "npm", fetch=lambda url: [])
    with pytest.raises(DiscoverError):
        dependents("x", "npm", fetch=lambda url: {} if "dependent_packages" not in url else {"a": 1})


@pytest.mark.parametrize("raw,want", [
    ("git+https://github.com/a/b.git", "https://github.com/a/b"),
    ("git://github.com/a/b", "https://github.com/a/b"),
    ("http://github.com/a/b/", "https://github.com/a/b"),
    ("https://github.com/a/b/tree/main/packages/c", "https://github.com/a/b"),
    ("https://codeberg.org/a/b", "https://codeberg.org/a/b"),
    ("https://example.com/a/b", None),
    ("https://github.com/a", None),
    ("", None),
    (None, None),
])
def test_clean_repo(raw, want):
    assert clean_repo(raw) == want


def test_render_config_round_trips(tmp_path):
    found = {"package": "mylib", "ecosystem": "npm", "results": [
        {"name": "@acme/big", "repo": "https://github.com/acme/big", "downloads": 1234, "dependents": 5},
        {"name": "gl", "repo": "https://gitlab.com/team/gl", "downloads": 1, "dependents": 0},
    ]}
    text = render_config(found)
    import sys
    if sys.version_info >= (3, 11):
        import tomllib
    else:
        import tomli as tomllib
    cfg = parse(tomllib.loads(text), str(tmp_path))
    assert cfg.library.name == "mylib"
    assert [d.name for d in cfg.dependents] == ["@acme/big", "gl"]
    assert cfg.dependents[0].repo == "https://github.com/acme/big.git"
    assert "1,234 downloads/month" in text
    py = render_config({"package": "p", "ecosystem": "pypi", "results": []})
    assert 'ecosystem = "pypi"' in py


def test_http_json_maps_errors(monkeypatch):
    import urllib.error

    def boom(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, None)
    monkeypatch.setattr(discover.urllib.request, "urlopen", boom)
    with pytest.raises(DiscoverError, match="not found"):
        discover.http_json("https://x/y")

    def boom500(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 500, "err", {}, None)
    monkeypatch.setattr(discover.urllib.request, "urlopen", boom500)
    with pytest.raises(DiscoverError, match="HTTP 500"):
        discover.http_json("https://x/y")

    def down(req, timeout):
        raise urllib.error.URLError("no route")
    monkeypatch.setattr(discover.urllib.request, "urlopen", down)
    with pytest.raises(DiscoverError, match="could not reach"):
        discover.http_json("https://x/y")

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"ok": 1}).encode()
    monkeypatch.setattr(discover.urllib.request, "urlopen", lambda req, timeout: Resp())
    assert discover.http_json("https://x/y") == {"ok": 1}
