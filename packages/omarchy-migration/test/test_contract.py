import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import sys

from omarchy_migration import contract, evidence, survey

HERE = Path(__file__).resolve().parent
VALID = HERE / "fixtures/valid"
POLICY = survey.POLICY_PATH


def base(name):
    path = POLICY if name == "policy" else VALID / f"{name}.json"
    return json.loads(path.read_text())


def patched(case):
    document = base(case["base"])
    *parents, last = case["path"]
    target = document
    for key in parents:
        target = target[key]
    if case["op"] == "set":
        target[last] = copy.deepcopy(case["value"])
    else:
        del target[last]
    return document


class ValidFixtureTests(unittest.TestCase):
    def test_every_valid_fixture_parses_through_the_public_interface(self):
        names = sorted(VALID.glob("*.json"))
        self.assertGreaterEqual(len(names), 9)
        for path in names:
            with self.subTest(path.name):
                self.assertEqual(contract.parse(path.read_bytes())["schema"], json.loads(path.read_text())["schema"])

    def test_fixtures_cover_every_public_document(self):
        schemas = {json.loads(path.read_text())["schema"] for path in VALID.glob("*.json")}
        self.assertEqual(schemas, set(contract.VALIDATORS) - {contract.POLICY})

    def test_fixtures_share_one_export_flow(self):
        request, receipt = base("export-request"), base("receipt")
        inventory, plan, report = base("inventory"), base("plan"), base("report")
        self.assertEqual(request["inventory_id"], inventory["inventory_id"])
        self.assertEqual(receipt["request_id"], request["request_id"])
        self.assertEqual(plan["export_id"], receipt["export_id"])
        self.assertEqual(plan["bundle_sha256"], receipt["bundle"]["sha256"])
        self.assertEqual(report["plan_id"], plan["plan_id"])
        self.assertEqual(base("progress-complete")["receipt"], receipt)

    def test_shipped_policy_validates_and_is_the_advertised_revision(self):
        policy = contract.parse(POLICY.read_bytes())
        self.assertEqual(policy["revision"], base("capabilities")["policy_revisions"][0])
        self.assertEqual(policy["revision"], base("inventory")["policy_revision"])


class InvalidFixtureTests(unittest.TestCase):
    def test_each_invalid_case_fails_with_its_stable_code(self):
        cases = json.loads((HERE / "fixtures/invalid.json").read_text())
        self.assertGreaterEqual(len(cases["cases"]), 40)
        for case in cases["cases"]:
            with self.subTest(case["name"]):
                with self.assertRaises(contract.ContractError) as caught:
                    contract.validate(patched(case))
                self.assertEqual(caught.exception.code, case["expect"])
        for case in cases["raw"]:
            with self.subTest(case["name"]):
                with self.assertRaises(contract.ContractError) as caught:
                    contract.parse(case["raw"].encode())
                self.assertEqual(caught.exception.code, case["expect"])

    def test_error_locates_the_nested_field(self):
        document = base("progress-complete")
        document["receipt"]["bundle"]["sha256"] = "x"
        with self.assertRaises(contract.ContractError) as caught:
            contract.validate(document)
        self.assertEqual(caught.exception.where, "$.receipt.bundle.sha256")

    def test_oversized_documents_are_rejected_before_parsing(self):
        with self.assertRaises(contract.ContractError) as caught:
            contract.parse(b" " * (contract.MAX_DOCUMENT + 1))
        self.assertEqual(caught.exception.code, "oversized_document")

    def test_export_requests_have_a_smaller_limit(self):
        data = json.dumps(base("export-request")).encode()
        padded = data[:-1] + b" " * (contract.MAX_REQUEST - len(data) + 1) + b"}"
        with self.assertRaises(contract.ContractError) as caught:
            contract.parse(padded)
        self.assertEqual(caught.exception.code, "oversized_document")
        contract.parse(data)

    def test_parse_takes_bytes_only(self):
        with self.assertRaises(TypeError):
            contract.parse(json.dumps(base("plan")))

    def test_failed_progress_without_identity_needs_an_error_code(self):
        document = base("progress-failed-unparsed")
        del document["error"]
        with self.assertRaises(contract.ContractError) as caught:
            contract.validate(document)
        self.assertEqual(caught.exception.code, "missing_field")


