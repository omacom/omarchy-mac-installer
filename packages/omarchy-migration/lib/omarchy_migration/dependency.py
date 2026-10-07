"""Explicit dependency admission for the synthetic age experiment."""

import os
from pathlib import Path
import subprocess

from . import probe


def configured_age():
    selected = os.environ.get("OMARCHY_TEST_AGE")
    if not selected:
        return None
    age = Path(selected)
    expected = os.environ.get("OMARCHY_TEST_AGE_SHA256", "")
    if len(expected) != 64 or any(char not in "0123456789abcdef" for char in expected):
        raise RuntimeError("OMARCHY_TEST_AGE_SHA256 must identify the verified executable")
    if not age.is_absolute() or probe.digest_file(age) != expected:
        raise RuntimeError("age path or executable differs from the verified dependency")
    version = subprocess.run(
        [str(age), "--version"], check=True, capture_output=True, text=True, timeout=5,
    ).stdout.strip()
    if version not in ("1.3.2", "v1.3.2"):
        raise RuntimeError("this disposable probe requires age 1.3.2")
    return age


def admit_age(path=None, sha256=None):
    """An age 1.3 executable for a real export: named, or found on a system PATH.

    The bundle format depends on age's scrypt passphrase stanza, which this
    package checks byte for byte, so only age 1.3.x is accepted. A digest pins
    the exact build when the caller knows it.
    """
    import shutil
    import stat

    selected = path or shutil.which("age", path="/usr/bin:/bin:/usr/local/bin")
    if not selected:
        raise RuntimeError("age is not installed; install it (for example `sudo pacman -S age`)")
    age = Path(selected)
    metadata = age.stat() if age.is_absolute() else None
    if metadata is None or not stat.S_ISREG(metadata.st_mode) or not metadata.st_mode & 0o111:
        raise RuntimeError("age must be an absolute path to an executable file")
    if sha256 is not None and probe.digest_file(age) != sha256:
        raise RuntimeError("age executable differs from the requested digest")
    version = subprocess.run([str(age), "--version"], check=True, capture_output=True, text=True,
                             timeout=5).stdout.strip().removeprefix("v")
    if not version.startswith("1.3."):
        raise RuntimeError(f"age 1.3 is required, found {version or 'an unknown version'}")
    return age
