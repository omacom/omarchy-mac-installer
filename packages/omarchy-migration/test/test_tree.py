"""Mixed synthetic trees: typed manifests, safe links and conservative recovery."""

import copy
import io
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import uuid

from omarchy_migration import probe, restore
from omarchy_migration.dependency import configured_age


SECRET = b"synthetic-only-otter-maple-window-cobalt"
MTIME = 1720000000123456789


def synthetic_paths(root):
    """Only test-generated roots: include directory links without following them."""
    paths = {}
    for parent, directories, files in os.walk(root, followlinks=False):
        for name in directories + files:
            path = Path(parent) / name
            paths[path.relative_to(root).as_posix()] = path
    return paths


def tree_document(entries):
    result = []
    for index, entry in enumerate(entries):
        result.append({"object": f"objects/{index:08d}", "mtime_ns": MTIME, **entry})
    return {"schema": probe.TREE_SCHEMA, "export_id": str(uuid.uuid4()), "entries": result}


class TreeManifestTests(unittest.TestCase):
    def test_explicit_ancestors_must_be_directories(self):
        child = {"path": "a/b", "kind": "directory", "mode": 0o700}
        for parent in (None, {"path": "a", "kind": "symlink", "target": "elsewhere"},
                       {"path": "a", "kind": "file", "mode": 0o600, "bytes": 0, "sha256": "0" * 64}):
            with self.subTest(parent=parent):
                document = tree_document([child] if parent is None else [parent, child])
                with self.assertRaisesRegex(probe.Rejected, "ancestor"):
                    probe.validate_manifest(document)

    def test_malformed_typed_entries_are_rejected(self):
        valid = tree_document([{"path": "link", "kind": "symlink", "target": "/inert"}])
        for key, value in (("kind", "socket"), ("target", ""), ("target", "x\0y"),
                           ("target", "x" * 4097), ("target", 123), ("mode", 0o777),
                           ("mtime_ns", True), ("path", "x" * 256), ("object", "../escape")):
            malformed = copy.deepcopy(valid)
            malformed["entries"][0][key] = value
            with self.subTest(key=key, value=str(value)[:30]), self.assertRaises(probe.Rejected):
                probe.validate_manifest(malformed)

    def test_component_resolution_does_not_collapse_a_symlink_before_dotdot(self):
        document = tree_document([
            {"path": "d", "kind": "directory", "mode": 0o700},
            {"path": "d/empty", "kind": "directory", "mode": 0o700},
            {"path": "d/file", "kind": "file", "mode": 0o600, "bytes": 0, "sha256": "0" * 64},
            {"path": "d/alias", "kind": "symlink", "target": "empty"},
            {"path": "safe", "kind": "symlink", "target": "d/empty/../file"},
            {"path": "unsafe", "kind": "symlink", "target": "d/alias/../file"},
        ])
        probe.validate_manifest(document)
        entries = {entry["path"]: entry for entry in document["entries"]}
        self.assertEqual(probe.link_target(entries["safe"], entries), ("d/file", ("d", "d/empty")))
        self.assertIsNone(probe.link_target(entries["unsafe"], entries))

    def test_archive_links_are_still_rejected_for_v2(self):
        document = tree_document([{"path": "link", "kind": "symlink", "target": "/inert"}])
        for kind in (tarfile.SYMTYPE, tarfile.DIRTYPE, tarfile.LNKTYPE):
            stream = io.BytesIO()
            with tarfile.open(fileobj=stream, mode="w", format=tarfile.USTAR_FORMAT) as archive:
                raw = json.dumps(document).encode()
                header = tarfile.TarInfo("manifest.json")
                header.size = len(raw)
                archive.addfile(header, io.BytesIO(raw))
                illicit = tarfile.TarInfo("objects/00000000")
                illicit.type, illicit.linkname = kind, "../outside"
                archive.addfile(illicit)
            stream.seek(0)
            with self.subTest(kind=kind), self.assertRaises(probe.Rejected):
                probe.validate_archive(stream)

    def test_source_special_files_are_refused_without_reading_them(self):
        with tempfile.TemporaryDirectory() as directory:
            fifo = Path(directory) / "pipe"
            os.mkfifo(fifo)
            with self.assertRaisesRegex(probe.Rejected, "source entry type"):
                probe.make_tree_manifest({"pipe": fifo})


class TreeRestoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.age = configured_age()
        if cls.age is None:
            raise unittest.SkipTest("set OMARCHY_TEST_AGE and OMARCHY_TEST_AGE_SHA256")
        if os.geteuid() == 0:
            raise unittest.SkipTest("restoration experiment requires an unprivileged owner")
        cls.source = tempfile.TemporaryDirectory(prefix="migration-tree-source-")
        cls.addClassCleanup(cls.source.cleanup)
        cls.source_root = Path(cls.source.name)
        cls.tree = cls.source_root / "tree"
        cls.tree.mkdir(mode=0o700)
        for directory in (".config/example", "Projects/demo/empty", "Shortcuts"):
            (cls.tree / directory).mkdir(parents=True)
        for name, contents in {
            ".config/example/options": b"unfamiliar personal setting\n",
            "Projects/demo/readme.txt": b"synthetic project\n",
            "Projects/demo/run": b"#!/bin/bash\ntouch SHOULD-NOT-RUN\n",
        }.items():
            path = cls.tree / name
            path.write_bytes(contents)
            path.chmod(0o750 if name.endswith("/run") else 0o640)
        cls.safe_links = {
            "Shortcuts/readme": "../Projects/demo/readme.txt",
            "Shortcuts/project": "../Projects/demo",
            "Shortcuts/normalized": "../Projects/demo/empty/../readme.txt",
        }
        cls.inert_links = {
            "Shortcuts/absolute": "/not-selected/example",
            "Shortcuts/escape": "../../outside",
            "Shortcuts/missing": "../not-selected",
            "Shortcuts/chain": "readme",
            "Shortcuts/cycle-a": "cycle-b",
            "Shortcuts/cycle-b": "cycle-a",
            "Shortcuts/through-link": "project/readme.txt",
            "Shortcuts/link-dotdot": "project/../readme",
        }
        for name, target in {**cls.safe_links, **cls.inert_links}.items():
            (cls.tree / name).symlink_to(target)
        cls.paths = synthetic_paths(cls.tree)
        for path in cls.paths.values():
            os.utime(path, ns=(MTIME, MTIME), follow_symlinks=False)
        cls.manifest = probe.make_tree_manifest(cls.paths)
        cls.ciphertext = cls.source_root / "tree.age"
        probe.encrypt(cls.age, SECRET, cls.ciphertext,
                      lambda stream: probe.write_archive(stream, cls.manifest, cls.paths))

    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix="migration-tree-test-")
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.target, self.job = self.root / "destination", self.root / "job"
        self.target.mkdir(mode=0o700)
        self.job.mkdir(mode=0o700)

    def verified(self, ciphertext=None):
        return restore.verified_bundle(self.age, SECRET, ciphertext or self.ciphertext)

    def run_restore(self, bundle):
        with restore.Restorer(bundle, self.target, self.job) as importer:
            return {action.path: action for action in importer.apply(importer.plan())}

    def test_mixed_tree_preserves_files_empty_directories_and_safe_link_text(self):
        self.assertEqual(probe.decode(self.age, SECRET, self.ciphertext), self.manifest)
        with self.verified() as bundle:
            self.assertEqual(len(list(bundle._directory.iterdir())), 3)
            report = self.run_restore(bundle)
        self.assertEqual(set(report), set(self.paths))
        for entry in self.manifest["entries"]:
            destination = self.target / entry["path"]
            if entry["kind"] == "file":
                self.assertEqual(destination.read_bytes(), self.paths[entry["path"]].read_bytes())
                self.assertEqual(stat.S_IMODE(destination.stat().st_mode), entry["mode"])
                self.assertEqual(destination.stat().st_mtime_ns, entry["mtime_ns"])
            elif entry["kind"] == "directory":
                self.assertTrue(destination.is_dir())
                self.assertEqual(stat.S_IMODE(destination.stat().st_mode), entry["mode"] & ~0o022)
                self.assertEqual(destination.stat().st_mtime_ns, entry["mtime_ns"])
                self.assertEqual(report[entry["path"]].status, "directory")
                self.assertEqual(report[entry["path"]].reason, "directory metadata restored")
        self.assertEqual(list((self.target / "Projects/demo/empty").iterdir()), [])
        for name, target in self.safe_links.items():
            path = self.target / name
            self.assertEqual(os.readlink(path), target)
            self.assertEqual(path.lstat().st_mtime_ns, MTIME)
            self.assertEqual(path.lstat().st_nlink, 1)
            self.assertEqual(report[name].status, "restored")
        for name in self.inert_links:
            self.assertEqual(report[name].status, "inert")
            self.assertFalse(os.path.lexists(self.target / name))
        self.assertEqual((self.target / "Shortcuts/readme").read_bytes(), b"synthetic project\n")
        self.assertFalse((self.target / "SHOULD-NOT-RUN").exists())
        self.assertEqual([path.name for path in self.job.iterdir()], ["journal.json"])

    def test_final_authentication_failure_creates_no_target_directories_or_links(self):
        corrupted = bytearray(self.ciphertext.read_bytes())
        corrupted[-1] ^= 1
        path = self.root / "corrupt.age"
        path.write_bytes(corrupted)
        with self.assertRaises(probe.Rejected):
            with self.verified(path) as bundle:
                self.run_restore(bundle)
        self.assertEqual(list(self.target.iterdir()), [])
        self.assertEqual(list(self.job.iterdir()), [])

    def test_existing_directory_modes_are_retained_and_later_changes_block_children(self):
        directory = self.target / "Projects"
        directory.mkdir(mode=0o750)
        with self.verified() as bundle:
            report = self.run_restore(bundle)
            self.assertEqual(report["Projects"].status, "directory")
            self.assertIn("metadata deferred", report["Projects"].reason)
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o750)
            self.assertNotEqual(directory.stat().st_mtime_ns, MTIME)
            directory.chmod(0o700)
            report = self.run_restore(bundle)
        self.assertEqual(report["Projects"].status, "conflict")
        self.assertEqual(report["Projects/demo/run"].status, "conflict")
        self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)

    def custom_bundle(self, build, finish=lambda root: None, modes=None):
        """Encrypt a test-built tree; finish() runs after mtimes, e.g. to drop write bits."""
        source = self.root / "custom-source"
        source.mkdir(mode=0o700)
        build(source)
        paths = synthetic_paths(source)
        for path in paths.values():
            os.utime(path, ns=(MTIME, MTIME), follow_symlinks=False)
        finish(source)
        self.addCleanup(lambda: [path.chmod(0o700) for path in source.rglob("*") if path.is_dir() and not path.is_symlink()])
        manifest = probe.make_tree_manifest(paths)
        for entry in manifest["entries"]:
            # A crafted producer may claim modes collection never emits.
            if modes and entry["path"] in modes:
                entry["mode"] = modes[entry["path"]]
        probe.validate_manifest(manifest)
        ciphertext = self.root / "custom.age"
        probe.encrypt(self.age, SECRET, ciphertext, lambda stream: probe.write_archive(stream, manifest, paths))
        return ciphertext, manifest

    def release_target(self):
        for path in [self.target, *self.target.rglob("*")]:
            if path.is_dir() and not path.is_symlink():
                path.chmod(0o700)

    def test_created_directory_waits_for_conflicting_child_then_finalizes_on_retry(self):
        def build(root):
            (root / "Notes").mkdir(mode=0o750)
            (root / "Notes/a.txt").write_bytes(b"a\n")
            (root / "Notes/b.txt").write_bytes(b"b\n")
        ciphertext, _ = self.custom_bundle(build)
        self.addCleanup(self.release_target)
        notes = self.target / "Notes"
        original = restore.Restorer._restore_directory

        def then_user_writes_b(importer, entry, action, observed):
            result = original(importer, entry, action, observed)
            if entry["path"] == "Notes":
                (notes / "b.txt").write_bytes(b"written by the user meanwhile\n")
            return result

        with self.verified(ciphertext) as bundle:
            with restore.Restorer(bundle, self.target, self.job) as importer:
                plan = importer.plan()
                with patch.object(restore.Restorer, "_restore_directory", then_user_writes_b):
                    report = {action.path: action for action in importer.apply(plan)}
            self.assertEqual(report["Notes/b.txt"].status, "conflict")
            self.assertEqual(report["Notes/a.txt"].status, "restored")
            self.assertIn("metadata deferred", report["Notes"].reason)
            self.assertEqual(stat.S_IMODE(notes.stat().st_mode), 0o700)
            (notes / "b.txt").unlink()
            with restore.Restorer(bundle, self.target, self.job) as importer:
                plan = importer.plan()
                planned = {action.path: action.status for action in plan}
                report = {action.path: action for action in importer.apply(plan)}
        self.assertEqual((planned["Notes"], planned["Notes/b.txt"]), ("restored", "create"))
        self.assertEqual((notes / "b.txt").read_bytes(), b"b\n")
        self.assertEqual(report["Notes"].reason, "directory metadata restored")
        self.assertEqual((stat.S_IMODE(notes.stat().st_mode), notes.stat().st_mtime_ns), (0o750, MTIME))

    def test_read_only_source_directory_keeps_its_contents_and_retries_cleanly(self):
        def build(root):
            (root / "Archive").mkdir()
            (root / "Archive/old.txt").write_bytes(b"kept\n")
        ciphertext, manifest = self.custom_bundle(build, lambda root: (root / "Archive").chmod(0o555))
        self.assertEqual(next(e for e in manifest["entries"] if e["path"] == "Archive")["mode"], 0o555)
        self.addCleanup(self.release_target)
        with self.verified(ciphertext) as bundle:
            first = self.run_restore(bundle)
            second = self.run_restore(bundle)
        archive = self.target / "Archive"
        self.assertEqual((archive / "old.txt").read_bytes(), b"kept\n")
        self.assertEqual((stat.S_IMODE(archive.stat().st_mode), archive.stat().st_mtime_ns), (0o555, MTIME))
        self.assertEqual(first["Archive"].reason, "directory metadata restored")
        self.assertNotIn("conflict", {action.status for action in second.values()})

    def test_crash_during_finalization_recovers_on_retry(self):
        def build(root):
            (root / "Pictures").mkdir(mode=0o750)
            (root / "Pictures/a.png").write_bytes(b"png\n")
        ciphertext, _ = self.custom_bundle(build)
        self.addCleanup(self.release_target)
        with self.verified(ciphertext) as bundle:
            original_utime = os.utime

            def fail_for_directories(target, *args, **kwargs):
                # Only finalization touches directory times; file restores proceed.
                if isinstance(target, int) and stat.S_ISDIR(os.fstat(target).st_mode):
                    raise OSError("power lost")
                return original_utime(target, *args, **kwargs)

            with restore.Restorer(bundle, self.target, self.job) as importer:
                plan = importer.plan()
                with patch.object(restore.os, "utime", side_effect=fail_for_directories):
                    with self.assertRaises(OSError):
                        importer.apply(plan)
            self.assertEqual((self.target / "Pictures/a.png").read_bytes(), b"png\n")
            pictures = self.target / "Pictures"
            self.assertEqual(stat.S_IMODE(pictures.stat().st_mode), 0o750)  # mode set, journal not updated
            journal = restore.read_journal(self.job / "journal.json")
            identity = next(entry["object"] for entry in bundle._manifest["entries"] if entry["path"] == "Pictures")
            self.assertEqual(journal["entries"][identity]["file"]["mode"], 0o700)
            with restore.Restorer(bundle, self.target, self.job) as importer:
                plan = importer.plan()
                self.assertEqual({action.path: action.status for action in plan}["Pictures"], "restored")
                report = {action.path: action for action in importer.apply(plan)}
        self.assertEqual(report["Pictures"].reason, "directory metadata restored")
        self.assertEqual(pictures.stat().st_mtime_ns, MTIME)

    def test_directory_mode_without_owner_access_stays_traversable(self):
        def build(root):
            (root / "Locked").mkdir()
            (root / "Locked/inner.txt").write_bytes(b"inner\n")
        ciphertext, _ = self.custom_bundle(build, modes={"Locked": 0o300})
        self.addCleanup(self.release_target)
        with self.verified(ciphertext) as bundle:
            first = self.run_restore(bundle)
            second = self.run_restore(bundle)
        locked = self.target / "Locked"
        self.assertEqual(stat.S_IMODE(locked.stat().st_mode), 0o700)
        self.assertEqual(first["Locked"].reason, "directory metadata restored")
        self.assertEqual({action.status for action in second.values()} - {"restored", "directory", "present"}, set())
        self.assertEqual((locked / "inner.txt").read_bytes(), b"inner\n")

    def test_failed_child_finalization_keeps_its_parent_private(self):
        def build(root):
            (root / "Archive/Inner").mkdir(parents=True)
            (root / "Archive/Inner/x").write_bytes(b"x\n")
            (root / "Archive").chmod(0o555)
        ciphertext, _ = self.custom_bundle(build)
        self.addCleanup(self.release_target)
        original = restore.Restorer._finalize_directory

        def fail_inner(importer, entry, saved):
            return None if entry["path"] == "Archive/Inner" else original(importer, entry, saved)

        with self.verified(ciphertext) as bundle, patch.object(restore.Restorer, "_finalize_directory", fail_inner):
            report = self.run_restore(bundle)
        self.assertEqual(report["Archive/Inner"].status, "conflict")
        self.assertIn("metadata deferred", report["Archive"].reason)
        self.assertEqual(stat.S_IMODE((self.target / "Archive").stat().st_mode), 0o700)

    def test_group_writable_source_directory_is_restored_without_group_write(self):
        def build(root):
            (root / "Shared").mkdir()
            (root / "Shared/x").write_bytes(b"x\n")
        ciphertext, manifest = self.custom_bundle(build, lambda root: (root / "Shared").chmod(0o775))
        self.assertEqual(next(e for e in manifest["entries"] if e["path"] == "Shared")["mode"], 0o775)
        with self.verified(ciphertext) as bundle:
            self.run_restore(bundle)
        self.assertEqual(stat.S_IMODE((self.target / "Shared").stat().st_mode), 0o755)

    def test_destination_symlink_cannot_turn_a_valid_manifest_link_into_an_escape(self):
        outside = self.root / "outside"
        outside.mkdir()
        sentinel = outside / "keep"
        sentinel.write_bytes(b"outside unchanged")
        (self.target / "Projects").symlink_to(outside, target_is_directory=True)
        with self.verified() as bundle:
            report = self.run_restore(bundle)
        self.assertEqual(report["Projects"].status, "conflict")
        for path in self.safe_links:
            self.assertEqual(report[path].status, "conflict")
            self.assertFalse(os.path.lexists(self.target / path))
        self.assertEqual([path.name for path in outside.iterdir()], ["keep"])
        self.assertEqual(sentinel.read_bytes(), b"outside unchanged")

    def test_conflicting_target_file_prevents_dependent_links(self):
        destination = self.target / "Projects/demo/readme.txt"
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b"native user version")
        with self.verified() as bundle:
            report = self.run_restore(bundle)
        for name in ("Shortcuts/readme", "Shortcuts/normalized"):
            self.assertEqual(report[name].status, "conflict")
            self.assertFalse(os.path.lexists(self.target / name))
        self.assertEqual(destination.read_bytes(), b"native user version")

    def test_link_dependencies_are_rechecked_after_regular_files_are_published(self):
        root_mode = stat.S_IMODE(self.root.stat().st_mode)
        link = os.link
        changed = False

        def change_intervening_directory(source, destination, **kwargs):
            nonlocal changed
            link(source, destination, **kwargs)
            if not changed and destination == "readme.txt":
                changed = True
                empty = self.target / "Projects/demo/empty"
                empty.rename(self.root / "moved-empty")
                empty.symlink_to(self.root, target_is_directory=True)

        with self.verified() as bundle, patch.object(restore.os, "link", side_effect=change_intervening_directory):
            report = self.run_restore(bundle)
        self.assertEqual(report["Shortcuts/normalized"].status, "conflict")
        self.assertFalse(os.path.lexists(self.target / "Shortcuts/normalized"))
        self.assertEqual(report["Shortcuts/readme"].status, "restored")
        # The swapped directory is reported, not finalized through the link.
        self.assertEqual(report["Projects/demo/empty"].status, "conflict")
        self.assertNotEqual(self.root.stat().st_mtime_ns, MTIME)
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), root_mode)
        self.assertEqual(stat.S_IMODE((self.root / "moved-empty").stat().st_mode), 0o700)

    def test_existing_target_leaf_symlink_is_not_followed_or_aliased(self):
        outside = self.root / "outside"
        outside.write_bytes(b"outside work")
        destination = self.target / "Projects/demo/readme.txt"
        destination.parent.mkdir(parents=True)
        destination.symlink_to(outside)
        with self.verified() as bundle:
            report = self.run_restore(bundle)
        self.assertEqual(report["Projects/demo/readme.txt"].status, "conflict")
        self.assertEqual(report["Shortcuts/readme"].status, "conflict")
        self.assertFalse(os.path.lexists(self.target / "Shortcuts/readme"))
        self.assertEqual(outside.read_bytes(), b"outside work")

    def test_removed_directory_is_not_recreated_and_descendants_stay_blocked(self):
        with self.verified() as bundle:
            self.run_restore(bundle)
            old = self.root / "moved-projects"
            (self.target / "Projects").rename(old)
            report = self.run_restore(bundle)
        self.assertEqual(report["Projects"].status, "conflict")
        self.assertFalse((self.target / "Projects").exists())
        self.assertEqual(report["Projects/demo/run"].status, "conflict")
        self.assertEqual((old / "demo/readme.txt").read_bytes(), b"synthetic project\n")

    def test_uncertain_mkdir_is_never_adopted_or_recreated(self):
        mkdir = os.mkdir
        for after_creation in (False, True):
            with self.subTest(after_creation=after_creation):
                self.target, self.job = self.root / f"target-{after_creation}", self.root / f"job-{after_creation}"
                self.target.mkdir(mode=0o700)
                self.job.mkdir(mode=0o700)

                def interrupt(*args, **kwargs):
                    if after_creation:
                        mkdir(*args, **kwargs)
                    raise RuntimeError("synthetic mkdir interruption")

                with self.verified() as bundle:
                    with restore.Restorer(bundle, self.target, self.job) as importer:
                        plan = importer.plan()
                        with patch.object(restore.os, "mkdir", side_effect=interrupt):
                            with self.assertRaisesRegex(RuntimeError, "mkdir interruption"):
                                importer.apply(plan)
                    report = self.run_restore(bundle)
                self.assertEqual(report[".config"].status, "conflict")
                self.assertEqual(report[".config/example/options"].status, "conflict")
                self.assertFalse((self.target / ".config/example").exists())
                self.assertEqual((self.target / ".config").exists(), after_creation)
                self.assertEqual(report["Projects/demo/run"].status, "restored")

    def test_retained_matching_file_deletion_is_not_resurrected(self):
        name = "Projects/demo/readme.txt"
        path = self.target / name
        path.parent.mkdir(parents=True)
        path.write_bytes(self.paths[name].read_bytes())
        path.chmod(0o640)
        os.utime(path, ns=(MTIME, MTIME))
        with self.verified() as bundle:
            self.assertEqual(self.run_restore(bundle)[name].status, "present")
            path.unlink()
            report = self.run_restore(bundle)
        self.assertEqual(report[name].status, "conflict")
        self.assertFalse(path.exists())

    def test_directory_appearing_during_mkdir_is_preserved_without_adopting_children(self):
        mkdir = os.mkdir
        first = True

        def race(name, *args, **kwargs):
            nonlocal first
            if first:
                first = False
                mkdir(name, *args, **kwargs)
                (self.target / name / "keep").write_bytes(b"new user directory")
            return mkdir(name, *args, **kwargs)

        with self.verified() as bundle:
            with restore.Restorer(bundle, self.target, self.job) as importer:
                plan = importer.plan()
                with patch.object(restore.os, "mkdir", side_effect=race):
                    report = {action.path: action for action in importer.apply(plan)}
        self.assertEqual(report[".config"].status, "conflict")
        self.assertEqual(report[".config/example/options"].status, "conflict")
        self.assertEqual((self.target / ".config/keep").read_bytes(), b"new user directory")
        self.assertFalse((self.target / ".config/example").exists())

    def test_deleted_or_replaced_symlinks_are_not_repaired_on_retry(self):
        with self.verified() as bundle:
            self.run_restore(bundle)
            deleted = self.target / "Shortcuts/readme"
            changed = self.target / "Shortcuts/project"
            deleted.unlink()
            changed.unlink()
            changed.symlink_to("/not-selected/new-target")
            report = self.run_restore(bundle)
        self.assertEqual(report["Shortcuts/readme"].status, "conflict")
        self.assertEqual(report["Shortcuts/project"].status, "conflict")
        self.assertFalse(os.path.lexists(deleted))
        self.assertEqual(os.readlink(changed), "/not-selected/new-target")

    def test_fresh_session_recovers_after_actual_sigkill_at_symlink_publication(self):
        child_scratch = self.root / "child-scratch"
        child_scratch.mkdir(mode=0o700)
        script = """
import os, signal, stat, sys
from pathlib import Path
from unittest.mock import patch
from omarchy_migration import restore
from omarchy_migration.dependency import configured_age
link = os.link
def crash_on_symlink(source, destination, **kwargs):
    link(source, destination, **kwargs)
    if stat.S_ISLNK(os.stat(source, dir_fd=kwargs['src_dir_fd'], follow_symlinks=False).st_mode):
        os.kill(os.getpid(), signal.SIGKILL)
with restore.verified_bundle(configured_age(), b'synthetic-only-otter-maple-window-cobalt', Path(sys.argv[1])) as bundle:
    with restore.Restorer(bundle, Path(sys.argv[2]), Path(sys.argv[3])) as importer:
        plan = importer.plan()
        with patch.object(restore.os, 'link', side_effect=crash_on_symlink):
            importer.apply(plan)
"""
        result = subprocess.run([sys.executable, "-c", script, str(self.ciphertext), str(self.target), str(self.job)],
                                env={**os.environ, "TMPDIR": str(child_scratch)}, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, -signal.SIGKILL, result.stderr.decode())
        path = self.target / "Shortcuts/normalized"
        inode = path.lstat().st_ino
        with self.verified() as bundle:
            report = self.run_restore(bundle)
        self.assertEqual(path.lstat().st_ino, inode)
        self.assertEqual(path.lstat().st_nlink, 1)
        self.assertEqual(report["Shortcuts/normalized"].status, "restored")
        self.assertEqual(report["Shortcuts/readme"].status, "restored")

    def test_regular_source_replaced_with_symlink_is_not_followed_during_archive_write(self):
        root = self.root / "capture"
        root.mkdir()
        source = root / "file"
        source.write_bytes(b"original")
        paths = {"file": source}
        manifest = probe.make_tree_manifest(paths)
        outside = self.root / "outside-data"
        outside.write_bytes(b"must not enter archive")
        source.unlink()
        source.symlink_to(outside)
        with self.assertRaises(OSError):
            probe.write_archive(io.BytesIO(), manifest, paths)

    def test_git_roundtrip_preserves_work_and_ignores_inherited_repository_settings(self):
        workspace = self.root / "git-source"
        project = workspace / "project"
        workspace.mkdir()
        unrelated = self.root / "unrelated-repository"
        unrelated.mkdir()
        index = unrelated / "index"
        index.write_bytes(b"unrelated index sentinel")
        with patch.dict(os.environ, {
            "GIT_DIR": str(unrelated / "git"), "GIT_WORK_TREE": str(unrelated),
            "GIT_INDEX_FILE": str(index), "GIT_COMMON_DIR": str(unrelated / "common"),
            "GIT_OBJECT_DIRECTORY": str(unrelated / "objects"),
        }):
            environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null")
        command = ["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false",
                   "-c", "core.attributesFile=/dev/null", "-c", "core.excludesFile=/dev/null",
                   "-c", "core.fsmonitor=false",
                   "-c", "user.name=Synthetic Fixture", "-c", "user.email=fixture@example.invalid"]

        def git(*arguments, cwd=project):
            return subprocess.run(command + list(arguments), cwd=cwd, env=environment,
                                  check=True, capture_output=True, text=True).stdout

        templates = self.root / "empty-templates"
        templates.mkdir()
        git("init", "--initial-branch=main", f"--template={templates}", str(project), cwd=workspace)
        (project / "tracked.txt").write_text("committed version\n")
        git("add", "tracked.txt")
        git("commit", "-m", "Synthetic base")
        expected_head = git("rev-parse", "HEAD")
        (project / "tracked.txt").write_text("uncommitted work\n")
        (project / "untracked.txt").write_text("untracked work\n")
        expected_status = git("status", "--porcelain")
        paths = synthetic_paths(workspace)
        manifest = probe.make_tree_manifest(paths)
        ciphertext = self.root / "git.age"
        probe.encrypt(self.age, SECRET, ciphertext, lambda stream: probe.write_archive(stream, manifest, paths))
        with self.verified(ciphertext) as bundle:
            report = self.run_restore(bundle)
        self.assertNotIn("conflict", {action.status for action in report.values()})
        restored = self.target / "project"
        self.assertEqual(git("rev-parse", "HEAD", cwd=restored), expected_head)
        self.assertEqual(git("status", "--porcelain", cwd=restored), expected_status)
        self.assertEqual((restored / "tracked.txt").read_text(), "uncommitted work\n")
        self.assertEqual((restored / "untracked.txt").read_text(), "untracked work\n")
        self.assertEqual([path.name for path in unrelated.iterdir()], ["index"])
        self.assertEqual(index.read_bytes(), b"unrelated index sentinel")


if __name__ == "__main__":
    unittest.main()
