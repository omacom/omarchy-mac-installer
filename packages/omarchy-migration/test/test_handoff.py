"""The handoff gives a claimed export only to its sudo caller, inside their home, without following links."""

import getpass
import io
import contextlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from omarchy_migration import handoff

EXPORT_ID = "1b4e28ba-2fa1-41d2-883f-0016d3cca427"
RECEIPT = json.loads((Path(__file__).resolve().parent / "fixtures/valid/receipt.json").read_text())


def account(home, uid=None, name=None):
    return SimpleNamespace(pw_name=name or getpass.getuser(), pw_uid=os.getuid() if uid is None else uid,
                           pw_gid=os.getgid(), pw_dir=str(home))


class InvokingUserTests(unittest.TestCase):
    def lookup(self, uid):
        if uid == 4242:
            raise KeyError(uid)
        return SimpleNamespace(pw_name="owner", pw_uid=uid, pw_gid=uid, pw_dir="/home/owner" if uid != 1001 else "home")

    def refused(self, environ, code):
        with self.assertRaises(handoff.HandoffError) as caught:
            handoff.invoking_user(environ, self.lookup)
        self.assertEqual(caught.exception.code, code)

    def test_only_a_regular_sudo_caller_is_accepted(self):
        self.assertEqual(handoff.invoking_user({"SUDO_UID": "1000", "SUDO_USER": "owner"}, self.lookup).pw_dir, "/home/owner")
        self.refused({}, "not_run_by_sudo")
        self.refused({"SUDO_UID": "1000"}, "not_run_by_sudo")
        self.refused({"SUDO_UID": "owner", "SUDO_USER": "owner"}, "not_run_by_sudo")
        self.refused({"SUDO_UID": "0", "SUDO_USER": "root"}, "user_refused")
        self.refused({"SUDO_UID": "999", "SUDO_USER": "owner"}, "user_refused")
        self.refused({"SUDO_UID": "1000", "SUDO_USER": "someone-else"}, "user_refused")
        self.refused({"SUDO_UID": "4242", "SUDO_USER": "owner"}, "user_refused")
        self.refused({"SUDO_UID": "1001", "SUDO_USER": "owner"}, "user_refused")

    def test_the_command_refuses_without_root(self):
        if os.geteuid() == 0:
            self.skipTest("runs unprivileged")
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            self.assertEqual(handoff.main([]), 1)
        self.assertEqual(json.loads(errors.getvalue()), {"error": "not_root"})


@unittest.skipUnless(sys.platform.startswith("linux") and os.geteuid() != 0, "needs Linux and an unprivileged owner")
class DeliverTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="migration-handoff-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.root.chmod(0o755)
        self.home = self.root / "home"
        self.home.mkdir(mode=0o700)
        self.source = self.root / "claimed"
        self.source.mkdir(mode=0o700)
        self.bundle_bytes = os.urandom(70000)
        (self.source / "bundle.age").write_bytes(self.bundle_bytes)
        (self.source / "receipt.json").write_text(json.dumps(RECEIPT))
        self.elsewhere = self.root / "elsewhere"
        self.elsewhere.mkdir()

    def deliver(self, home=None):
        bundle = os.open(self.source / "bundle.age", os.O_RDONLY)
        receipt = os.open(self.source / "receipt.json", os.O_RDONLY)
        try:
            return handoff.deliver(str(home or self.home), EXPORT_ID, bundle, receipt)
        finally:
            os.close(bundle)
            os.close(receipt)

    def refused(self, code="unsafe_home", home=None):
        with self.assertRaises(handoff.HandoffError) as caught:
            self.deliver(home)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(list(self.elsewhere.rglob("*")), [])

    def test_delivers_private_copies_into_the_home(self):
        folder = self.deliver()
        self.assertEqual(folder, self.home / ".local/share/omarchy-migration/incoming" / EXPORT_ID)
        self.assertEqual((folder / "bundle.age").read_bytes(), self.bundle_bytes)
        self.assertEqual(json.loads((folder / "receipt.json").read_text()), RECEIPT)
        self.assertEqual(sorted(os.listdir(folder)), ["bundle.age", "receipt.json"])
        for path in (folder, folder.parent, self.home / ".local"):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700, path)
        for name in ("bundle.age", "receipt.json"):
            self.assertEqual(stat.S_IMODE((folder / name).stat().st_mode), 0o600)
        self.assertEqual(self.deliver(), folder)

    def test_a_linked_folder_in_the_home_is_refused(self):
        (self.home / ".local").symlink_to(self.elsewhere)
        self.refused()

    def test_a_linked_export_folder_is_refused(self):
        incoming = self.home / ".local/share/omarchy-migration/incoming"
        incoming.mkdir(parents=True, mode=0o700)
        (incoming / EXPORT_ID).symlink_to(self.elsewhere)
        self.refused()

    def test_a_folder_others_can_write_is_refused(self):
        (self.home / ".local").mkdir(mode=0o700)
        (self.home / ".local").chmod(0o777)
        self.refused()

    def test_a_home_owned_by_someone_else_is_refused(self):
        self.refused(home=Path("/"))

    def test_links_planted_at_the_file_names_are_replaced_not_followed(self):
        folder = self.home / ".local/share/omarchy-migration/incoming" / EXPORT_ID
        folder.mkdir(parents=True, mode=0o700)
        victim = self.elsewhere / "victim"
        (folder / "bundle.age").symlink_to(victim)
        (folder / "bundle.age.partial").symlink_to(victim)
        self.deliver()
        self.assertFalse(victim.exists())
        self.assertFalse((folder / "bundle.age").is_symlink())
        self.assertEqual((folder / "bundle.age").read_bytes(), self.bundle_bytes)


