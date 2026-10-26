"""Plans and reports describe the exact authenticated bundle and restore job."""

import ast
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

from omarchy_migration import fixture, omarchy_state, probe, restore, review
from omarchy_migration.dependency import configured_age
from omarchy_migration import contract

COMMAND = [sys.executable, "-m", "omarchy_migration.review"]
VALID = Path(__file__).resolve().parent / "fixtures/valid"
STATUSES = {"create", "present", "restored", "replace", "replaced", "conflict", "inert", "directory"}


class FakeBundle:
    def __init__(self, receipt):
        self.export_id = receipt["export_id"]
        self.ciphertext_sha256 = receipt["bundle"]["sha256"]
        self._manifest = {"export_id": self.export_id, "entries": [],
                          "provenance": {"policy_revision": receipt["policy_revision"]}}


class ReasonTableTests(unittest.TestCase):
    def test_every_restorer_reason_has_a_stable_code(self):
        tree = ast.parse((Path(restore.__file__)).read_text())
        reasons = set()

        def strings(node):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                return [node.value]
            if isinstance(node, ast.IfExp):
                return strings(node.body) + strings(node.orelse)
            return []

        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", "")) in ("Action", "_action"):
                values = list(node.args) + [keyword.value for keyword in node.keywords]
                # Reasons must be literal so they can be mapped to codes.
                self.assertFalse(any(isinstance(value, ast.JoinedStr) for value in values), ast.unparse(node))
                literals = [text for value in values for text in strings(value)]
                reasons.update(text for text in literals if text not in STATUSES and text != "")
            if isinstance(node, ast.Tuple):
                values = [text for element in node.elts for text in strings(element)]
                if len(values) >= 2 and values[0] in STATUSES:
                    reasons.update(values[1:])
        reasons = {reason for reason in reasons if reason not in STATUSES}
        self.assertGreaterEqual(len(reasons), 15)
        self.assertEqual(reasons - set(review.REASONS), set())
        self.assertTrue(all(contract.CODE.fullmatch(code) for code in review.REASONS.values()))

    def test_unknown_reason_is_refused(self):
        with self.assertRaises(review.ReviewError):
            review.reason_code(restore.Action("a", "conflict", "a brand new reason"))


class DocumentTests(unittest.TestCase):
    def setUp(self):
        self.receipt = json.loads((VALID / "receipt.json").read_text())
        self.bundle = FakeBundle(self.receipt)
        self.plan = {"plan_id": str(uuid.uuid4()), "export_id": self.bundle.export_id}

    def report(self, *results):
        return review.report_document(self.plan, results, str(uuid.uuid4()), self.bundle)

    def test_unknown_apply_status_is_refused(self):
        with self.assertRaisesRegex(review.ReviewError, "unexpected restore status"):
            self.report(restore.Action("Documents/a", "bogus", "new file"))
        with self.assertRaisesRegex(review.ReviewError, "unexpected restore status"):
            self.report(restore.Action("Documents/a", "create", "new file"))

    def test_directory_only_category_is_restored_not_skipped(self):
        report = self.report(restore.Action(".config", "directory", "structure retained; source directory metadata deferred"))
        self.assertEqual(report["categories"], [{"id": "configuration", "outcome": "restored", "restored": 1,
                                                 "conflicts": 0, "omitted": 0, "reasons": []}])

    def test_empty_bundle_cannot_be_planned(self):
        with self.assertRaisesRegex(review.ReviewError, "empty_bundle"):
            review.plan_document(self.bundle, (), self.receipt, 1000, {"job": [1, 2]})

    def test_metadata_losses_are_reported_on_their_category(self):
        self.bundle._manifest["provenance"]["metadata"] = [{"archive": ".config/app/settings", "lost": ["acl"]}]
        report = self.report(restore.Action(".config/app/settings", "restored", "new entry published"),
                             restore.Action("Documents/a", "restored", "new entry published"))
        reasons = {item["id"]: item["reasons"] for item in report["categories"]}
        self.assertEqual(reasons, {"configuration": ["metadata_not_preserved"], "files-and-projects": []})
        # A lossy file that did not arrive does not make its category claim a loss.
        report = self.report(restore.Action(".config/app/settings", "conflict", "existing file or unsafe path preserved"))
        self.assertEqual(report["categories"][0]["reasons"], ["destination_exists"])
        self.bundle._manifest["provenance"] = None
        self.report(restore.Action("Documents/a", "restored", "new entry published"))

    def test_bundle_without_provenance_cannot_be_planned(self):
        del self.bundle._manifest["provenance"]
        with self.assertRaisesRegex(review.ReviewError, "provenance_missing"):
            review.plan_document(self.bundle, (restore.Action("Documents/a", "create", "new file"),), self.receipt, 1000, {})

    def test_receipt_revision_must_match_the_authenticated_provenance(self):
        receipt = dict(self.receipt, policy_revision="try-omarchy/other/1")
        with self.assertRaisesRegex(review.ReviewError, "receipt_mismatch"):
            review.check_receipt(self.bundle, receipt)

    def test_plan_id_covers_receipt_binding_and_actions(self):
        actions = (restore.Action("Documents/a", "create", "new file"),)
        base = review.plan_id(self.bundle, actions, 1000, {"job": [1, 2]}, self.receipt)
        edited = dict(self.receipt, policy_revision="try-omarchy/other/1")
        for changed in (review.plan_id(self.bundle, actions, 1000, {"job": [1, 3]}, self.receipt),
                        review.plan_id(self.bundle, actions, 1000, {"job": [1, 2]}, edited),
                        review.plan_id(self.bundle, actions, 1001, {"job": [1, 2]}, self.receipt),
                        review.plan_id(self.bundle, (restore.Action("Documents/a", "present", "matching file already exists"),),
                                       1000, {"job": [1, 2]}, self.receipt)):
            self.assertNotEqual(changed, base)
        self.assertEqual(review.plan_id(self.bundle, actions, 1000, {"job": [1, 2]}, self.receipt), base)


