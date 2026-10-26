"""Disposable target behavior across authentication, publication and retry."""

import copy
import errno
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from omarchy_migration import probe, restore
from omarchy_migration.dependency import configured_age


SECRET = b"synthetic-only-otter-maple-window-cobalt"


class RestoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.age = configured_age()
        if cls.age is None:
            raise unittest.SkipTest("set OMARCHY_TEST_AGE and OMARCHY_TEST_AGE_SHA256")
        if os.geteuid() == 0:
            raise unittest.SkipTest("restoration experiment requires an unprivileged owner")
        cls.source = tempfile.TemporaryDirectory(prefix="migration-restore-source-")
        cls.addClassCleanup(cls.source.cleanup)
        cls.source_root = Path(cls.source.name)
        cls.files = {}
        for number, (path, contents, mode) in enumerate((
            (".config/unknown/settings", b"custom setting\n", 0o600),
            ("Projects/demo/draft λ.txt", b"untracked synthetic draft\n", 0o640),
            ("Projects/demo/run", b"#!/bin/bash\ntouch SHOULD-NOT-EXECUTE\n", 0o750),
            ("empty", b"", 0o400),
        )):
            source = cls.source_root / str(number)
            source.write_bytes(contents)
            source.chmod(mode)
            os.utime(source, ns=(1720000000123456789, 1720000000123456789))
            cls.files[path] = source
        cls.manifest = probe.make_manifest(cls.files)
        cls.ciphertext = cls.source_root / "bundle.age"
        probe.encrypt(cls.age, SECRET, cls.ciphertext,
                      lambda stream: probe.write_archive(stream, cls.manifest, cls.files))

    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix="migration-restore-test-")
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.target = self.root / "destination"
        self.job = self.root / "job"
        self.target.mkdir(mode=0o700)
        self.job.mkdir(mode=0o700)

    def verified(self, ciphertext=None, secret=SECRET):
        return restore.verified_bundle(self.age, secret, ciphertext or self.ciphertext)

    def run_restore(self, bundle):
        with restore.Restorer(bundle, self.target, self.job) as importer:
            return importer.apply(importer.plan())

    def first_path(self):
        return self.target / self.manifest["entries"][0]["path"]

    def interrupt_publication(self, bundle, *, after_link=True):
        link = os.link

        def interrupt(*args, **kwargs):
            if after_link:
                link(*args, **kwargs)
            raise RuntimeError("synthetic process interruption")

        with restore.Restorer(bundle, self.target, self.job) as importer:
            plan = importer.plan()
            with patch.object(restore.os, "link", side_effect=interrupt):
                with self.assertRaisesRegex(RuntimeError, "process interruption"):
                    importer.apply(plan)

    def test_roundtrip_plans_without_writes_and_preserves_content_metadata_owner(self):
        with self.verified() as bundle:
            with restore.Restorer(bundle, self.target, self.job) as importer:
                plan = importer.plan()
                self.assertEqual({action.status for action in plan}, {"create"})
                self.assertEqual(list(self.target.iterdir()), [])
                self.assertEqual(list(self.job.iterdir()), [])
                report = importer.apply(plan)
                self.assertEqual({action.status for action in report}, {"restored"})
            for entry in self.manifest["entries"]:
                path = self.target / entry["path"]
                self.assertEqual(path.read_bytes(), self.files[entry["path"]].read_bytes())
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), entry["mode"])
                self.assertEqual(path.stat().st_mtime_ns, entry["mtime_ns"])
                self.assertEqual(path.stat().st_uid, os.geteuid())
                self.assertEqual(path.stat().st_nlink, 1)
            self.assertFalse((self.target / "SHOULD-NOT-EXECUTE").exists())
            self.assertEqual([p.name for p in self.job.iterdir()], ["journal.json"])
            self.assertEqual(stat.S_IMODE((self.job / "journal.json").stat().st_mode), 0o600)

    def test_authentication_failures_expose_no_bundle_and_remove_plaintext(self):
        corrupted = bytearray(self.ciphertext.read_bytes())
        corrupted[-1] ^= 1
        altered = self.root / "altered.age"
        altered.write_bytes(corrupted)
        truncated = self.root / "truncated.age"
        truncated.write_bytes(self.ciphertext.read_bytes()[:-16])
        directory = tempfile.TemporaryDirectory
        created = []

        def record_directory(*args, **kwargs):
            scratch = directory(*args, **kwargs)
            created.append(Path(scratch.name))
            return scratch

        for ciphertext, secret in ((self.ciphertext, b"wrong-secret"),
                                   (altered, SECRET), (truncated, SECRET)):
            with self.subTest(ciphertext=ciphertext.name), patch.object(restore.tempfile, "TemporaryDirectory", side_effect=record_directory):
                with self.assertRaises(probe.Rejected):
                    with self.verified(ciphertext, secret):
                        self.fail("unauthenticated bundle escaped")
                self.assertTrue(all(not path.exists() for path in created))
                self.assertEqual(list(self.target.iterdir()), [])
                self.assertEqual(list(self.job.iterdir()), [])

    def test_verified_handle_expires_and_scratch_is_private_and_cleaned(self):
        with self.verified() as bundle:
            scratch = bundle._directory
            self.assertEqual(stat.S_IMODE(scratch.stat().st_mode), 0o700)
            self.assertTrue(all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in scratch.iterdir()))
        self.assertFalse(scratch.exists())
        with self.assertRaisesRegex(probe.Rejected, "closed"):
            restore.Restorer(bundle, self.target, self.job)

    def test_existing_differing_files_and_symlinks_are_preserved(self):
        first = self.first_path()
        first.parent.mkdir(parents=True)
        first.write_bytes(b"native defaults and user edits\n")
        outside = self.root / "outside"
        outside.mkdir()
        (self.target / "Projects").symlink_to(outside, target_is_directory=True)
        (self.target / "empty").symlink_to(outside / "should-not-exist")
        with self.verified() as bundle:
            report = self.run_restore(bundle)
        self.assertEqual({action.status for action in report}, {"conflict"})
        self.assertEqual(first.read_bytes(), b"native defaults and user edits\n")
        self.assertEqual(list(outside.iterdir()), [])
        self.assertTrue((self.target / "Projects").is_symlink())
        self.assertTrue((self.target / "empty").is_symlink())

    def test_fifo_and_file_used_as_parent_are_conflicts_without_blocking(self):
        os.mkfifo(self.target / "empty", 0o600)
        (self.target / "Projects").write_bytes(b"not a directory")
        with self.verified() as bundle:
            report = self.run_restore(bundle)
        self.assertEqual([action.status for action in report], ["restored", "conflict", "conflict", "conflict"])
        self.assertTrue(stat.S_ISFIFO((self.target / "empty").stat().st_mode))

    def test_identical_existing_file_is_kept_without_changing_its_inode(self):
        source = self.files["empty"]
        destination = self.target / "empty"
        destination.write_bytes(source.read_bytes())
        destination.chmod(source.stat().st_mode & 0o777)
        os.utime(destination, ns=(source.stat().st_mtime_ns, source.stat().st_mtime_ns))
        before = destination.stat()
        with self.verified() as bundle:
            report = self.run_restore(bundle)
        self.assertEqual(report[-1].status, "present")
        self.assertEqual(destination.stat().st_ino, before.st_ino)
        self.assertEqual(destination.stat().st_mtime_ns, before.st_mtime_ns)

    def test_repeat_preserves_user_edits_deletions_permissions_and_replacements(self):
        with self.verified() as bundle:
            self.run_restore(bundle)
            entries = self.manifest["entries"]
            paths = [self.target / entry["path"] for entry in entries]
            paths[0].write_bytes(b"edited after restore")
            paths[1].unlink()
            paths[2].chmod(0o700)
            paths[3].unlink()
            paths[3].write_bytes(b"replacement")
            report = self.run_restore(bundle)
        self.assertEqual({action.status for action in report}, {"conflict"})
        self.assertEqual(paths[0].read_bytes(), b"edited after restore")
        self.assertFalse(paths[1].exists())
        self.assertEqual(stat.S_IMODE(paths[2].stat().st_mode), 0o700)
        self.assertEqual(paths[3].read_bytes(), b"replacement")

    def test_unchanged_retry_does_not_republish_files(self):
        with self.verified() as bundle:
            self.run_restore(bundle)
            original = {entry["path"]: (self.target / entry["path"]).stat().st_ino
                        for entry in self.manifest["entries"]}
            with patch.object(restore.os, "link", side_effect=AssertionError("unexpected republication")):
                report = self.run_restore(bundle)
        self.assertEqual({action.status for action in report}, {"restored"})
        self.assertEqual(original, {path: (self.target / path).stat().st_ino for path in original})

    def test_link_before_completion_interruption_recovers_positive_inode_evidence(self):
        with self.verified() as bundle:
            self.interrupt_publication(bundle)
            inode = self.first_path().stat().st_ino
            self.assertEqual(self.first_path().stat().st_nlink, 2)
            report = self.run_restore(bundle)
        self.assertEqual({action.status for action in report}, {"restored"})
        self.assertEqual(self.first_path().stat().st_ino, inode)
        self.assertEqual(self.first_path().stat().st_nlink, 1)

    def test_fresh_process_recovers_after_actual_sigkill_at_publication(self):
        child_scratch = self.root / "child-scratch"
        child_scratch.mkdir(mode=0o700)
        script = """
import os, signal, sys
from pathlib import Path
from unittest.mock import patch
from omarchy_migration import restore
from omarchy_migration.dependency import configured_age
link = os.link
def crash(*args, **kwargs):
    link(*args, **kwargs)
    os.kill(os.getpid(), signal.SIGKILL)
with restore.verified_bundle(configured_age(), b'synthetic-only-otter-maple-window-cobalt', Path(sys.argv[1])) as bundle:
    with restore.Restorer(bundle, Path(sys.argv[2]), Path(sys.argv[3])) as importer:
        plan = importer.plan()
        with patch.object(restore.os, 'link', side_effect=crash):
            importer.apply(plan)
"""
        result = subprocess.run(
            [sys.executable, "-c", script, str(self.ciphertext), str(self.target), str(self.job)],
            env={**os.environ, "TMPDIR": str(child_scratch)}, capture_output=True, timeout=20,
        )
        self.assertEqual(result.returncode, -signal.SIGKILL, result.stderr.decode())
        inode = self.first_path().stat().st_ino
        with self.verified() as bundle:
            report = self.run_restore(bundle)
        self.assertEqual({action.status for action in report}, {"restored"})
        self.assertEqual(self.first_path().stat().st_ino, inode)

    def test_ambiguous_missing_file_after_intent_is_never_recreated(self):
        for after_link in (False, True):
            with self.subTest(after_link=after_link):
                # Each publication experiment needs its own durable job.
                self.target = self.root / f"destination-{after_link}"
                self.job = self.root / f"job-{after_link}"
                self.target.mkdir(mode=0o700)
                self.job.mkdir(mode=0o700)
                with self.verified() as bundle:
                    self.interrupt_publication(bundle, after_link=after_link)
                    if after_link:
                        self.first_path().unlink()
                    report = self.run_restore(bundle)
                self.assertEqual(report[0].status, "conflict")
                self.assertFalse(self.first_path().exists())
                self.assertEqual({action.status for action in report[1:]}, {"restored"})

    def test_interrupted_pending_hardlink_is_never_repaired_after_user_edit(self):
        with self.verified() as bundle:
            self.interrupt_publication(bundle)
            self.first_path().write_bytes(b"later user work")
            self.first_path().chmod(0o640)
            report = self.run_restore(bundle)
        self.assertEqual(report[0].status, "conflict")
        self.assertEqual(self.first_path().read_bytes(), b"later user work")
        self.assertEqual(stat.S_IMODE(self.first_path().stat().st_mode), 0o640)

    def test_file_appearing_after_plan_is_preserved(self):
        with self.verified() as bundle:
            with restore.Restorer(bundle, self.target, self.job) as importer:
                plan = importer.plan()
                first = self.first_path()
                first.parent.mkdir(parents=True)
                first.write_bytes(b"new work while preview open")
                report = importer.apply(plan)
        self.assertEqual(report[0].status, "conflict")
        self.assertEqual(first.read_bytes(), b"new work while preview open")

    def test_nonoverwriting_publication_handles_last_moment_creation(self):
        original_link = os.link
        count = 0

        def race(source, destination, **kwargs):
            nonlocal count
            count += 1
            if count == 1:
                fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600,
                             dir_fd=kwargs["dst_dir_fd"])
                with os.fdopen(fd, "wb") as output:
                    output.write(b"racing user file")
            return original_link(source, destination, **kwargs)

        with self.verified() as bundle, patch.object(restore.os, "link", side_effect=race):
            report = self.run_restore(bundle)
        self.assertEqual(report[0].status, "conflict")
        self.assertEqual(self.first_path().read_bytes(), b"racing user file")

    def test_job_is_locked_and_plan_cannot_be_replayed(self):
        with self.verified() as bundle:
            with restore.Restorer(bundle, self.target, self.job) as importer:
                with self.assertRaisesRegex(probe.Rejected, "active"):
                    restore.Restorer(bundle, self.target, self.job)
                old_plan = importer.plan()
                current_plan = importer.plan()
                with self.assertRaisesRegex(probe.Rejected, "latest plan"):
                    importer.apply(old_plan)
                importer.apply(current_plan)
                with self.assertRaisesRegex(probe.Rejected, "latest plan"):
                    importer.apply(current_plan)

    def test_wrong_target_or_changed_manifest_cannot_reuse_job(self):
        with self.verified() as bundle:
            self.run_restore(bundle)
            other = self.root / "other-target"
            other.mkdir(mode=0o700)
            with self.assertRaisesRegex(probe.Rejected, "binding differs"):
                restore.Restorer(bundle, other, self.job)
        changed = copy.deepcopy(self.manifest)
        changed["entries"][0]["mode"] = 0o640
        altered = self.root / "changed-manifest.age"
        probe.encrypt(self.age, SECRET, altered, lambda stream: probe.write_archive(stream, changed, self.files))
        with self.verified(altered) as bundle:
            with self.assertRaisesRegex(probe.Rejected, "binding differs"):
                restore.Restorer(bundle, self.target, self.job)

    def test_copied_journal_cannot_create_second_job_for_the_same_destination(self):
        with self.verified() as bundle:
            self.run_restore(bundle)
            other = self.root / "copied-job"
            other.mkdir(mode=0o700)
            journal = other / "journal.json"
            journal.write_bytes((self.job / "journal.json").read_bytes())
            journal.chmod(0o600)
            with self.assertRaisesRegex(probe.Rejected, "binding differs"):
                restore.Restorer(bundle, self.target, other)

    def test_replaced_destination_directory_invalidates_plan(self):
        with self.verified() as bundle:
            with restore.Restorer(bundle, self.target, self.job) as importer:
                plan = importer.plan()
                old = self.root / "old-target"
                self.target.rename(old)
                self.target.mkdir(mode=0o700)
                with self.assertRaisesRegex(probe.Rejected, "identity changed"):
                    importer.apply(plan)
                self.assertEqual(list(old.iterdir()), [])
                self.assertEqual(list(self.target.iterdir()), [])

    def test_nested_directory_replacement_after_plan_is_a_conflict(self):
        parent = self.first_path().parent
        parent.mkdir(parents=True)
        with self.verified() as bundle:
            with restore.Restorer(bundle, self.target, self.job) as importer:
                plan = importer.plan()
                old_parent = self.root / "old-parent"
                parent.rename(old_parent)
                parent.mkdir()
                report = importer.apply(plan)
        self.assertEqual(report[0].status, "conflict")
        self.assertEqual(list(parent.iterdir()), [])
        self.assertEqual(list(old_parent.iterdir()), [])
        self.assertEqual({action.status for action in report[1:]}, {"restored"})

    def test_unrelated_job_insecure_job_and_nested_job_are_rejected(self):
        with self.verified() as bundle:
            (self.job / "keep").write_bytes(b"unrelated")
            with self.assertRaisesRegex(probe.Rejected, "unrecognized"):
                restore.Restorer(bundle, self.target, self.job)
            self.assertEqual((self.job / "keep").read_bytes(), b"unrelated")
            self.job.chmod(0o755)
            with self.assertRaisesRegex(probe.Rejected, "0700"):
                restore.Restorer(bundle, self.target, self.job)
            nested = self.target / "job"
            nested.mkdir(mode=0o700)
            for target, job in ((self.target, nested), (nested, self.target), (self.target, self.target)):
                with self.assertRaisesRegex(probe.Rejected, "separate trees"):
                    restore.Restorer(bundle, target, job)

    def test_symlink_journal_and_malformed_journal_are_rejected_without_target_writes(self):
        outside = self.root / "outside-journal"
        outside.write_bytes(b"unchanged")
        journal = self.job / "journal.json"
        journal.symlink_to(outside)
        with self.verified() as bundle:
            with self.assertRaises(OSError):
                restore.Restorer(bundle, self.target, self.job)
            self.assertEqual(outside.read_bytes(), b"unchanged")
            journal.unlink()
            journal.write_bytes(b"{")
            journal.chmod(0o600)
            with self.assertRaisesRegex(probe.Rejected, "invalid restore journal"):
                restore.Restorer(bundle, self.target, self.job)
        self.assertEqual(list(self.target.iterdir()), [])

    def test_journal_sync_failure_cannot_publish_destination_files(self):
        with self.verified() as bundle:
            with restore.Restorer(bundle, self.target, self.job) as importer:
                plan = importer.plan()
                with patch.object(restore.os, "fsync", side_effect=OSError(errno.ENOSPC, "synthetic full disk")):
                    with self.assertRaises(OSError):
                        importer.apply(plan)
        self.assertEqual(list(self.target.iterdir()), [])

    def test_publication_intent_sync_failure_cannot_publish_even_after_data_sync(self):
        sync = os.fsync

        def fail_intent(fd):
            # Intents are appended to the journal and made durable by its fsync.
            journal = self.job / "journal.json"
            if (journal.exists() and stat.S_ISREG(os.fstat(fd).st_mode)
                    and os.fstat(fd).st_ino == journal.stat().st_ino and restore.read_journal(journal)["entries"]):
                raise OSError(errno.ENOSPC, "synthetic intent sync failure")
            return sync(fd)

        with self.verified() as bundle:
            with patch.object(restore.os, "fsync", side_effect=fail_intent):
                with self.assertRaisesRegex(OSError, "intent sync failure"):
                    self.run_restore(bundle)
            self.assertEqual(list(self.target.iterdir()), [])
            report = self.run_restore(bundle)
        self.assertEqual(report[0].status, "conflict")
        self.assertFalse(self.first_path().exists())

    def test_root_symlinks_and_job_privacy_changes_are_rejected(self):
        target_alias, job_alias = self.root / "target-alias", self.root / "job-alias"
        target_alias.symlink_to(self.target, target_is_directory=True)
        job_alias.symlink_to(self.job, target_is_directory=True)
        with self.verified() as bundle:
            for target, job in ((target_alias, self.job), (self.target, job_alias)):
                with self.assertRaises(OSError):
                    restore.Restorer(bundle, target, job)
            with restore.Restorer(bundle, self.target, self.job) as importer:
                plan = importer.plan()
                self.job.chmod(0o755)
                with self.assertRaisesRegex(probe.Rejected, "remain private"):
                    importer.apply(plan)
        self.assertEqual(list(self.target.iterdir()), [])

    def test_changed_verified_scratch_is_rejected_before_file_publication(self):
        with self.verified() as bundle:
            first = bundle._directory / "00000000"
            first.write_bytes(b"X" * first.stat().st_size)
            with self.assertRaisesRegex(probe.Rejected, "digest differs"):
                self.run_restore(bundle)
        self.assertEqual(list(self.target.iterdir()), [])

    def test_unreadable_source_mode_is_reported_without_changing_permissions(self):
        changed = copy.deepcopy(self.manifest)
        changed["entries"][0]["mode"] = 0o000
        ciphertext = self.root / "unreadable.age"
        probe.encrypt(self.age, SECRET, ciphertext, lambda stream: probe.write_archive(stream, changed, self.files))
        with self.verified(ciphertext) as bundle:
            report = self.run_restore(bundle)
        self.assertEqual(report[0].status, "conflict")
        self.assertIn("unreadable", report[0].reason)
        self.assertFalse(self.first_path().exists())


if __name__ == "__main__":
    unittest.main()
