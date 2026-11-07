"""Minimal JUnit XML reader: which test cases failed?"""
from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from typing import Dict, Optional


def failed_tests(path: str) -> Optional[Dict[str, str]]:
    """Map id -> first line of the failure message for failed/errored cases; None if no readable file."""
    if not path or not os.path.isfile(path):
        return None
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return None
    failed: Dict[str, str] = {}
    for case in root.iter("testcase"):
        bad = [child for child in case if child.tag in ("failure", "error")]
        if bad:
            cls = case.get("classname") or case.get("file") or ""
            if cls == "test":  # node:test's junit reporter uses a constant classname
                cls = ""
            name = case.get("name") or "?"
            raw = bad[0].get("message") or (bad[0].text or "").strip().split("\n")[0]
            msg = " ".join(raw.replace("^", " ").split())
            msg = re.sub(r"(?<=[^\s])([+-] )", r" \1", msg)
            failed[f"{cls}::{name}" if cls else name] = msg[:160]
    return failed