@unittest.skipUnless(sys.platform.startswith("linux") and os.geteuid() != 0, "needs Linux and an unprivileged owner")
class HandoffTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="migration-handoff-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.root.chmod(0o755)
        self.homes = self.root / "home"
        self.homes.mkdir(mode=0o755)
        self.home = self.homes / "owner"
        self.home.mkdir(mode=0o700)
        self.state = self.root / "state"
        export = self.state / "incoming" / EXPORT_ID
        export.mkdir(parents=True, mode=0o700)
        self.bundle_bytes = os.urandom(5000)
        receipt = json.loads(json.dumps(RECEIPT))
        receipt["export_id"] = EXPORT_ID
        receipt["bundle"]["bytes"] = len(self.bundle_bytes)
        (export / "bundle.age").write_bytes(self.bundle_bytes)
        (export / "receipt.json").write_text(json.dumps(receipt))
        (self.state / "pending").mkdir(mode=0o755)
        (self.state / "pending" / EXPORT_ID).write_bytes(b"")
        (self.state / "pending" / "not-an-export").write_bytes(b"")

    def handoff(self):
        with patch.object(handoff, "_as_user", lambda _account, work: work()):
            return handoff.handoff(account(self.home), self.state, trusted=os.getuid())

    def test_moves_each_pending_export_and_prepares_the_work_folder(self):
        result = self.handoff()
        work = self.homes / ".omarchy-migration" / str(os.getuid())
        self.assertEqual(result, {"work": str(work), "exports": [EXPORT_ID],
                                  "incoming": str(self.home / ".local/share/omarchy-migration/incoming")})
        self.assertEqual((self.home / ".local/share/omarchy-migration/incoming" / EXPORT_ID / "bundle.age").read_bytes(),
                         self.bundle_bytes)
        self.assertFalse((self.state / "incoming" / EXPORT_ID).exists())
        self.assertFalse((self.state / "pending" / EXPORT_ID).exists())
        self.assertEqual(stat.S_IMODE(work.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(work.parent.stat().st_mode), 0o711)
        self.assertEqual(self.handoff()["exports"], [])

    def test_a_claimed_export_that_does_not_match_its_receipt_stays(self):
        (self.state / "incoming" / EXPORT_ID / "bundle.age").write_bytes(b"short")
        with self.assertRaises(handoff.HandoffError) as caught:
            self.handoff()
        self.assertEqual(caught.exception.code, "claimed_export_invalid")
        self.assertTrue((self.state / "pending" / EXPORT_ID).exists())
        self.assertFalse((self.home / ".local").exists())

    def test_the_work_folder_needs_a_trusted_parent(self):
        self.homes.chmod(0o777)
        with self.assertRaises(handoff.HandoffError) as caught:
            self.handoff()
        self.assertEqual(caught.exception.code, "work_unavailable")
        self.homes.chmod(0o755)
        (self.homes / ".omarchy-migration").symlink_to(self.root)
        with self.assertRaises(handoff.HandoffError) as caught:
            self.handoff()
        self.assertEqual(caught.exception.code, "work_unavailable")
        with self.assertRaises(handoff.HandoffError):
            handoff.prepare_work(self.home, os.getuid(), os.getgid(), trusted=0)

    def test_nothing_waiting_still_prepares_the_work_folder(self):
        result = handoff.handoff(account(self.home), self.root / "missing-state", trusted=os.getuid())
        self.assertEqual(result["exports"], [])
        self.assertTrue(Path(result["work"]).is_dir())


if __name__ == "__main__":
    unittest.main()
