"""The staged package holds the native commands, units, enablement links and sudo rule it needs."""

import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent.parent
AGE_TARBALL_SHA256 = "6b8dc4333c53a5a57c9e5834e3a48f92605d7154014cd07269ff3327db5d37f4"
AGE_SHA256 = "41b072352f4561018949623c674d16ef704019b9108a9bbdbd21292efebfc94f"
SUDO_RULE = '%wheel ALL=(root) NOPASSWD: /usr/lib/omarchy-migration/handoff ""'


def unit(path):
    return [line for line in path.read_text().splitlines() if line and not line.startswith("#")]


class StagedPackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="migration-package-test-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name) / "root"
        subprocess.run(["bash", str(HERE / "install"), str(cls.root)], check=True)

    def mode(self, path):
        return stat.S_IMODE((self.root / path).lstat().st_mode)

    def test_commands_and_the_handoff_are_executable(self):
        for path in ("usr/bin/omarchy-migration", "usr/bin/omarchy-migration-import", "usr/bin/omarchy-migration-offer",
                     "usr/lib/omarchy-migration/handoff"):
            self.assertEqual(self.mode(path), 0o755, path)
        for name in ("claim", "handoff", "importer", "offer", "staging", "review", "restore"):
            self.assertTrue((self.root / f"usr/lib/omarchy-migration/omarchy_migration/{name}.py").is_file(), name)

    def test_units_are_enabled_by_vendor_links(self):
        for path, link in (("usr/lib/systemd/system/omarchy-migration-claim.service",
                            "usr/lib/systemd/system/multi-user.target.wants/omarchy-migration-claim.service"),
                           ("usr/lib/systemd/user/omarchy-migration-offer.service",
                            "usr/lib/systemd/user/graphical-session.target.wants/omarchy-migration-offer.service")):
            self.assertEqual(self.mode(path), 0o644)
            self.assertTrue((self.root / link).is_symlink())
            self.assertEqual(os.readlink(self.root / link), "../" + Path(path).name)
            self.assertEqual((self.root / link).resolve(), (self.root / path).resolve())

    def test_the_claim_runs_once_before_owner_setup_when_a_marker_exists(self):
        lines = unit(self.root / "usr/lib/systemd/system/omarchy-migration-claim.service")
        for line in ("ConditionPathExists=/boot/efi/omarchy/migration.json", "After=local-fs.target boot-efi.mount",
                     "Before=omarchy-mac-first-boot.service omarchy-provision-owner.service display-manager.service",
                     "Type=oneshot", "ExecStart=/usr/bin/omarchy-migration claim", "WantedBy=multi-user.target"):
            self.assertIn(line, lines)
        self.assertFalse(any(line.startswith(("Requires=", "RequiredBy=", "Wants=")) for line in lines))

    def test_the_offer_runs_in_the_graphical_session(self):
        lines = unit(self.root / "usr/lib/systemd/user/omarchy-migration-offer.service")
        for line in ("ExecStart=/usr/bin/omarchy-migration-offer", "WantedBy=graphical-session.target",
                     "PartOf=graphical-session.target"):
            self.assertIn(line, lines)

    def test_the_sudo_rule_allows_only_the_handoff_without_arguments(self):
        path = self.root / "etc/sudoers.d/omarchy-migration"
        self.assertEqual(self.mode(path), 0o440)
        self.assertEqual(self.mode("etc/sudoers.d"), 0o750)
        self.assertEqual(unit(path), [SUDO_RULE])
        visudo = shutil.which("visudo", path="/usr/sbin:/usr/bin:/sbin:/bin")
        if visudo:
            result = subprocess.run([visudo, "-c", "-f", str(path)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_the_staged_handoff_finds_its_library_and_refuses_without_root(self):
        if os.geteuid() == 0 or not Path("/usr/bin/python3").exists():
            self.skipTest("runs unprivileged with /usr/bin/python3")
        result = subprocess.run([str(self.root / "usr/lib/omarchy-migration/handoff")], capture_output=True, text=True,
                                env={"PATH": "/usr/bin:/bin"})
        self.assertEqual((result.returncode, json.loads(result.stderr)), (1, {"error": "not_root"}))

    def test_the_staged_offer_runs_from_the_staged_root(self):
        with tempfile.TemporaryDirectory() as home:
            result = subprocess.run([str(self.root / "usr/bin/omarchy-migration-offer"), "--state", str(Path(home) / "state"),
                                     "--home", home], capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"})
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_packaged_age_is_found_beside_the_library(self):
        age = os.environ.get("OMARCHY_TEST_AGE")
        if not age:
            self.skipTest("set OMARCHY_TEST_AGE")
        base = self.root / "usr/lib/omarchy-migration"
        shutil.copy2(age, base / "age")
        (base / "age.sha256").write_text(os.environ["OMARCHY_TEST_AGE_SHA256"] + "  age\n")
        try:
            result = subprocess.run([sys.executable, "-I", "-c", "import sys; sys.path.insert(0, sys.argv[1]); "
                                     "from omarchy_migration.dependency import packaged_age; print(packaged_age())", str(base)],
                                    capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"})
            self.assertEqual((result.returncode, result.stdout.strip()), (0, str(base / "age")), result.stderr)
        finally:
            (base / "age").unlink()
            (base / "age.sha256").unlink()


class PackageBuildTests(unittest.TestCase):
    def setUp(self):
        self.text = (HERE / "PKGBUILD").read_text()

    def field(self, name):
        match = re.search(rf"^{name}=(.*)$", self.text, re.MULTILINE)
        self.assertIsNotNone(match, name)
        return match.group(1)

    def test_version_matches_the_package_version(self):
        self.assertEqual(self.field("pkgver"), (HERE / "version").read_text().strip())

    def test_age_is_pinned_by_tarball_and_binary_digest(self):
        self.assertEqual(self.field("_age_version"), "1.3.2")
        self.assertEqual(self.field("_age_sha256"), AGE_SHA256)
        self.assertEqual(self.field("sha256sums"), f"('{AGE_TARBALL_SHA256}')")
        self.assertIn("https://github.com/FiloSottile/age/releases/download/v$_age_version/age-v$_age_version-linux-arm64.tar.gz",
                      self.field("source"))
        self.assertEqual(self.text.count("http"), 2)
        self.assertEqual(self.field("depends"), "(python sudo)")
        self.assertEqual(self.field("arch"), "(aarch64)")
        self.assertEqual(self.field("options"), "(!strip !debug)")
        self.assertIn('install -m 0755 "$age" "$pkgdir/usr/lib/omarchy-migration/age"', self.text)
        self.assertIn('bash "$startdir/install" "$pkgdir"', self.text)

    def test_pkgbuild_is_valid_bash(self):
        subprocess.run(["bash", "-n", str(HERE / "PKGBUILD")], check=True)


if __name__ == "__main__":
    unittest.main()
