import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLES = os.path.join(ROOT, "examples")
PY = sys.executable


def node_ok():
    if not shutil.which("node") or not shutil.which("npm"):
        return False
    out = subprocess.run(["node", "-p", "process.versions.node.split('.')[0]"], capture_output=True, text=True)
    try:
        return int(out.stdout.strip()) >= 20
    except ValueError:
        return False


needs_node = pytest.mark.skipif(not node_ok(), reason="needs node >= 20 and npm")
needs_network = pytest.mark.skipif(bool(os.environ.get("REVDEPS_OFFLINE")), reason="REVDEPS_OFFLINE set")


def pycmd(code: str) -> str:
    """A shell command that runs a Python snippet with the current interpreter (portable)."""
    return subprocess.list2cmdline([PY, "-c", code]) if os.name == "nt" else \
        f"'{PY}' -c " + "'" + code.replace("'", "'\"'\"'") + "'"
