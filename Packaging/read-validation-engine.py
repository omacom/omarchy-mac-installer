#!/usr/bin/env python3
"""Print the inspection engine pin shared by app packaging and release CI.

The engine is pinned only in Engine/source-lock.json, and OmarchyEnginePinPlugin
compiles the same values into the app (docs/engine-versions.md). The checks are
the generator's.
"""
import json
from pathlib import Path
import re

root = Path(__file__).resolve().parents[1]
lock = json.loads((root / "Engine/source-lock.json").read_text())
version = lock["downstream_overlay"]["version"]
artifact = lock["validation_artifact"]
name, digest, size = artifact["filename"], artifact["sha256"], artifact["size_bytes"]
if (not isinstance(version, str) or re.fullmatch(r"v[0-9A-Za-z.+-]*", version) is None
        or name != f"installer-{version}.tar.gz"
        or not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        or type(size) is not int or size <= 0):
    raise SystemExit("Cannot read the inspection engine pin from the source lock")
print(name, digest, size)