class PassphraseTests(unittest.TestCase):
    def read(self, data):
        read, write = os.pipe()
        os.write(write, data)
        os.close(write)
        try:
            return review.read_passphrase(read)
        finally:
            os.close(read)

    def test_maximum_length_with_or_without_newline_is_accepted(self):
        secret = b"x" * review.MAX_PASSPHRASE
        self.assertEqual(self.read(secret + b"\n"), secret)
        self.assertEqual(self.read(secret), secret)

    def test_overlong_empty_and_ambiguous_input_is_refused(self):
        for data, error in ((b"x" * (review.MAX_PASSPHRASE + 1), "passphrase_too_long"),
                            (b"x" * (review.MAX_PASSPHRASE + 1) + b"\n", "passphrase_too_long"),
                            (b"", "passphrase_invalid"), (b"\n", "passphrase_invalid"),
                            (b"secret\r\n", "passphrase_invalid"), (b"two\nlines\n", "passphrase_invalid"),
                            (b"nul\0byte", "passphrase_invalid")):
            with self.subTest(data=data[:12]), self.assertRaisesRegex(review.ReviewError, error):
                self.read(data)


class ReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.age = configured_age()
        if cls.age is None:
            raise unittest.SkipTest("set OMARCHY_TEST_AGE and OMARCHY_TEST_AGE_SHA256 for real age fixtures")

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="migration-review-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        policy = fixture.fixture_policy()
        files, links = fixture.examples(policy)
        self.request = {"schema": contract.EXPORT_REQUEST, "request_id": str(uuid.uuid4()),
                        "inventory_id": fixture.inventory_id(policy, files, links), "policy_revision": policy["revision"],
                        "selection": {"categories": ["files-and-projects", "configuration"], "credential_stores": [],
                                      "mounts": [], "share_stores": []}}
        self.export = self.root / "export"
        self.receipt = fixture.export_fixture(self.request, self.export, self.age, lambda *_a, **_k: None, lambda: False)
        self.target, self.job = self.root / "target", self.root / "job"
        self.target.mkdir(mode=0o700)
        self.job.mkdir(mode=0o700)

    def plan(self):
        with restore.verified_bundle(self.age, fixture.SECRET, self.export / "bundle.age") as bundle:
            with restore.Restorer(bundle, self.target, self.job) as importer:
                return review.plan_document(bundle, importer.plan(), self.receipt, 1000, importer._binding), bundle.ciphertext_sha256

    def command(self, operation, *extra, secret=fixture.SECRET, receipt=None):
        read, write = os.pipe()
        os.write(write, secret + b"\n")
        os.close(write)
        try:
            result = subprocess.run(
                COMMAND + [operation, "--bundle", str(self.export / "bundle.age"), "--receipt", str(receipt or self.export / "receipt.json"),
                           "--target", str(self.target), "--job", str(self.job), "--passphrase-fd", str(read), *extra],
                pass_fds=(read,), capture_output=True, text=True, timeout=60)
        finally:
            os.close(read)
        return result

    def test_plan_binds_the_authenticated_ciphertext_and_counts_every_entry(self):
        plan, digest = self.plan()
        self.assertEqual(contract.validate(plan), contract.PLAN)
        self.assertEqual(digest, probe.digest_file(self.export / "bundle.age"))
        self.assertEqual((plan["bundle_sha256"], plan["export_id"]), (self.receipt["bundle"]["sha256"], self.receipt["export_id"]))
        self.assertEqual(plan["policy_revision"], self.receipt["policy_revision"])
        self.assertEqual(sum(plan["actions"].values()), self.receipt["estimates"]["entries"])
        self.assertEqual(plan["actions"]["conflict"], 0)
        # The Work link into /mnt/mac, and Omarchy's background link by the home's absolute path.
        self.assertEqual(plan["actions"]["inert"], 2)
        # Whole blocks per file and one per new entry: at least the content itself.
        self.assertGreaterEqual(plan["required_bytes"], self.receipt["estimates"]["expanded_bytes"])
        self.assertEqual(plan["required_bytes"] % 4096, 0)

    def test_plan_id_is_stable_until_the_destination_changes(self):
        first, _ = self.plan()
        self.assertEqual(self.plan()[0]["plan_id"], first["plan_id"])
        (self.target / "Projects").mkdir(mode=0o700)
        (self.target / "Projects/demo").mkdir(mode=0o700)
        (self.target / "Projects/demo/changed.txt").write_bytes(b"edited on the destination\n")
        changed, _ = self.plan()
        self.assertNotEqual(changed["plan_id"], first["plan_id"])
        self.assertEqual(changed["actions"]["conflict"], 1)

    def test_plan_for_one_destination_cannot_be_applied_to_another(self):
        plan = contract.parse(self.command("plan").stdout.encode())
        other = self.root / "other-target"
        other.mkdir(mode=0o700)
        self.target = other
        self.job = self.root / "other-job"
        self.job.mkdir(mode=0o700)
        refused = self.command("apply", "--plan-id", plan["plan_id"])
        self.assertEqual((refused.returncode, json.loads(refused.stderr)["error"]), (1, "plan_changed"))
        self.assertEqual(list(other.iterdir()), [])

    def test_receipt_with_another_policy_revision_is_refused(self):
        plan = contract.parse(self.command("plan").stdout.encode())
        self.assertEqual(plan["policy_revision"], self.receipt["policy_revision"])
        edited = self.root / "edited-receipt.json"
        edited.write_text(json.dumps(dict(self.receipt, policy_revision="try-omarchy/82927e9/9")))
        for operation, extra in (("plan", ()), ("apply", ("--plan-id", plan["plan_id"]))):
            refused = self.command(operation, *extra, receipt=edited)
            with self.subTest(operation=operation):
                self.assertEqual((refused.returncode, json.loads(refused.stderr)["error"]), (1, "receipt_mismatch"))
        self.assertEqual(list(self.target.iterdir()), [])

    def test_plan_id_option_is_only_for_apply(self):
        self.assertEqual(self.command("plan", "--plan-id", str(uuid.uuid4())).returncode, 2)
        self.assertEqual(self.command("apply").returncode, 2)

    def test_receipt_for_another_bundle_is_refused(self):
        with restore.verified_bundle(self.age, fixture.SECRET, self.export / "bundle.age") as bundle:
            with restore.Restorer(bundle, self.target, self.job) as importer:
                actions = importer.plan()
                for field, value in ((("export_id",), str(uuid.uuid4())), (("bundle", "sha256"), "0" * 64)):
                    receipt = json.loads(json.dumps(self.receipt))
                    target = receipt
                    for key in field[:-1]:
                        target = target[key]
                    target[field[-1]] = value
                    with self.subTest(field=field), self.assertRaises(review.ReviewError) as caught:
                        review.plan_document(bundle, actions, receipt, 1000, importer._binding)
                    self.assertEqual(str(caught.exception), "receipt_mismatch")

    def test_command_applies_only_the_reviewed_plan_and_reports_by_category(self):
        planned = self.command("plan")
        self.assertEqual(planned.returncode, 0, planned.stderr)
        plan = contract.parse(planned.stdout.encode())
        refused = self.command("apply", "--plan-id", str(uuid.uuid4()))
        self.assertEqual((refused.returncode, json.loads(refused.stderr)["error"]), (1, "plan_changed"))
        self.assertEqual(list(self.target.iterdir()), [])
        applied = self.command("apply", "--plan-id", plan["plan_id"])
        self.assertEqual(applied.returncode, 0, applied.stderr)
        report = contract.parse(applied.stdout.encode())
        self.assertEqual(report["plan_id"], plan["plan_id"])
        outcomes = {item["id"]: (item["outcome"], item["restored"], item["conflicts"]) for item in report["categories"]}
        decoded = probe.decode(self.age, fixture.SECRET, self.export / "bundle.age")
        # Files and the directories that hold them both count as restored.
        # Omarchy regenerates its current theme state, so the import leaves it out.
        generated = [entry for entry in decoded["entries"] if entry["path"].startswith(omarchy_state.GENERATED[0])]
        configuration = [entry for entry in decoded["entries"]
                         if entry["path"].startswith(".") and entry not in generated]
        self.assertEqual(outcomes["configuration"], ("restored", len(configuration), 0))  # includes original copies
        settings = next(item for item in report["categories"] if item["id"] == "configuration")
        self.assertEqual((settings["omitted"], settings["reasons"]), (len(generated), ["regenerated_by_omarchy"]))
        user_files = [entry for entry in configuration
                      if probe.entry_kind(entry) == "file" and not entry["path"].startswith(fixture.collection.ORIGINALS_ROOT + "/")]
        self.assertEqual(len(user_files), 4)
        self.assertEqual(outcomes["files-and-projects"][0], "restored")
        work = next(item for item in report["categories"] if item["id"] == "files-and-projects")
        self.assertEqual((work["omitted"], work["reasons"]), (1, ["link_not_restorable"]))
        self.assertEqual(json.loads((self.target / ".config/omarchy/shell.json").read_bytes()), {"version": 1})

    def test_conflicts_make_a_category_partial_and_retry_reports_the_same_job(self):
        (self.target / ".config/unfamiliar-example").mkdir(mode=0o700, parents=True)
        (self.target / ".config/unfamiliar-example/settings").write_bytes(b"native-choice\n")
        plan = contract.parse(self.command("plan").stdout.encode())
        self.assertEqual(plan["actions"]["conflict"], 1)
        report = contract.parse(self.command("apply", "--plan-id", plan["plan_id"]).stdout.encode())
        configuration = next(item for item in report["categories"] if item["id"] == "configuration")
        self.assertEqual((configuration["outcome"], configuration["conflicts"], configuration["reasons"]),
                         ("partial", 1, ["destination_exists", "regenerated_by_omarchy"]))
        self.assertEqual((self.target / ".config/unfamiliar-example/settings").read_bytes(), b"native-choice\n")
        again = contract.parse(self.command("plan").stdout.encode())
        retried = contract.parse(self.command("apply", "--plan-id", again["plan_id"]).stdout.encode())
        self.assertEqual(retried["job_id"], report["job_id"])
        self.assertEqual(again["actions"]["create"], 0)

    def test_apply_refuses_without_enough_free_space_and_writes_nothing(self):
        plan = contract.parse(self.command("plan").stdout.encode())
        read, write = os.pipe()
        os.write(write, fixture.SECRET + b"\n")
        os.close(write)
        arguments = ["apply", "--plan-id", plan["plan_id"], "--bundle", str(self.export / "bundle.age"),
                     "--receipt", str(self.export / "receipt.json"), "--target", str(self.target),
                     "--job", str(self.job), "--passphrase-fd", str(read)]
        errors = io.StringIO()
        try:
            with patch.object(review, "available_bytes", return_value=plan["required_bytes"]), \
                    contextlib.redirect_stderr(errors):
                self.assertEqual(review.main(arguments), 1)
        finally:
            os.close(read)
        self.assertEqual(json.loads(errors.getvalue())["error"], "insufficient_space")
        self.assertEqual(list(self.target.iterdir()), [])
        self.assertEqual(review.space_shortfall({"required_bytes": 10}, self.target), 0)

    def test_wrong_passphrase_and_bad_passphrase_input_write_nothing_or_leak(self):
        wrong = b"not-the-transfer-passphrase"
        for secret, check in ((wrong, lambda error: error not in ("", "passphrase_invalid")),
                              (b"", lambda error: error == "passphrase_invalid")):
            result = self.command("plan", secret=secret)
            with self.subTest(secret=secret):
                self.assertEqual(result.returncode, 1)
                error = json.loads(result.stderr)["error"]
                self.assertTrue(check(error), error)
                for text in (result.stdout, result.stderr):
                    self.assertNotIn(wrong.decode(), text)
                    self.assertNotIn(fixture.SECRET.decode(), text)
                    self.assertNotIn(str(self.target), text)
        self.assertEqual(list(self.target.iterdir()), [])
        self.assertEqual(list(self.job.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