class PolicyTests(unittest.TestCase):
    def test_credential_stores_are_separate_from_every_rule(self):
        policy = base("policy")
        roots = [root for store in policy["credential_stores"] for root in store["roots"]]
        for rule in policy["rules"]:
            for root in roots:
                self.assertFalse(contract._overlaps(rule["path"], root), (rule["path"], root))

    def test_policy_holds_out_every_probe_credential_root(self):
        roots = {root for store in base("policy")["credential_stores"] for root in store["roots"]}
        self.assertTrue({".ssh", ".gnupg", ".config/BraveSoftware", ".config/1Password", ".codex"} <= roots)

    def test_sibling_names_do_not_overlap(self):
        self.assertFalse(contract._overlaps(".config/chromium-flags.conf", ".config/chromium"))
        self.assertTrue(contract._overlaps(".config/chromium/Default", ".config/chromium"))


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="migration-evidence-"))
        self.addCleanup(shutil.rmtree, self.directory)
        self.policy = base("policy")
        self.policy["mounts"] = []
        self.policy["rules"] = self.policy["rules"][:2]
        for rule in self.policy["rules"]:
            # This checkout has no provider history for earlier blocks to cite.
            rule.get("transform", {}).pop("earlier", None)
            path = self.directory / rule["evidence"]["path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(rule["id"])
            rule["evidence"]["sha256"] = hashlib.sha256(rule["id"].encode()).hexdigest()

    def test_unchanged_checkout_has_no_drift(self):
        self.assertEqual(evidence.drift(self.policy, self.directory), [])

    def test_changed_and_missing_evidence_is_reported(self):
        first, second = (rule["evidence"]["path"] for rule in self.policy["rules"])
        (self.directory / first).write_text("upstream changed this fragment")
        (self.directory / second).unlink()
        statuses = {item["path"]: item["status"] for item in evidence.drift(self.policy, self.directory)}
        self.assertEqual(statuses, {first: "changed", second: "missing"})

    def test_an_earlier_block_must_appear_in_its_cited_file_at_its_commit(self):
        git = ["git", "-C", str(self.directory), "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
        subprocess.run([*git, "init", "-q"], check=True)
        writer = self.directory / "guest/writer.sh"
        writer.parent.mkdir(parents=True, exist_ok=True)
        writer.write_text("cat >> input.lua <<'EOF'\n\n-- try block\ndofile(\"/try/x.lua\")\nEOF\n")
        subprocess.run([*git, "add", "-A"], check=True)
        subprocess.run([*git, "commit", "-q", "-m", "old block"], check=True)
        commit = subprocess.run([*git, "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
        writer.write_text("rewritten later\n")
        rule = copy.deepcopy(self.policy["rules"][0])
        rule["transform"] = {"type": "strip-appended-block", "block": "\n-- new\n",
                             "earlier": [{"block": "\n-- try block\ndofile(\"/try/x.lua\")\n",
                                          "commit": commit, "path": "guest/writer.sh"}]}
        self.policy["rules"][0] = rule
        self.assertEqual(evidence.drift(self.policy, self.directory), [])
        for damage in ({"block": "\n-- never written\n"}, {"commit": "f" * 40}, {"path": "guest/other.sh"}):
            broken = copy.deepcopy(self.policy)
            broken["rules"][0]["transform"]["earlier"][0].update(damage)
            with self.subTest(damage=damage):
                self.assertEqual(evidence.drift(broken, self.directory),
                                 [{"path": broken["rules"][0]["transform"]["earlier"][0]["path"],
                                   "status": "earlier-block-not-found"}])

    def test_evidence_cannot_escape_the_checkout_through_a_link(self):
        path = self.directory / self.policy["rules"][0]["evidence"]["path"]
        path.unlink()
        outside = self.directory.parent / f"{self.directory.name}-outside"
        outside.write_text("try-hypr-monitors")
        self.addCleanup(outside.unlink)
        path.symlink_to(outside)
        statuses = {item["status"] for item in evidence.drift(self.policy, self.directory)}
        self.assertEqual(statuses, {"missing"})


class CommandTests(unittest.TestCase):
    def run_main(self, *paths):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = contract.main(["validate", *map(str, paths)])
        return status, [json.loads(line) for line in output.getvalue().splitlines()]

    def test_command_reports_each_document(self):
        with tempfile.TemporaryDirectory() as directory:
            bad = Path(directory) / "bad.json"
            bad.write_text('{"schema": "omarchy-migration/receipt/9"}')
            status, results = self.run_main(VALID / "plan.json", bad, Path(directory) / "missing.json")
        self.assertEqual(status, 1)
        self.assertEqual([result["valid"] for result in results], [True, False, False])
        self.assertEqual(results[1]["error"], "unsupported_schema")
        self.assertEqual(results[2]["error"], "unreadable_document")

    def test_command_succeeds_when_all_documents_are_valid(self):
        status, _ = self.run_main(*sorted(VALID.glob("*.json")), POLICY)
        self.assertEqual(status, 0)

    def test_module_entry_point_runs(self):
        result = subprocess.run(
            [sys.executable, "-m", "omarchy_migration.contract", "validate", str(VALID / "plan.json")],
            capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
