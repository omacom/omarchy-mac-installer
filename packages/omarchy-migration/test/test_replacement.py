"""Approved synthetic replacements preserve independent backups across failures."""

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
FILE = ".config/demo/settings"
OLD = b"native default with local customizations\n"
NEW = b"selected synthetic personal preferences\n"
MTIME = 1720000000123456789
OLD_MTIME = MTIME - 1000000000


class ReplacementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.age = configured_age()
        if cls.age is None or os.geteuid() == 0:
            raise unittest.SkipTest("requires verified age and an unprivileged owner")
        cls.source = tempfile.TemporaryDirectory(prefix="migration-replacement-source-")
        cls.addClassCleanup(cls.source.cleanup)
        cls.source_root = Path(cls.source.name)
        tree = cls.source_root / "tree"
        (tree / ".config/demo").mkdir(parents=True)
        (tree / FILE).write_bytes(NEW)
        (tree / FILE).chmod(0o600)
        (tree / "other").write_bytes(b"unrelated selected file")
        (tree / "shortcut").symlink_to(FILE)
        paths = {name: tree / name for name in (".config", ".config/demo", FILE, "other", "shortcut")}
        for path in paths.values():
            os.utime(path, ns=(MTIME, MTIME), follow_symlinks=False)
        cls.manifest = probe.make_tree_manifest(paths)
        cls.ciphertext = cls.source_root / "tree.age"
        probe.encrypt(cls.age, SECRET, cls.ciphertext,
                      lambda stream: probe.write_archive(stream, cls.manifest, paths))
        files = {FILE: tree / FILE, "other": tree / "other"}
        manifest = probe.make_manifest(files)
        cls.v1 = cls.source_root / "files.age"
        probe.encrypt(cls.age, SECRET, cls.v1, lambda stream: probe.write_archive(stream, manifest, files))

    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix="migration-replacement-test-")
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.destination("initial")

    def destination(self, name):
        self.target, self.job = self.root / f"{name}-target", self.root / f"{name}-job"
        self.target.mkdir(mode=0o700)
        self.job.mkdir(mode=0o700)
        self.file = self.target / FILE
        self.file.parent.mkdir(parents=True, mode=0o700)
        self.file.write_bytes(OLD)
        self.file.chmod(0o640)
        os.utime(self.file, ns=(OLD_MTIME, OLD_MTIME))

    def verified(self, ciphertext=None):
        return restore.verified_bundle(self.age, SECRET, ciphertext or self.ciphertext)

    def run_restore(self, bundle, replace=(FILE,)):
        with restore.Restorer(bundle, self.target, self.job, replace=replace) as importer:
            return {action.path: action for action in importer.apply(importer.plan())}

    def saved(self):
        journal = restore.read_journal(self.job / "journal.json")
        identity = next(entry["object"] for entry in self.manifest["entries"] if entry["path"] == FILE)
        return journal["entries"][identity]

    def assert_backup(self, saved):
        backup = self.job / saved["backup"]["name"]
        self.assertEqual(backup.read_bytes(), OLD)
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)
        self.assertEqual(backup.stat().st_mtime_ns, OLD_MTIME)
        self.assertEqual(backup.stat().st_nlink, 1)
        self.assertEqual(saved["backup"]["original"]["mode"], 0o640)
        return backup

    def test_default_preserves_conflicting_file_and_blocks_dependent_link(self):
        with self.verified() as bundle:
            report = self.run_restore(bundle, replace=())
        self.assertEqual(report[FILE].status, "conflict")
        self.assertEqual(report["shortcut"].status, "conflict")
        self.assertEqual(self.file.read_bytes(), OLD)
        self.assertFalse((self.target / "shortcut").exists())

    def test_approved_replacement_preserves_independent_private_backup_and_links(self):
        alias = self.root / "original-alias"
        os.link(self.file, alias)
        with self.verified() as bundle:
            with restore.Restorer(bundle, self.target, self.job, replace=(FILE,)) as importer:
                plan = importer.plan()
                self.assertEqual(next(action.status for action in plan if action.path == FILE), "replace")
                self.assertEqual(self.file.read_bytes(), OLD)
                self.assertEqual(list(self.job.iterdir()), [])
                report = {action.path: action for action in importer.apply(plan)}
            saved = self.saved()
            backup = self.assert_backup(saved)
            self.assertEqual(report[FILE].status, "replaced")
            self.assertEqual(report[FILE].backup, backup.name)
            self.assertEqual(self.file.read_bytes(), NEW)
            self.assertEqual(stat.S_IMODE(self.file.stat().st_mode), 0o600)
            self.assertEqual(self.file.stat().st_mtime_ns, MTIME)
            self.assertEqual((self.target / "shortcut").read_bytes(), NEW)
            alias.write_bytes(b"later edit through original hardlink")
            self.assertEqual(backup.read_bytes(), OLD)
            inode = self.file.stat().st_ino
            retry = self.run_restore(bundle)
            self.assertEqual(retry[FILE].status, "restored")
            self.assertEqual(retry[FILE].backup, backup.name)
            self.assertEqual(self.file.stat().st_ino, inode)
        self.assertEqual({path.name for path in self.job.iterdir() if not path.name.endswith(".displaced")}, {"journal.json", backup.name})

    def test_v1_regular_file_bundle_supports_explicit_replacement(self):
        with self.verified(self.v1) as bundle:
            report = self.run_restore(bundle)
            self.assertEqual(report[FILE].status, "replaced")
            self.assertEqual(self.run_restore(bundle)[FILE].status, "restored")
        self.assertEqual(self.file.read_bytes(), NEW)

    def test_approval_rejects_unknown_duplicate_directory_link_and_nonlist_values(self):
        with self.verified() as bundle:
            for approval in (("unknown",), (FILE, FILE), (".config",), ("shortcut",), FILE, (True,), {FILE: True}):
                with self.subTest(approval=approval), self.assertRaises(probe.Rejected):
                    restore.Restorer(bundle, self.target, self.job, replace=approval)
        self.assertEqual(self.file.read_bytes(), OLD)
        self.assertEqual(list(self.job.iterdir()), [])

    def test_approval_set_is_bound_to_job_and_cannot_be_changed_on_retry(self):
        with self.verified() as bundle:
            self.run_restore(bundle)
            for approval in ((), (FILE, "other")):
                with self.subTest(approval=approval), self.assertRaisesRegex(probe.Rejected, "binding differs"):
                    restore.Restorer(bundle, self.target, self.job, replace=approval)
        self.assertEqual(self.file.read_bytes(), NEW)

    def test_matching_approved_file_is_retained_and_later_deletion_stays_deleted(self):
        self.file.write_bytes(NEW)
        self.file.chmod(0o600)
        os.utime(self.file, ns=(MTIME, MTIME))
        with self.verified() as bundle:
            self.assertEqual(self.run_restore(bundle)[FILE].status, "present")
            self.assertNotIn("backup", self.saved())
            self.file.unlink()
            self.assertEqual(self.run_restore(bundle)[FILE].status, "conflict")
        self.assertFalse(self.file.exists())

    def test_symlink_and_special_destination_cannot_be_approved_as_regular_file(self):
        with self.verified() as bundle:
            for kind in ("link", "fifo"):
                self.destination(kind)
                self.file.unlink()
                if kind == "link":
                    self.file.symlink_to(self.root / "outside")
                else:
                    os.mkfifo(self.file)
                self.assertEqual(self.run_restore(bundle)[FILE].status, "conflict")
                self.assertTrue(self.file.is_symlink() if kind == "link" else stat.S_ISFIFO(self.file.lstat().st_mode))

    def test_edit_after_plan_preserves_new_work_without_backup_or_replacement(self):
        with self.verified() as bundle:
            with restore.Restorer(bundle, self.target, self.job, replace=(FILE,)) as importer:
                plan = importer.plan()
                self.file.write_bytes(b"later user edit")
                report = {action.path: action for action in importer.apply(plan)}
        self.assertEqual(report[FILE].status, "conflict")
        self.assertEqual(self.file.read_bytes(), b"later user edit")
        self.assertFalse(any("backup" in entry for entry in restore.read_journal(self.job / "journal.json")["entries"].values()))

    def test_parent_replaced_after_plan_does_not_write_into_replacement(self):
        with self.verified() as bundle:
            with restore.Restorer(bundle, self.target, self.job, replace=(FILE,)) as importer:
                plan = importer.plan()
                old_parent = self.root / "old-parent"
                self.file.parent.rename(old_parent)
                self.file.parent.mkdir(mode=0o700)
                self.file.write_bytes(b"replacement parent data")
                report = {action.path: action for action in importer.apply(plan)}
        self.assertEqual(report[FILE].status, "conflict")
        self.assertEqual(self.file.read_bytes(), b"replacement parent data")
        self.assertEqual((old_parent / "settings").read_bytes(), OLD)

    def test_change_during_preparation_retains_backup_and_preserves_current_destination(self):
        prepare = restore.Restorer._prepare

        def change(importer, entry):
            result = prepare(importer, entry)
            if entry["path"] == FILE:
                self.file.write_bytes(b"late edit before publication")
            return result

        with self.verified() as bundle, patch.object(restore.Restorer, "_prepare", change):
            report = self.run_restore(bundle)
        self.assertEqual(report[FILE].status, "conflict")
        self.assertEqual(self.file.read_bytes(), b"late edit before publication")
        self.assert_backup(self.saved())

    def test_edit_at_publication_is_swapped_back_without_loss(self):
        exchange = restore._exchange
        calls = []

        def edit_first(*arguments):
            if not calls:
                self.file.write_bytes(b"autosave at the last moment")
            calls.append(arguments)
            return exchange(*arguments)

        with self.verified() as bundle, patch.object(restore, "_exchange", edit_first):
            report = self.run_restore(bundle)
        if len(calls) < 2:
            self.skipTest("this filesystem cannot swap names atomically")
        self.assertEqual(report[FILE].status, "conflict")
        self.assertEqual(report[FILE].reason, "destination changed during publication")
        self.assertEqual(self.file.read_bytes(), b"autosave at the last moment")
        self.assert_backup(self.saved())

    def test_atomic_publication_leaves_only_the_backup_in_the_job(self):
        with self.verified() as bundle:
            report = self.run_restore(bundle)
        self.assertEqual(report[FILE].status, "replaced")
        self.assertEqual(self.file.read_bytes(), NEW)
        backup = self.saved()["backup"]["name"]
        self.assertEqual({path.name for path in self.job.iterdir()},
                         {"journal.json", backup, backup + ".displaced"})
        self.assertEqual((self.job / (backup + ".displaced")).read_bytes(), OLD)

    def test_backup_failure_prevents_replacement(self):
        sync = os.fsync

        def fail_backup(fd):
            metadata = os.fstat(fd)
            if stat.S_ISREG(metadata.st_mode) and metadata.st_size == len(OLD):
                raise OSError(errno.ENOSPC, "synthetic backup sync failure")
            return sync(fd)

        with self.verified() as bundle, patch.object(restore.os, "fsync", side_effect=fail_backup):
            with self.assertRaisesRegex(OSError, "backup sync"):
                self.run_restore(bundle)
        self.assertEqual(self.file.read_bytes(), OLD)
        self.assertFalse(any("backup" in entry for entry in restore.read_journal(self.job / "journal.json")["entries"].values()))

    def test_original_changed_before_backup_is_rejected_without_publication(self):
        backup = restore.Restorer._backup

        def change(importer, entry, observed):
            self.file.write_bytes(b"changed original before copy")
            return backup(importer, entry, observed)

        with self.verified() as bundle, patch.object(restore.Restorer, "_backup", change):
            with self.assertRaisesRegex(probe.Rejected, "original changed"):
                self.run_restore(bundle)
        self.assertEqual(self.file.read_bytes(), b"changed original before copy")

    def test_preparation_failure_removes_only_known_unjournaled_backup_before_retry(self):
        prepare = restore.Restorer._prepare

        def fail(importer, entry):
            if entry["path"] == FILE:
                raise OSError(errno.ENOSPC, "synthetic preparation failure")
            return prepare(importer, entry)

        with self.verified() as bundle:
            with patch.object(restore.Restorer, "_prepare", fail), self.assertRaisesRegex(OSError, "preparation"):
                self.run_restore(bundle)
            self.assertEqual(self.file.read_bytes(), OLD)
            self.assertEqual([path.name for path in self.job.iterdir()], ["journal.json"])
            self.assertEqual(self.run_restore(bundle)[FILE].status, "replaced")
            backup = self.assert_backup(self.saved())
            self.assertEqual({path.name for path in self.job.iterdir() if not path.name.endswith(".displaced")}, {"journal.json", backup.name})

    def test_backup_modified_before_publication_preserves_original_and_reports_reference(self):
        prepare = restore.Restorer._prepare

        def damage(importer, entry):
            result = prepare(importer, entry)
            if entry["path"] == FILE:
                for path in self.job.iterdir():
                    if path.name != "journal.json" and path.read_bytes() == OLD:
                        path.write_bytes(b"changed private backup")
            return result

        with self.verified() as bundle, patch.object(restore.Restorer, "_prepare", damage):
            report = self.run_restore(bundle)
        self.assertEqual(report[FILE].status, "conflict")
        self.assertEqual(report[FILE].backup, self.saved()["backup"]["name"])
        self.assertEqual(self.file.read_bytes(), OLD)

    def test_intent_failure_leaves_original_and_retry_reports_uncertainty(self):
        save = restore.Restorer._save

        def fail_intent(importer, *keys):
            save(importer, *keys)
            if any("backup" in entry for entry in importer._journal["entries"].values()):
                raise OSError(errno.ENOSPC, "synthetic intent sync failure")

        with self.verified() as bundle:
            with patch.object(restore.Restorer, "_save", fail_intent), self.assertRaisesRegex(OSError, "intent sync"):
                self.run_restore(bundle)
            self.assertEqual(self.file.read_bytes(), OLD)
            self.assert_backup(self.saved())
            self.assertEqual(self.run_restore(bundle)[FILE].status, "conflict")
        self.assertEqual(self.file.read_bytes(), OLD)

    def test_interruption_before_replace_never_retries_destructive_operation(self):
        replace = os.replace

        def interrupt(source, destination, **kwargs):
            if destination == "settings":
                raise RuntimeError("synthetic interruption before replacement")
            return replace(source, destination, **kwargs)

        with self.verified() as bundle:
            with patch.object(restore, "_exchange", return_value=False), patch.object(
                    restore.os, "replace", side_effect=interrupt), self.assertRaises(RuntimeError):
                self.run_restore(bundle)
            retry = self.run_restore(bundle)[FILE]
            self.assertEqual(retry.status, "conflict")
            self.assertEqual(retry.backup, self.saved()["backup"]["name"])
        self.assertEqual(self.file.read_bytes(), OLD)
        self.assert_backup(self.saved())

    def test_interruption_at_the_swap_never_retries_destructive_operation(self):
        def interrupt(*arguments):
            raise RuntimeError("synthetic interruption before the swap")

        with self.verified() as bundle:
            with patch.object(restore, "_exchange", side_effect=interrupt), self.assertRaises(RuntimeError):
                self.run_restore(bundle)
            retry = self.run_restore(bundle)[FILE]
            self.assertEqual(retry.status, "conflict")
        self.assertEqual(self.file.read_bytes(), OLD)
        self.assert_backup(self.saved())

    def test_real_sigkill_after_the_swap_is_recovered_without_replacing_again(self):
        script = """
import os, signal, sys
from unittest.mock import patch
from omarchy_migration import restore
exchange = restore._exchange
def kill(*arguments):
    if not exchange(*arguments):
        sys.exit(3)
    os.kill(os.getpid(), signal.SIGKILL)
with restore.verified_bundle(sys.argv[1], b'synthetic-only-otter-maple-window-cobalt', sys.argv[2]) as bundle:
    with restore.Restorer(bundle, sys.argv[3], sys.argv[4], replace=('.config/demo/settings',)) as importer:
        with patch.object(restore, '_exchange', side_effect=kill):
            importer.apply(importer.plan())
"""
        scratch = self.root / "child-scratch"
        scratch.mkdir(mode=0o700)
        result = subprocess.run([sys.executable, "-c", script, str(self.age), str(self.ciphertext),
                                 str(self.target), str(self.job)], capture_output=True, timeout=20,
                                env={**os.environ, "TMPDIR": str(scratch)})
        if result.returncode == 3:
            self.skipTest("this filesystem cannot swap names atomically")
        self.assertEqual(result.returncode, -signal.SIGKILL, result.stderr.decode())
        inode = self.file.stat().st_ino
        self.assert_backup(self.saved())
        with self.verified() as bundle:
            self.assertEqual(self.run_restore(bundle)[FILE].status, "restored")
        self.assertEqual(self.file.stat().st_ino, inode)
        self.assertEqual(self.saved()["state"], "applied")
        self.assertEqual(self.file.read_bytes(), NEW)

    def test_real_sigkill_after_a_swap_over_an_edit_puts_the_edit_back(self):
        script = """
import os, signal, sys
from unittest.mock import patch
from omarchy_migration import restore
exchange = restore._exchange
def edit_then_kill(*arguments):
    with open(sys.argv[5], 'wb') as destination:
        destination.write(b'autosave just before the swap')
    if not exchange(*arguments):
        sys.exit(3)
    os.kill(os.getpid(), signal.SIGKILL)
with restore.verified_bundle(sys.argv[1], b'synthetic-only-otter-maple-window-cobalt', sys.argv[2]) as bundle:
    with restore.Restorer(bundle, sys.argv[3], sys.argv[4], replace=('.config/demo/settings',)) as importer:
        with patch.object(restore, '_exchange', side_effect=edit_then_kill):
            importer.apply(importer.plan())
"""
        scratch = self.root / "child-scratch"
        scratch.mkdir(mode=0o700)
        result = subprocess.run([sys.executable, "-c", script, str(self.age), str(self.ciphertext),
                                 str(self.target), str(self.job), str(self.file)], capture_output=True,
                                timeout=20, env={**os.environ, "TMPDIR": str(scratch)})
        if result.returncode == 3:
            self.skipTest("this filesystem cannot swap names atomically")
        self.assertEqual(result.returncode, -signal.SIGKILL, result.stderr.decode())
        self.assertEqual(self.file.read_bytes(), NEW)
        with self.verified() as bundle:
            retry = self.run_restore(bundle)[FILE]
        self.assertEqual(retry.status, "conflict")
        self.assertEqual(retry.reason, "destination changed during publication")
        self.assertEqual(self.file.read_bytes(), b"autosave just before the swap")
        self.assert_backup(self.saved())
        with self.verified() as bundle:
            self.assertEqual(self.run_restore(bundle)[FILE].status, "conflict")
        self.assertEqual(self.file.read_bytes(), b"autosave just before the swap")

    def test_a_write_through_a_descriptor_held_open_is_kept(self):
        with open(self.file, "r+b") as held:
            with self.verified() as bundle:
                report = self.run_restore(bundle)
            self.assertEqual(report[FILE].status, "replaced")
            held.seek(0)
            held.write(b"written by an application that kept the file open")
            held.flush()
        displaced = [path for path in self.job.iterdir() if path.name.endswith(".displaced")]
        if not displaced:
            self.skipTest("this filesystem cannot swap names atomically")
        self.assertEqual(displaced[0].read_bytes()[:16], b"written by an ap")
        self.assertEqual(self.file.read_bytes(), NEW)

    def test_a_link_swapped_out_is_put_back(self):
        exchange = restore._exchange
        calls = []

        def link_first(*arguments):
            if not calls:
                self.file.unlink()
                self.file.symlink_to(self.root / "elsewhere")
            calls.append(arguments)
            return exchange(*arguments)

        with self.verified() as bundle, patch.object(restore, "_exchange", link_first):
            report = self.run_restore(bundle)
        if len(calls) < 2:
            self.skipTest("this filesystem cannot swap names atomically")
        self.assertEqual(report[FILE].status, "conflict")
        self.assertTrue(self.file.is_symlink())

    def test_completion_journal_failure_recovers_new_inode_and_original_backup(self):
        save = restore.Restorer._save

        def fail_completion(importer, *keys):
            if any("backup" in entry and entry["state"] == "applied"
                   for entry in importer._journal["entries"].values()):
                raise OSError(errno.ENOSPC, "synthetic completion failure")
            return save(importer, *keys)

        with self.verified() as bundle:
            with patch.object(restore.Restorer, "_save", fail_completion), self.assertRaisesRegex(OSError, "completion"):
                self.run_restore(bundle)
            inode = self.file.stat().st_ino
            self.assertEqual(self.saved()["state"], "pending")
            self.assertEqual(self.run_restore(bundle)[FILE].status, "restored")
            self.assertEqual(self.file.stat().st_ino, inode)
            self.assert_backup(self.saved())

    def test_real_sigkill_after_replacement_is_recovered_without_replacing_again(self):
        script = """
import os, signal, sys
from unittest.mock import patch
from omarchy_migration import restore
replace = os.replace
def kill(source, destination, **kwargs):
    replace(source, destination, **kwargs)
    if destination == 'settings':
        os.kill(os.getpid(), signal.SIGKILL)
with restore.verified_bundle(sys.argv[1], b'synthetic-only-otter-maple-window-cobalt', sys.argv[2]) as bundle:
    with restore.Restorer(bundle, sys.argv[3], sys.argv[4], replace=('.config/demo/settings',)) as importer:
        with patch.object(restore, '_exchange', return_value=False), patch.object(restore.os, 'replace', side_effect=kill):
            importer.apply(importer.plan())
"""
        scratch = self.root / "child-scratch"
        scratch.mkdir(mode=0o700)
        result = subprocess.run([sys.executable, "-c", script, str(self.age), str(self.ciphertext),
                                 str(self.target), str(self.job)], capture_output=True, timeout=20,
                                env={**os.environ, "TMPDIR": str(scratch)})
        self.assertEqual(result.returncode, -signal.SIGKILL, result.stderr.decode())
        inode = self.file.stat().st_ino
        self.assertEqual(self.saved()["state"], "pending")
        self.assert_backup(self.saved())
        with self.verified() as bundle:
            self.assertEqual(self.run_restore(bundle)[FILE].status, "restored")
        self.assertEqual(self.file.stat().st_ino, inode)
        self.assertEqual(self.saved()["state"], "applied")
        self.assertEqual(self.file.read_bytes(), NEW)

    def test_either_post_replace_directory_sync_failure_recovers_positive_witness(self):
        with self.verified() as bundle:
            for failing in ("job", "parent"):
                self.destination(failing)
                sync, replace, published = os.fsync, os.replace, False
                failing_inode = (self.job if failing == "job" else self.file.parent).stat().st_ino

                def mark(source, destination, **kwargs):
                    nonlocal published
                    result = replace(source, destination, **kwargs)
                    if destination == "settings":
                        published = True
                    return result

                def fail(fd):
                    if published and os.fstat(fd).st_ino == failing_inode:
                        raise OSError(errno.ENOSPC, "synthetic directory sync failure")
                    return sync(fd)

                with patch.object(restore, "_exchange", return_value=False), patch.object(
                        restore.os, "replace", side_effect=mark), patch.object(
                        restore.os, "fsync", side_effect=fail), self.assertRaisesRegex(OSError, "directory sync"):
                    self.run_restore(bundle)
                inode = self.file.stat().st_ino
                self.assertEqual(self.saved()["state"], "pending")
                self.assertEqual(self.run_restore(bundle)[FILE].status, "restored")
                self.assertEqual(self.file.stat().st_ino, inode)
                self.assert_backup(self.saved())

    def test_later_edit_deletion_or_new_inode_remains_conflict_even_with_approval(self):
        with self.verified() as bundle:
            for change in ("edit", "delete", "replace"):
                self.destination(change)
                self.run_restore(bundle)
                if change == "edit":
                    self.file.write_bytes(b"user edits after import")
                else:
                    if change == "replace":
                        # Holding the old inode keeps ext4 from handing its number to the new file.
                        held = self.root / "held"
                        held.unlink(missing_ok=True)
                        os.link(self.file, held)
                    self.file.unlink()
                    if change == "replace":
                        self.file.write_bytes(NEW)
                        self.file.chmod(0o600)
                        os.utime(self.file, ns=(MTIME, MTIME))
                before = self.file.read_bytes() if self.file.exists() else None
                result = self.run_restore(bundle)[FILE]
                self.assertEqual(result.status, "conflict")
                self.assertEqual(result.backup, self.saved()["backup"]["name"])
                self.assertEqual(self.file.read_bytes() if self.file.exists() else None, before)
                self.assert_backup(self.saved())

    def test_damaged_or_missing_backup_prevents_completed_recovery(self):
        with self.verified() as bundle:
            for damage in ("edit", "delete", "chmod", "symlink", "hardlink"):
                self.destination(damage)
                self.run_restore(bundle)
                backup = self.assert_backup(self.saved())
                if damage == "edit":
                    backup.write_bytes(b"damaged backup")
                elif damage == "chmod":
                    backup.chmod(0o644)
                elif damage == "hardlink":
                    os.link(backup, self.root / "backup-alias")
                else:
                    backup.unlink()
                    if damage == "symlink":
                        backup.symlink_to(self.file)
                inode = self.file.stat().st_ino
                result = self.run_restore(bundle)[FILE]
                self.assertEqual(result.status, "conflict")
                self.assertEqual(result.backup, self.saved()["backup"]["name"])
                self.assertEqual(self.file.stat().st_ino, inode)
                self.assertEqual(self.file.read_bytes(), NEW)

    def test_backup_change_after_plan_prevents_reporting_success(self):
        with self.verified() as bundle:
            self.run_restore(bundle)
            with restore.Restorer(bundle, self.target, self.job, replace=(FILE,)) as importer:
                plan = importer.plan()
                self.assert_backup(self.saved()).unlink()
                report = {action.path: action for action in importer.apply(plan)}
                self.assertEqual(report[FILE].status, "conflict")
                self.assertEqual(report[FILE].backup, self.saved()["backup"]["name"])
        self.assertEqual(self.file.read_bytes(), NEW)

    def test_malformed_backup_records_are_rejected_without_destination_mutation(self):
        with self.verified() as bundle:
            self.run_restore(bundle)
            path = self.job / "journal.json"
            baseline = restore.read_journal(path)
            identity = next(key for key, entry in baseline["entries"].items() if "backup" in entry)
            for damage in ("path", "unknown", "digest", "mode", "alias", "approval"):
                changed = copy.deepcopy(baseline)
                saved = changed["entries"][identity]
                if damage == "path":
                    saved["backup"]["name"] = "../outside"
                elif damage == "unknown":
                    saved["backup"]["extra"] = True
                elif damage == "digest":
                    saved["backup"]["file"]["sha256"] = "x" * 64
                elif damage == "mode":
                    saved["backup"]["file"]["mode"] = 0o644
                elif damage == "alias":
                    saved["backup"]["file"]["inode"] = saved["file"]["inode"]
                else:
                    changed["replacement_paths"] = []
                path.write_text(json.dumps(changed))
                with self.subTest(damage=damage), self.assertRaises(probe.Rejected):
                    restore.Restorer(bundle, self.target, self.job, replace=(FILE,))
                self.assertEqual(self.file.read_bytes(), NEW)


if __name__ == "__main__":
    unittest.main()
