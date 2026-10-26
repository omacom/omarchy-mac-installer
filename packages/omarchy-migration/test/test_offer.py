"""The offer notifies only while an export waits for this user, and tells a failed claim once."""

import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from omarchy_migration import claim, offer

EXPORT_ID = "1b4e28ba-2fa1-41d2-883f-0016d3cca427"
OTHER_ID = "9f0c8a2e-3b1d-4c5e-8f7a-6b5c4d3e2f10"


class DecisionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="migration-offer-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state = self.root / "state"
        (self.state / "pending").mkdir(parents=True)
        self.home = self.root / "home"
        self.home.mkdir()
        self.records = offer.user_state(self.home)

    def pend(self, export_id=EXPORT_ID):
        (self.state / "pending" / export_id).write_bytes(b"")

    def fail(self, reason="digest_mismatch", export_id=EXPORT_ID):
        (self.state / claim.FAILED_NAME).write_text(json.dumps(
            {"schema": claim.FAILED_SCHEMA, "export_id": export_id, "reason": reason}))

    def handed_off(self, export_id=EXPORT_ID):
        folder = self.home / ".local/share/omarchy-migration/incoming" / export_id
        folder.mkdir(parents=True)
        (folder / "bundle.age").write_bytes(b"x")

    def record(self, folder, name):
        (self.records / folder).mkdir(parents=True, exist_ok=True)
        (self.records / folder / name).write_text("{}")

    def test_nothing_to_say_without_an_export_or_a_failure(self):
        self.assertEqual(offer.decide(self.state, self.home), [])
        self.assertEqual(offer.decide(self.root / "missing", self.home), [])

    def test_a_claimed_or_handed_off_export_is_offered_until_imported_or_dismissed(self):
        self.pend()
        (self.state / "pending" / "not-an-id").write_bytes(b"")
        self.assertEqual(offer.decide(self.state, self.home), [("offer", [EXPORT_ID])])
        self.handed_off(OTHER_ID)
        self.assertEqual(offer.decide(self.state, self.home), [("offer", sorted([EXPORT_ID, OTHER_ID]))])
        self.record("imported", f"{EXPORT_ID}.json")
        self.assertEqual(offer.decide(self.state, self.home), [("offer", [OTHER_ID])])
        self.record("dismissed", OTHER_ID)
        self.assertEqual(offer.decide(self.state, self.home), [])

    def test_a_handed_off_export_without_its_bundle_is_not_offered(self):
        self.handed_off()
        (self.home / ".local/share/omarchy-migration/incoming" / EXPORT_ID / "bundle.age").unlink()
        self.assertEqual(offer.decide(self.state, self.home), [])

    def test_a_failed_claim_is_told_once_per_failure(self):
        self.fail()
        actions = offer.decide(self.state, self.home)
        self.assertEqual(actions, [("failed", {"schema": claim.FAILED_SCHEMA, "export_id": EXPORT_ID,
                                               "reason": "digest_mismatch"})])
        offer.remember_failure(self.home, actions[0][1])
        self.assertEqual(offer.decide(self.state, self.home), [])
        self.fail(reason="region_invalid", export_id=None)
        self.assertEqual(offer.decide(self.state, self.home)[0][0], "failed")

    def test_a_malformed_failure_record_is_ignored(self):
        for text in ("{", "[]", json.dumps({"schema": claim.FAILED_SCHEMA, "export_id": EXPORT_ID}),
                     json.dumps({"schema": claim.FAILED_SCHEMA, "export_id": "nope", "reason": "x"}),
                     json.dumps({"schema": claim.FAILED_SCHEMA, "export_id": None, "reason": "Bad Code"}),
                     json.dumps({"schema": "other", "export_id": None, "reason": "x"}), "x" * 5000):
            with self.subTest(text=text[:40]):
                (self.state / claim.FAILED_NAME).write_text(text)
                self.assertEqual(offer.decide(self.state, self.home), [])


class NotificationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="migration-offer-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state = self.root / "state"
        (self.state / "pending").mkdir(parents=True)
        self.home = self.root / "home"
        self.home.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "log"

    def tool(self, name):
        path = self.bin / name
        path.write_text(f'#!/bin/sh\nprintf "%s\\n" "{name}" "$@" "--" >> "{self.log}"\n')
        path.chmod(0o755)

    def run_offer(self):
        with patch.dict(os.environ, {"PATH": str(self.bin)}), patch.object(offer, "FALLBACK_PATH", ()), \
                contextlib.redirect_stderr(io.StringIO()):
            return offer.main(["--state", str(self.state), "--home", str(self.home)])

    def calls(self):
        if not self.log.exists():
            return []
        calls, current = [], []
        for line in self.log.read_text().splitlines():
            if line == "--":
                calls.append(current)
                current = []
            else:
                current.append(line)
        return calls

    def test_sends_the_offer_after_waiting_for_notifications(self):
        self.tool("omarchy-notification-wait")
        self.tool("omarchy-notification-send")
        (self.state / "pending" / EXPORT_ID).write_bytes(b"")
        self.assertEqual(self.run_offer(), 0)
        self.assertEqual(self.calls(), [
            ["omarchy-notification-wait"],
            ["omarchy-notification-send", "-u", "critical", offer.OFFER_TITLE, offer.OFFER_BODY, "--exec",
             "omarchy-launch-floating-terminal-with-presentation", "omarchy-migration-import"],
        ])

    def test_tells_a_failed_claim_once(self):
        self.tool("omarchy-notification-send")
        (self.state / claim.FAILED_NAME).write_text(json.dumps(
            {"schema": claim.FAILED_SCHEMA, "export_id": None, "reason": "digest_mismatch"}))
        self.run_offer()
        self.run_offer()
        self.assertEqual(self.calls(), [["omarchy-notification-send", "-u", "critical", offer.FAILED_TITLE, offer.FAILED_BODY]])

    def test_stays_quiet_without_the_notification_tools(self):
        (self.state / "pending" / EXPORT_ID).write_bytes(b"")
        self.assertEqual(self.run_offer(), 0)
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()
