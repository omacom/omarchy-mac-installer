#!/usr/bin/env python3
"""package_record.py traces installed packages to the pinned databases and the set."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
RECORD = ROOT / "builder/package_record.py"


class PackageRecordTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.repos = self.base / "repos/omarchy-candidates"
        self.repos.mkdir(parents=True)
        self.entries = self.base / "entries"
        self.root = self.base / "root"
        self.packages = []
        for name, version, group in (("aura", "1.0.0b13-1", "boot"), ("uboot-aurora", "2026.07-1", "boot"),
                                     ("m1n1-aurora", "1.6.1.aurora23-3", "stage1")):
            filename = f"{name}-{version}-aarch64.pkg.tar.zst"
            (self.repos / filename).write_bytes(name.encode())
            digest = hashlib.sha256(name.encode()).hexdigest()
            entry = self.entries / f"{name}-{version}"
            entry.mkdir(parents=True)
            (entry / "desc").write_text(f"%FILENAME%\n{filename}\n\n%NAME%\n{name}\n\n%VERSION%\n{version}\n\n"
                                        f"%SHA256SUM%\n{digest}\n")
            self.packages.append({"name": name, "version": version, "filename": filename, "sha256": digest,
                                  "group": group})
        subprocess.run(["bsdtar", "-czf", str(self.repos / "omarchy-candidates.db"), "-C", str(self.entries),
                        *sorted(p.name for p in self.entries.iterdir())], check=True)
        (self.base / "import.json").write_text(json.dumps({"packages": self.packages}))
        (self.base / "cache").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def install(self, *names):
        for package in self.packages:
            if package["name"] in names:
                local = self.root / "var/lib/pacman/local" / f"{package['name']}-{package['version']}"
                local.mkdir(parents=True)
                (local / "desc").write_text(f"%NAME%\n{package['name']}\n\n%VERSION%\n{package['version']}\n")

    def record(self):
        return subprocess.run([sys.executable, str(RECORD), "--root", str(self.root), "--repositories",
                               str(self.base / "repos"), "--order", "omarchy-candidates", "--cache",
                               str(self.base / "cache"), "--candidates", str(self.base / "import.json")],
                              capture_output=True, text=True)

    def test_an_edge_image_installs_aura_and_not_its_stage1_source(self):
        self.install("aura", "uboot-aurora")
        result = self.record()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([line.split("|")[0] for line in result.stdout.splitlines()], ["aura", "uboot-aurora"])

    def test_the_stage1_source_must_not_be_installed(self):
        self.install("aura", "uboot-aurora", "m1n1-aurora")
        result = self.record()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("m1n1-aurora is installed, but the set carries it only for its raw Stage 1", result.stderr)

    def test_an_installable_candidate_must_still_be_installed(self):
        self.install("aura")
        result = self.record()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("candidate packages are not installed: uboot-aurora", result.stderr)


if __name__ == "__main__":
    unittest.main()
