#!/usr/bin/env python3
"""The installed system's pacman.conf names every repository Omarchy's packages come from;
[aur], which carries none of them, may be left out."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "builder"))
from verify_installed_system import Verification, check_pacman

REPOSITORIES = ("omarchy", "asahi-alarm", "core", "extra", "alarm", "aur")


class CheckPacmanTest(unittest.TestCase):
    def required_repositories(self, names):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "etc").mkdir()
            (root / "etc/pacman.conf").write_text(
                "[options]\nArchitecture = aarch64\n"
                + "".join(f"\n[{name}]\nInclude = /etc/pacman.d/mirrorlist\n" for name in names))
            result = Verification()
            check_pacman(result, root)
            return result.checks["pacman-required-repositories"]

    def test_every_repository_passes(self):
        self.assertEqual(self.required_repositories(REPOSITORIES)["result"], "passed")

    def test_aur_may_be_left_out(self):
        self.assertEqual(self.required_repositories(REPOSITORIES[:-1])["result"], "passed")

    def test_a_missing_repository_fails(self):
        check = self.required_repositories(tuple(name for name in REPOSITORIES if name != "extra"))
        self.assertEqual(check, {"result": "failed", "detail": "missing repositories: extra"})


if __name__ == "__main__":
    unittest.main()
