"""--defaults replaces only untouched shipped defaults, with backups, and only as planned."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid

from omarchy_migration import contract, fixture, probe, restore, review
from omarchy_migration.dependency import configured_age, packaged_age

COMMAND = [sys.executable, "-m", "omarchy_migration.review"]
UNTOUCHED = ".config/example-theme/selected"
CHANGED = ".config/unfamiliar-example/settings"
PERSONAL = "Projects/demo/changed.txt"
DEFAULT = b"tokyo-night\n"
THEME_NAME = ".local/state/omarchy/current/theme.name"
THEME_FILE = ".local/state/omarchy/current/theme/colors.toml"


class DefaultsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.age = configured_age()
        if cls.age is None or os.geteuid() == 0:
            raise unittest.SkipTest("set OMARCHY_TEST_AGE and OMARCHY_TEST_AGE_SHA256, and run unprivileged")

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="migration-defaults-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        policy = fixture.fixture_policy()
        files, links = fixture.examples(policy)
        self.files = files
        request = {"schema": contract.EXPORT_REQUEST, "request_id": str(uuid.uuid4()),
                   "inventory_id": fixture.inventory_id(policy, files, links), "policy_revision": policy["revision"],
                   "selection": {"categories": ["files-and-projects", "configuration"], "credential_stores": [],
                                 "mounts": [], "share_stores": []}}
        self.export = self.root / "export"
        fixture.export_fixture(request, self.export, self.age, lambda *_a, **_k: None, lambda: False)
        self.skel = self.root / "skel"
        self.write(self.skel, UNTOUCHED, DEFAULT)
        self.write(self.skel, CHANGED, b"shipped default\n")
        self.target, self.job = self.root / "home", self.root / "job"
        self.target.mkdir(mode=0o700)
        self.job.mkdir(mode=0o700)
        self.write(self.target, UNTOUCHED, DEFAULT)
        self.write(self.target, CHANGED, b"changed on this Mac\n")
        self.write(self.target, PERSONAL, b"native edit\n")
        # The native system's own theme state, which Omarchy wrote at its first login.
        self.write(self.target, THEME_NAME, b"tokyo-night\n")
        self.write(self.target, THEME_FILE, b'accent = "#native"\n')

    def write(self, root, path, data):
        (root / path).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        (root / path).write_bytes(data)
        (root / path).chmod(0o600)

    def command(self, operation, *extra, defaults=True, summary=None):
        read, write = os.pipe()
        os.write(write, fixture.SECRET + b"\n")
        os.close(write)
        arguments = [operation, "--bundle", str(self.export / "bundle.age"), "--receipt", str(self.export / "receipt.json"),
                     "--target", str(self.target), "--job", str(self.job), "--passphrase-fd", str(read), *extra]
        if defaults:
            arguments += ["--defaults", str(self.skel)]
        if summary:
            arguments += ["--summary", str(summary)]
        try:
            return subprocess.run(COMMAND + arguments, pass_fds=(read,), capture_output=True, text=True, timeout=120)
        finally:
            os.close(read)

    def plan(self, **options):
        result = self.command("plan", **options)
        self.assertEqual(result.returncode, 0, result.stderr)
        return contract.parse(result.stdout.encode())

    def test_plan_replaces_only_byte_identical_defaults_and_lists_them_apart(self):
        summary_path = self.root / "summary.json"
        plan = self.plan(summary=summary_path)
        self.assertEqual(plan["actions"]["replace"], 1)
        self.assertEqual(plan["actions"]["conflict"], 2)
        summary = json.loads(summary_path.read_text())
        self.assertEqual(summary["schema"], review.SUMMARY_SCHEMA)
        self.assertEqual(summary["plan_id"], plan["plan_id"])
        self.assertEqual(summary["replaces_default"], [UNTOUCHED])
        self.assertEqual(summary["kept"], [{"path": CHANGED, "reason": "native_file_changed"},
                                           {"path": PERSONAL, "reason": "destination_exists"}])
        self.assertEqual(oct(summary_path.stat().st_mode & 0o777), oct(0o600))
        self.assertGreater(sum(group["create"] for group in summary["categories"]), 0)
        self.assertGreater(sum(summary["left_out"].values()), 0)
        without = self.plan(defaults=False)
        self.assertEqual((without["actions"]["replace"], without["actions"]["conflict"]), (0, 3))
        self.assertNotEqual(without["plan_id"], plan["plan_id"])
        self.assertEqual(self.command("plan", summary=summary_path).returncode, 1)

    def test_omarchy_theme_state_is_left_to_omarchy_and_its_choice_is_reported(self):
        summary_path = self.root / "summary.json"
        plan = self.plan(summary=summary_path)
        summary = json.loads(summary_path.read_text())
        self.assertEqual(summary["omarchy"], {
            "theme": "solitude", "background": ".local/state/omarchy/current/theme/backgrounds/1-on-pole.webp"})
        self.assertFalse(any(item["path"].startswith(".local/state/omarchy/current") for item in summary["kept"]))
        applied = self.command("apply", "--plan-id", plan["plan_id"])
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertEqual((self.target / THEME_NAME).read_bytes(), b"tokyo-night\n")
        self.assertEqual((self.target / THEME_FILE).read_bytes(), b'accent = "#native"\n')
        self.assertFalse((self.target / ".local/state/omarchy/current/background").exists())
        configuration = next(item for item in contract.parse(applied.stdout.encode())["categories"]
                             if item["id"] == "configuration")
        self.assertIn("regenerated_by_omarchy", configuration["reasons"])

    def test_space_counts_the_backup_of_a_large_default(self):
        large = b"x" * (4 * 1024 * 1024)
        self.write(self.skel, UNTOUCHED, large)
        self.write(self.target, UNTOUCHED, large)
        plan = self.plan()
        self.assertEqual(plan["actions"]["replace"], 1)
        self.assertGreaterEqual(plan["required_bytes"], len(large))

    def test_plan_id_binds_the_defaults(self):
        first = self.plan()["plan_id"]
        self.assertEqual(self.plan()["plan_id"], first)
        self.write(self.skel, ".config/omarchy/shell.json", b"{}\n")
        self.assertNotEqual(self.plan()["plan_id"], first)

    def test_apply_replaces_with_a_private_backup_and_keeps_changed_files(self):
        plan = self.plan()
        applied = self.command("apply", "--plan-id", plan["plan_id"])
        self.assertEqual(applied.returncode, 0, applied.stderr)
        report = contract.parse(applied.stdout.encode())
        self.assertEqual((self.target / UNTOUCHED).read_bytes(), self.files[UNTOUCHED])
        self.assertEqual((self.target / CHANGED).read_bytes(), b"changed on this Mac\n")
        self.assertEqual((self.target / PERSONAL).read_bytes(), b"native edit\n")
        backups = [path for path in self.job.iterdir() if path.name != "journal.json" and not path.name.endswith(".displaced")]
        self.assertEqual([path.read_bytes() for path in backups], [DEFAULT])
        self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)
        configuration = next(item for item in report["categories"] if item["id"] == "configuration")
        self.assertIn("native_file_changed", configuration["reasons"])
        again = self.plan()
        self.assertEqual((again["actions"]["replace"], again["actions"]["create"]), (0, 0))
        retried = self.command("apply", "--plan-id", again["plan_id"])
        self.assertEqual(retried.returncode, 0, retried.stderr)
        self.assertEqual((self.target / UNTOUCHED).read_bytes(), self.files[UNTOUCHED])

    def test_apply_refuses_when_a_default_changed_since_the_plan(self):
        plan = self.plan()
        self.write(self.target, UNTOUCHED, b"edited after the review\n")
        refused = self.command("apply", "--plan-id", plan["plan_id"])
        self.assertEqual((refused.returncode, json.loads(refused.stderr)["error"]), (1, "plan_changed"))
        self.assertEqual((self.target / UNTOUCHED).read_bytes(), b"edited after the review\n")
        self.assertEqual(list(self.job.iterdir()), [])

    def test_a_restorer_refuses_a_changed_default_between_plan_and_apply(self):
        with restore.verified_bundle(self.age, fixture.SECRET, self.export / "bundle.age") as bundle:
            defaults = review.default_digests(bundle, self.skel)
            self.assertEqual(set(defaults), {UNTOUCHED, CHANGED})
            with restore.Restorer(bundle, self.target, self.job, replace_matching=defaults) as importer:
                actions = importer.plan()
                self.write(self.target, UNTOUCHED, b"edited during the review\n")
                results = {action.path: action for action in importer.apply(actions)}
        self.assertEqual((results[UNTOUCHED].status, review.reason_code(results[UNTOUCHED])), ("conflict", "destination_changed"))
        self.assertEqual((self.target / UNTOUCHED).read_bytes(), b"edited during the review\n")

    def test_links_in_the_defaults_folder_are_not_followed(self):
        (self.skel / ".config/example-theme/selected").unlink()
        (self.skel / ".config/example-theme/selected").symlink_to(self.target / UNTOUCHED)
        (self.skel / ".config/unfamiliar-example").rename(self.root / "elsewhere")
        (self.skel / ".config/unfamiliar-example").symlink_to(self.root / "elsewhere")
        plan = self.plan()
        self.assertEqual((plan["actions"]["replace"], plan["actions"]["conflict"]), (0, 3))

    def test_conditional_approval_is_strict(self):
        with restore.verified_bundle(self.age, fixture.SECRET, self.export / "bundle.age") as bundle:
            for value in ({UNTOUCHED: "A" * 64}, {UNTOUCHED: "0" * 63}, {"missing": "0" * 64},
                          {".config": "0" * 64}, [UNTOUCHED]):
                with self.subTest(value=value), self.assertRaises(probe.Rejected):
                    restore.Restorer(bundle, self.target, self.job, replace_matching=value)
            with self.assertRaises(probe.Rejected):
                restore.Restorer(bundle, self.target, self.job, replace=(UNTOUCHED,), replace_matching={UNTOUCHED: "0" * 64})


class PassphraseCodeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.age = configured_age()
        if cls.age is None:
            raise unittest.SkipTest("set OMARCHY_TEST_AGE and OMARCHY_TEST_AGE_SHA256")

    def test_a_wrong_passphrase_has_its_own_code(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "file"
            source.write_bytes(b"synthetic")
            manifest = probe.make_manifest({"file": source})
            bundle = Path(directory) / "bundle.age"
            probe.encrypt(self.age, b"right-synthetic", bundle, lambda stream: probe.write_archive(stream, manifest, {"file": source}))
            with self.assertRaisesRegex(probe.Rejected, "^passphrase_incorrect$"):
                probe.decode(self.age, b"wrong-synthetic", bundle)
            bundle.write_bytes(bundle.read_bytes()[:-20])
            with self.assertRaises(probe.Rejected) as caught:
                probe.decode(self.age, b"right-synthetic", bundle)
            self.assertNotEqual(str(caught.exception), "passphrase_incorrect")


class PackagedAgeTests(unittest.TestCase):
    def test_the_packaged_age_needs_its_recorded_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            age = Path(directory) / "age"
            self.assertIsNone(packaged_age(age))
            age.write_bytes(b"#!/bin/sh\necho v1.3.2\n")
            age.chmod(0o755)
            (Path(directory) / "age.sha256").write_text("not a digest\n")
            with self.assertRaises(RuntimeError):
                packaged_age(age)
            (Path(directory) / "age.sha256").write_text("0" * 64 + "  age\n")
            with self.assertRaises(RuntimeError):
                packaged_age(age)
            (Path(directory) / "age.sha256").write_text(probe.digest_file(age) + "  age\n")
            self.assertEqual(packaged_age(age), age)


if __name__ == "__main__":
    unittest.main()
