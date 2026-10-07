"""The real exporter and the trial: policy-driven export of a home, and a read-only round trip."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid

from omarchy_migration import contract, dependency, export, fixture, probe, trial
from omarchy_migration.dependency import configured_age


PASSPHRASE = b"synthetic-transfer-passphrase-heron"
COMMAND = [sys.executable, "-m", "omarchy_migration", "export"]


def policy_document():
    return json.loads(export.POLICY_PATH.read_bytes())


def export_request(**selection):
    return {"schema": contract.EXPORT_REQUEST, "request_id": str(uuid.uuid4()), "inventory_id": str(uuid.uuid4()),
            "policy_revision": policy_document()["revision"],
            "selection": {"categories": ["configuration", "files-and-projects"], "credential_stores": [],
                          "mounts": [], "share_stores": [], **selection}}


def tree_digest(root):
    """Contents, modes and times of everything under `root`, links unfollowed."""
    digest = hashlib.sha256()
    for directory, names, files in sorted(os.walk(root)):
        names.sort()
        for name in sorted(names + files):
            path = Path(directory) / name
            metadata = path.lstat()
            digest.update(f"{path.relative_to(root)}\0{metadata.st_mode}\0{metadata.st_mtime_ns}\0".encode())
            if path.is_symlink():
                digest.update(os.readlink(path).encode())
            elif path.is_file():
                digest.update(path.read_bytes())
    return digest.hexdigest()


class RequestTests(unittest.TestCase):
    def test_requests_outside_the_policy_or_needing_missing_adapters_are_refused(self):
        policy = export.migration_policy.Policy(policy_document())
        cases = (
            ({"policy_revision": "try-omarchy/other/1"}, "policy_revision_mismatch"),
            ({"selection": {**export_request()["selection"], "credential_stores": ["ssh"]}}, "credential_store_unavailable"),
            ({"selection": {**export_request()["selection"], "credential_stores": ["no-such-store"]}},
             "unknown_credential_store"),
            ({"selection": {**export_request()["selection"], "mounts": ["no-such-mount"]}}, "unknown_mount"),
            ({"selection": {**export_request()["selection"], "mounts": ["mac-share"],
                           "share_stores": ["no-such-store"]}}, "unknown_share_store"),
        )
        for change, error in cases:
            request = {**export_request(), **change}
            with self.subTest(error=error), self.assertRaises(export.ExportError) as caught:
                export.check_request(request, policy)
            self.assertEqual(str(caught.exception), error)

    def test_passphrase_is_one_bounded_line(self):
        for data, expected in ((b"correct horse\n", b"correct horse"), (b"no-newline", b"no-newline")):
            read, write = os.pipe()
            os.write(write, data)
            os.close(write)
            with self.subTest(data=data):
                self.assertEqual(export.read_passphrase(read), expected)
            os.close(read)
        for data in (b"", b"\n", b"two\nlines\n", b"nul\0byte", b"x" * (export.MAX_PASSPHRASE + 1)):
            read, write = os.pipe()
            os.write(write, data)
            os.close(write)
            with self.subTest(data=data[:16]), self.assertRaises(export.ExportError):
                export.read_passphrase(read)
            os.close(read)


class AgeAdmissionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="migration-age-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def fake_age(self, version):
        path = self.root / f"age-{version}"
        path.write_text(f"#!/bin/sh\necho {version}\n")
        path.chmod(0o755)
        return path

    def test_only_an_absolute_age_1_3_executable_is_admitted(self):
        self.assertEqual(dependency.admit_age(str(self.fake_age("v1.3.2"))), self.fake_age("v1.3.2"))
        for version in ("v1.2.1", "v1.4.0", "garbage"):
            with self.subTest(version=version), self.assertRaisesRegex(RuntimeError, "age 1.3 is required"):
                dependency.admit_age(str(self.fake_age(version)))
        with self.assertRaisesRegex(RuntimeError, "absolute path"):
            dependency.admit_age("age")
        with self.assertRaisesRegex(RuntimeError, "digest"):
            dependency.admit_age(str(self.fake_age("v1.3.2")), "0" * 64)


@unittest.skipUnless(sys.platform.startswith("linux"), "collection needs Linux /proc")
class ExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.age = configured_age()
        if cls.age is None:
            raise unittest.SkipTest("set OMARCHY_TEST_AGE and OMARCHY_TEST_AGE_SHA256 for real age fixtures")

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="migration-export-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        files, links = fixture.examples(fixture.fixture_policy())
        files[".cache/thumbnails/one.png"] = b"SYNTHETIC cache"
        self.home, self.share, self.scratch = fixture.materialize_all(self.root, files, links)
        self.output = self.root / "export"

    def run_export(self, request, passphrase=PASSPHRASE + b"\n", *extra):
        request_path = self.root / f"request-{uuid.uuid4()}.json"
        request_path.write_text(json.dumps(request))
        read, write = os.pipe()
        os.write(write, passphrase)
        os.close(write)
        try:
            result = subprocess.run(
                [*COMMAND, "--request", str(request_path), "--output", str(self.output), "--passphrase-fd", str(read),
                 "--home", str(self.home), "--age", str(self.age), "--age-sha256", probe.digest_file(self.age),
                 "--scratch", str(self.scratch), *extra],
                pass_fds=(read,), capture_output=True, text=True, timeout=120)
        finally:
            os.close(read)
        events = [json.loads(line) for line in result.stdout.splitlines()]
        for event in events:
            contract.validate(event)
        return result, events

    def decoded(self):
        return probe.decode(self.age, PASSPHRASE, self.output / "bundle.age")

    def test_export_encrypts_with_the_passphrase_and_applies_the_try_policy(self):
        request = export_request()
        result, events = self.run_export(request)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([event["phase"] for event in events], ["preparing", "capturing", "finalizing", "complete"])
        receipt = json.loads((self.output / "receipt.json").read_text())
        self.assertEqual(receipt, events[-1]["receipt"])
        self.assertEqual(receipt["bundle"]["sha256"], probe.digest_file(self.output / "bundle.age"))
        self.assertEqual(json.loads((self.output / "request.json").read_text()), request)
        self.assertEqual(sorted(path.name for path in self.output.iterdir()), ["bundle.age", "receipt.json", "request.json"])
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o700)
        manifest = self.decoded()
        paths = {entry["path"] for entry in manifest["entries"]}
        self.assertIn("Projects/demo/changed.txt", paths)
        self.assertNotIn(".config/hypr/monitors.lua", paths)
        # Credential stores are held out, caches are not selected by default,
        # and the shared-folder link stays a link unless the mount is chosen.
        self.assertFalse(any(path.startswith((".ssh", ".config/BraveSoftware", ".cache")) for path in paths))
        self.assertNotIn("Work/Projects/plan.md", paths)
        self.assertNotIn(PASSPHRASE.decode(), result.stdout + result.stderr)
        with self.assertRaises(probe.Rejected):
            probe.decode(self.age, b"wrong passphrase", self.output / "bundle.age")

    def test_selected_shared_folder_comes_without_its_credential_stores(self):
        result, _ = self.run_export(export_request(mounts=["mac-share"]), PASSPHRASE,
                                    "--share-root", f"mac-share={self.share}")
        self.assertEqual(result.returncode, 0, result.stderr)
        paths = {entry["path"] for entry in self.decoded()["entries"]}
        self.assertTrue({"Work/Projects/plan.md", "Work/photo.jpg"} <= paths)
        self.assertNotIn("Work/.ssh/id_ed25519", paths)

    def test_refusals_report_a_code_and_leave_no_output(self):
        cases = ((export_request(credential_stores=["ssh"]), PASSPHRASE, "credential_store_unavailable"),
                 (export_request(), b"\n", "passphrase_invalid"),
                 (export_request(categories=["caches"]), PASSPHRASE, "nothing_selected"))
        (self.home / ".cache").rename(self.root / "cache")
        for request, passphrase, error in cases:
            with self.subTest(error=error):
                result, events = self.run_export(request, passphrase)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(events[-1]["phase"], "failed")
                self.assertEqual(events[-1]["error"], error)
                self.assertFalse((self.output / "bundle.age").exists())

    def test_an_existing_output_is_never_reused_or_overwritten(self):
        self.output.mkdir()
        (self.output / "bundle.age").write_bytes(b"someone else's")
        result, events = self.run_export(export_request())
        self.assertEqual(result.returncode, 1)
        self.assertEqual(events[-1]["phase"], "failed")
        self.assertEqual((self.output / "bundle.age").read_bytes(), b"someone else's")

    def test_trial_round_trip_leaves_the_home_untouched_and_finds_nothing_unexpected(self):
        before = tree_digest(self.home)
        workdir = self.root / "trial"
        workdir.mkdir(mode=0o700)
        summary = trial.run_trial(self.home, workdir, age=self.age, policy_document=policy_document())
        self.assertEqual(tree_digest(self.home), before)
        outcomes = summary["comparison"]["outcomes"]
        self.assertNotIn("unexpected", outcomes, summary["comparison"]["examples"])
        self.assertNotIn("changed-since-export", outcomes)
        self.assertGreater(outcomes["identical"], 0)
        self.assertEqual(outcomes["transformed"], 3)
        # Three cleaned files' originals, plus the directories that hold them.
        self.assertGreater(outcomes["original-copy"], 3)
        self.assertEqual(outcomes["inert"], 1)  # the Work link into the shared folder
        self.assertEqual(json.loads((workdir / "trial-report.json").read_text())["comparison"], summary["comparison"])
        details = {(item["source"], item["outcome"]) for item in summary["exception_details"]}
        self.assertIn((".config/hypr/monitors.lua", "excluded"), details)
        self.assertIn((".ssh", "held-out"), details)
        described = trial.describe(summary)
        self.assertNotIn("transformed", described.split("Not exported, by policy:")[1].splitlines()[0])
        self.assertIn("UNEXPECTED", trial.describe({**summary, "comparison": {
            "outcomes": {"unexpected": 1}, "examples": {"unexpected": ["x"]}}}))

    def test_trial_reports_files_edited_during_the_trial_as_changed_not_unexpected(self):
        workdir = self.root / "trial"
        workdir.mkdir(mode=0o700)
        edited = self.home / "Projects/demo/changed.txt"

        def edit_while_restoring(phase, **fields):
            if phase == "restoring":
                edited.write_bytes(b"edited after the export\n")

        summary = trial.run_trial(self.home, workdir, age=self.age, policy_document=policy_document(),
                                  emit=edit_while_restoring)
        self.assertEqual(summary["comparison"]["outcomes"]["changed-since-export"], 1)
        self.assertEqual(summary["comparison"]["examples"]["changed-since-export"], ["Projects/demo/changed.txt"])
        self.assertNotIn("unexpected", summary["comparison"]["outcomes"])


if __name__ == "__main__":
    unittest.main()
