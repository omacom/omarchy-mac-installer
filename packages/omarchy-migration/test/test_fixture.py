"""Exercise the runnable fixture's contract request/result boundaries."""

import json
import os
from pathlib import Path
import select
import signal
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

from omarchy_migration import fixture, probe, restore
from omarchy_migration.dependency import configured_age
from omarchy_migration import contract


COMMAND = [sys.executable, "-m", "omarchy_migration.fixture"]
SAMPLE = Path(__file__).resolve().parent / "fixtures/fixture-request.json"
EXPORTED_FILES = {
    "Projects/demo/changed.txt", "Projects/demo/untracked.txt",
    ".config/example-theme/selected", ".config/unfamiliar-example/settings",
    ".config/hypr/input.lua", ".config/chromium-flags.conf",
    ".config/omarchy/extensions/omarchy-menu.jsonc",
}


def export_request(**selection):
    policy = fixture.fixture_policy()
    files, links = fixture.examples(policy)
    return {"schema": contract.EXPORT_REQUEST, "request_id": str(uuid.uuid4()),
            "inventory_id": fixture.inventory_id(policy, files, links), "policy_revision": policy["revision"],
            "selection": {"categories": ["files-and-projects", "configuration"], "credential_stores": [],
                          "mounts": [], "share_stores": [], **selection}}


class RequestTests(unittest.TestCase):
    def test_malformed_duplicate_and_oversized_requests_fail_before_job_creation(self):
        request = export_request()
        duplicate = json.dumps(request)[:-1] + ', "request_id": "' + str(uuid.uuid4()) + '"}'
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, output = root / "request.json", root / "job"
            for raw, error in ((b"{", "invalid_request"),
                               (duplicate.encode(), "invalid_request"),
                               (json.dumps({**request, "passphrase": "x"}).encode(), "invalid_request"),
                               (json.dumps(json.loads((Path(__file__).resolve().parent / "fixtures/valid/plan.json").read_text())).encode(),
                                "unsupported_request"),
                               (b" " * 8193, "oversized_request")):
                source.write_bytes(raw)
                result = subprocess.run(COMMAND + ["export", "--request", str(source),
                                        "--output-directory", str(output)],
                                        capture_output=True, text=True, timeout=10)
                with self.subTest(error=error):
                    self.assertEqual(result.returncode, 1)
                    event = json.loads(result.stdout)
                    self.assertEqual(contract.validate(event), contract.PROGRESS)
                    self.assertEqual(event["error"], error)
                    self.assertFalse(output.exists())

    def test_capabilities_and_inventory_are_contract_documents_without_crypto(self):
        environment = dict(os.environ)
        environment.pop("OMARCHY_TEST_AGE", None)
        documents = {}
        for operation in ("capabilities", "inventory"):
            result = subprocess.run(COMMAND + [operation], env=environment, capture_output=True,
                                    text=True, timeout=30, check=True)
            documents[operation] = contract.parse(result.stdout.encode())
        capabilities, inventory = documents["capabilities"], documents["inventory"]
        self.assertEqual(capabilities["policy_revisions"], [inventory["policy_revision"]])
        available = {adapter["id"] for adapter in capabilities["adapters"] if adapter["available"]}
        self.assertEqual(available, set(fixture.SUPPORTED_ADAPTERS))
        categories = {item["id"]: item for item in inventory["categories"]}
        self.assertEqual((categories["configuration"]["files"], categories["files-and-projects"]["files"]), (5, 2))
        self.assertEqual((categories["caches"]["files"], categories["caches"]["default_selected"]), (0, False))
        share = inventory["mounts"][0]
        self.assertEqual((share["id"], share["linked"], share["measured"], share["files"]), ("mac-share", True, True, 2))
        self.assertEqual(share["share_stores"], [{"id": "share-ssh", "category": "credentials"}])
        stores = {item["id"]: item for item in inventory["credential_stores"]}
        self.assertEqual((stores["ssh"]["present"], stores["ssh"]["adapter_available"]), (True, True))
        self.assertEqual((stores["chromium"]["present"], stores["chromium"]["adapter_available"]), (False, False))
        self.assertEqual(json.loads(SAMPLE.read_text())["inventory_id"], inventory["inventory_id"])

    def test_requests_bind_to_this_inventory_policy_and_available_stores(self):
        policy = fixture.fixture_policy()
        fixture.check_request(export_request(credential_stores=["ssh"]), policy)
        for request, error in (
                ({**export_request(), "inventory_id": str(uuid.uuid4())}, "inventory_changed"),
                ({**export_request(), "policy_revision": "try-omarchy/82927e9/4"}, "policy_revision_mismatch"),
                (export_request(categories=["photos"]), "unknown_category"),
                (export_request(credential_stores=["keychain"]), "unknown_credential_store"),
                (export_request(credential_stores=["chromium"]), "credential_store_unavailable"),
                (export_request(categories=["files-and-projects"], credential_stores=["ssh"]), "credential_store_outside_selection"),
                (export_request(mounts=["dropbox"]), "unknown_mount"),
                (export_request(mounts=["mac-share"], share_stores=["share-passwords"]), "unknown_share_store"),
                (export_request(categories=["configuration"], mounts=["mac-share"]), "mount_outside_selection")):
            with self.subTest(error=error), self.assertRaises(fixture.FixtureError) as caught:
                contract.validate(request)
                fixture.check_request(request, policy)
            self.assertEqual(str(caught.exception), error)


class ExportFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.age = configured_age()
        if cls.age is None:
            raise unittest.SkipTest("set OMARCHY_TEST_AGE and OMARCHY_TEST_AGE_SHA256 for real age fixtures")

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="migration-fixture-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.output = self.root / "job"
        self.request = export_request()
        self.request_path = self.root / "request.json"
        self.request_path.write_text(json.dumps(self.request))

    def command(self):
        return COMMAND + ["export", "--request", str(self.request_path),
                          "--output-directory", str(self.output)]

    def execute(self):
        result = subprocess.run(self.command(), capture_output=True, text=True, timeout=60)
        events = [json.loads(line) for line in result.stdout.splitlines()]
        for event in events:
            contract.validate(event)
        return result.returncode, events

    def decoded_files(self):
        decoded = probe.decode(self.age, fixture.SECRET, self.output / "bundle.age")
        return {entry["path"] for entry in decoded["entries"] if probe.entry_kind(entry) == "file"}, decoded

    def test_real_command_exports_policy_cleaned_data_and_reuses_complete_job(self):
        code, events = self.execute()
        self.assertEqual(code, 0)
        self.assertEqual([event["phase"] for event in events], ["preparing", "capturing", "finalizing", "complete"])
        self.assertEqual([event["sequence"] for event in events], [1, 2, 3, 4])
        receipt = events[-1]["receipt"]
        self.assertEqual(receipt, json.loads((self.output / "receipt.json").read_text()))
        self.assertEqual(contract.validate(receipt), contract.RECEIPT)
        self.assertEqual((receipt["request_id"], receipt["policy_revision"]),
                         (self.request["request_id"], self.request["policy_revision"]))
        files, decoded = self.decoded_files()
        originals = decoded["provenance"]["originals"]
        self.assertEqual({path for path in files if not path.startswith(originals + "/")}, EXPORTED_FILES)
        self.assertEqual({path[len(originals) + 1:] for path in files if path.startswith(originals + "/")},
                         {".config/hypr/input.lua", ".config/chromium-flags.conf", ".config/omarchy/extensions/omarchy-menu.jsonc"})
        self.assertEqual(decoded["schema"], contract.BUNDLE)
        self.assertEqual(decoded["provenance"]["policy_revision"], receipt["policy_revision"])
        excluded = {item["source"] for item in decoded["provenance"]["collection"]["exceptions"] if item["outcome"] == "excluded"}
        self.assertEqual(excluded, {".config/hypr/monitors.lua"})
        self.assertEqual(receipt["estimates"]["entries"], len(decoded["entries"]))
        self.assertEqual(receipt["bundle"]["bytes"], (self.output / "bundle.age").stat().st_size)
        before = (self.output / "bundle.age").stat()
        code, repeated = self.execute()
        self.assertEqual(code, 0)
        self.assertEqual([event["phase"] for event in repeated], ["complete"])
        self.assertTrue(repeated[-1]["reused"])
        self.assertEqual(repeated[-1]["receipt"], receipt)
        self.assertEqual((self.output / "bundle.age").stat().st_mtime_ns, before.st_mtime_ns)

    def test_restored_export_keeps_personal_content_without_try_integrations(self):
        self.assertEqual(self.execute()[0], 0)
        target, job = self.root / "destination", self.root / "restore-job"
        target.mkdir(mode=0o700)
        job.mkdir(mode=0o700)
        with restore.verified_bundle(self.age, fixture.SECRET, self.output / "bundle.age") as bundle:
            with restore.Restorer(bundle, target, job) as importer:
                importer.apply(importer.plan())
        self.assertEqual((target / ".config/hypr/input.lua").read_bytes(), b"input {\n  kb_layout = us\n}\n")
        originals = target / fixture.collection.ORIGINALS_ROOT / self.request["request_id"]
        self.assertIn(b"try-omarchy/pinch-input.lua", (originals / ".config/hypr/input.lua").read_bytes())
        self.assertEqual(stat.S_IMODE((originals / ".config/hypr/input.lua").stat().st_mode), 0o600)
        self.assertEqual((target / ".config/chromium-flags.conf").read_bytes(), b"--ozone-platform=wayland\n")
        menu = (target / ".config/omarchy/extensions/omarchy-menu.jsonc").read_bytes()
        self.assertEqual(json.loads(menu), {"launch.notes": {"label": "Notes", "action": "obsidian"}})
        self.assertFalse((target / ".config/hypr/monitors.lua").exists())
        self.assertFalse((target / ".ssh").exists())
        self.assertFalse(os.path.lexists(target / "Work"))

    def test_explicit_credential_selection_adds_only_fake_stores(self):
        self.request = export_request(credential_stores=["ssh", "brave"])
        self.request_path.write_text(json.dumps(self.request))
        self.assertEqual(self.execute()[0], 0)
        files, _ = self.decoded_files()
        files = {path for path in files if not path.startswith(fixture.collection.ORIGINALS_ROOT + "/")}
        self.assertEqual(files, EXPORTED_FILES | {".ssh/id_example", ".config/BraveSoftware/Brave-Origin/Default/example"})

    def test_selected_share_arrives_with_its_stores_only_when_ticked(self):
        self.request = export_request(mounts=["mac-share"])
        self.request_path.write_text(json.dumps(self.request))
        code, events = self.execute()
        self.assertEqual(code, 0, events[-1])
        files, _ = self.decoded_files()
        self.assertTrue({"Work/Projects/plan.md", "Work/photo.jpg"} <= files)
        self.assertNotIn("Work/.ssh/id_ed25519", files)
        self.output, self.request = self.root / "job-with-key", export_request(mounts=["mac-share"], share_stores=["share-ssh"])
        self.request_path.write_text(json.dumps(self.request))
        code, events = self.execute()
        self.assertEqual(code, 0, events[-1])
        files, _ = self.decoded_files()
        self.assertIn("Work/.ssh/id_ed25519", files)

    def test_category_selection_limits_the_export(self):
        self.request = export_request(categories=["files-and-projects"])
        self.request_path.write_text(json.dumps(self.request))
        self.assertEqual(self.execute()[0], 0)
        files, _ = self.decoded_files()
        self.assertEqual(files, {"Projects/demo/changed.txt", "Projects/demo/untracked.txt"})

    def test_changed_request_or_ciphertext_cannot_reuse_success(self):
        self.assertEqual(self.execute()[0], 0)
        changed = dict(self.request, selection={**self.request["selection"], "categories": ["files-and-projects"]})
        self.request_path.write_text(json.dumps(changed))
        code, events = self.execute()
        self.assertEqual((code, events[-1]["error"]), (1, "request_conflict"))
        self.request_path.write_text(json.dumps(self.request))
        with (self.output / "bundle.age").open("r+b") as stream:
            stream.seek(-1, 2)
            original = stream.read(1)
            stream.seek(-1, 2)
            stream.write(bytes([original[0] ^ 1]))
        code, events = self.execute()
        self.assertEqual((code, events[-1]["error"]), (1, "ciphertext_changed"))

    def test_tampered_receipt_cannot_be_reused(self):
        self.assertEqual(self.execute()[0], 0)
        receipt_path = self.output / "receipt.json"
        receipt = json.loads(receipt_path.read_text())
        receipt["bundle"]["sha256"] = receipt["bundle"]["sha256"].upper()
        receipt_path.chmod(0o600)
        receipt_path.write_text(json.dumps(receipt))
        code, events = self.execute()
        self.assertEqual((code, events[-1]["error"]), (1, "invalid_receipt"))

    def test_existing_directory_and_symlink_are_not_repurposed(self):
        self.output.mkdir(mode=0o700)
        marker = self.output / "personal-marker"
        marker.write_bytes(b"keep me")
        self.assertEqual(self.execute()[0], 1)
        self.assertEqual(marker.read_bytes(), b"keep me")
        self.assertEqual(set(path.name for path in self.output.iterdir()), {"personal-marker"})
        alias = self.root / "alias"
        alias.symlink_to(self.output, target_is_directory=True)
        self.output = alias
        code, events = self.execute()
        self.assertEqual((code, events[-1]["error"]), (1, "unsafe_job_directory"))

    def test_cancel_at_finalization_keeps_ciphertext_incomplete_without_receipt(self):
        events = []

        def emit(phase, **_fields):
            events.append(phase)

        with self.assertRaises(fixture.Cancelled):
            fixture.export_fixture(self.request, self.output, self.age, emit,
                                   lambda: "finalizing" in events)
        self.assertTrue((self.output / "bundle.age").exists())
        self.assertFalse((self.output / "receipt.json").exists())
        self.assertNotIn("complete", events)
        code, repeated = self.execute()
        self.assertEqual((code, repeated[-1]["error"]), (1, "job_incomplete_use_new_directory"))

    def test_parent_sync_failure_cannot_report_a_durable_completed_job(self):
        events = []
        original = fixture.sync_directory

        def sync(directory):
            if directory == self.output.parent:
                raise OSError("injected parent-directory synchronization failure")
            original(directory)

        with patch.object(fixture, "sync_directory", side_effect=sync), self.assertRaises(OSError):
            fixture.export_fixture(self.request, self.output, self.age,
                                   lambda phase, **fields: events.append(phase), lambda: False)
        self.assertFalse((self.output / "receipt.json").exists())
        self.assertNotIn("complete", events)

    def test_sigterm_cancellation_never_publishes_receipt_and_duplicate_cannot_start_writer(self):
        with subprocess.Popen(self.command() + ["--pause-before-capture", "30"],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as process:
            try:
                self.assertTrue(select.select([process.stdout], [], [], 10)[0])
                self.assertEqual(json.loads(process.stdout.readline())["phase"], "preparing")
                code, duplicate = self.execute()
                self.assertEqual((code, duplicate[-1]["error"]), (1, "job_incomplete_use_new_directory"))
                process.send_signal(signal.SIGTERM)
                stdout, stderr = process.communicate(timeout=10)
                self.assertEqual(process.returncode, 130, stderr)
                self.assertEqual(json.loads(stdout.splitlines()[-1])["phase"], "cancelled")
                self.assertFalse((self.output / "receipt.json").exists())
                self.assertFalse((self.output / "bundle.age").exists())
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
