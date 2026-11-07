"""Load and validate revdeps.toml."""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised on 3.9/3.10 CI
    import tomli as tomllib

ECOSYSTEMS = ("npm", "pypi")
DEFAULT_CONFIG = "revdeps.toml"


class ConfigError(Exception):
    pass


@dataclass
class Library:
    ecosystem: str
    path: str
    name: Optional[str] = None
    build: Optional[str] = None


@dataclass
class Dependent:
    name: str
    repo: Optional[str] = None
    ref: Optional[str] = None
    path: Optional[str] = None
    subdir: str = ""
    install: Optional[str] = None
    inject: Optional[str] = None
    test: Optional[str] = None
    env: Dict[str, str] = field(default_factory=dict)
    timeout: int = 900
    skip: bool = False
    venv: bool = True
    venv_args: List[str] = field(default_factory=list)
    python: Optional[str] = None


@dataclass
class Config:
    library: Library
    dependents: List[Dependent]
    base_dir: str


_NAME_OK = re.compile(r"^[A-Za-z0-9@._/-]+$")
_DEP_KEYS = {
    "name", "repo", "ref", "path", "subdir", "install", "inject", "test",
    "env", "timeout", "skip", "venv", "venv_args", "python",
}


def detect_package(path: str):
    """Return (ecosystem, name) for a library checkout, or (None, None)."""
    pj = os.path.join(path, "package.json")
    if os.path.isfile(pj):
        try:
            with open(pj, encoding="utf-8") as fh:
                return "npm", json.load(fh).get("name")
        except (OSError, ValueError):
            return "npm", None
    pp = os.path.join(path, "pyproject.toml")
    if os.path.isfile(pp):
        try:
            with open(pp, "rb") as fh:
                data = tomllib.load(fh)
            return "pypi", (data.get("project") or {}).get("name")
        except (OSError, tomllib.TOMLDecodeError):
            return "pypi", None
    if os.path.isfile(os.path.join(path, "setup.py")) or os.path.isfile(os.path.join(path, "setup.cfg")):
        return "pypi", None
    return None, None


def safe_dirname(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "dependent"


def parse(data: dict, base_dir: str) -> Config:
    lib = data.get("library") or {}
    if not isinstance(lib, dict):
        raise ConfigError("[library] must be a table")
    lib_path = os.path.normpath(os.path.join(base_dir, lib.get("path", ".")))
    eco, detected_name = detect_package(lib_path)
    ecosystem = lib.get("ecosystem") or eco
    if ecosystem not in ECOSYSTEMS:
        raise ConfigError(
            f"library ecosystem must be one of {', '.join(ECOSYSTEMS)} "
            f"(set [library] ecosystem, or add package.json / pyproject.toml at {lib_path})"
        )
    library = Library(ecosystem=ecosystem, path=lib_path, name=lib.get("name") or detected_name,
                      build=lib.get("build"))

    defaults = data.get("defaults") or {}
    raw = data.get("dependent") or data.get("dependents") or []
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        raise ConfigError("[[dependent]] entries must be an array of tables")
    deps: List[Dependent] = []
    seen = set()
    for i, entry in enumerate(raw, 1):
        if not isinstance(entry, dict):
            raise ConfigError(f"dependent #{i} must be a table")
        merged = dict(defaults)
        merged.update(entry)
        unknown = set(merged) - _DEP_KEYS
        if unknown:
            raise ConfigError(f"dependent #{i}: unknown key(s): {', '.join(sorted(unknown))}")
        name = merged.get("name")
        if not name and merged.get("repo"):
            name = re.sub(r"\.git$", "", merged["repo"].rstrip("/").split("/")[-1])
        if not name:
            raise ConfigError(f"dependent #{i}: needs a name or repo")
        if not _NAME_OK.match(name):
            raise ConfigError(f"dependent #{i}: invalid name {name!r}")
        if name in seen:
            raise ConfigError(f"duplicate dependent name {name!r}")
        seen.add(name)
        if bool(merged.get("repo")) == bool(merged.get("path")):
            raise ConfigError(f"dependent {name!r}: set exactly one of repo or path")
        path = merged.get("path")
        if path:
            path = os.path.normpath(os.path.join(base_dir, path))
        env = merged.get("env") or {}
        if not isinstance(env, dict):
            raise ConfigError(f"dependent {name!r}: env must be a table")
        timeout = merged.get("timeout", 900)
        if not isinstance(timeout, int) or timeout <= 0:
            raise ConfigError(f"dependent {name!r}: timeout must be a positive integer (seconds)")
        venv_args = merged.get("venv_args") or []
        if not isinstance(venv_args, list):
            raise ConfigError(f"dependent {name!r}: venv_args must be a list")
        deps.append(Dependent(
            name=name, repo=merged.get("repo"), ref=merged.get("ref") or None, path=path,
            subdir=merged.get("subdir", "") or "", install=merged.get("install"),
            inject=merged.get("inject"), test=merged.get("test"),
            env={str(k): str(v) for k, v in env.items()}, timeout=timeout,
            skip=bool(merged.get("skip", False)), venv=bool(merged.get("venv", True)),
            venv_args=[str(a) for a in venv_args], python=merged.get("python"),
        ))
    return Config(library=library, dependents=deps, base_dir=base_dir)


def load(path: str) -> Config:
    if not os.path.isfile(path):
        raise ConfigError(f"{path} not found (create one with: revdeps discover --write)")
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    return parse(data, os.path.dirname(os.path.abspath(path)))
