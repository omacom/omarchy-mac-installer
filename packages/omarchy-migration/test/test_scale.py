"""Limits come from real sizes and free space, and the journal scales with the import."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from omarchy_migration import collection, contract, policy, probe, restore
from omarchy_migration.dependency import configured_age

SECRET = b"synthetic-only-otter-maple-window-cobalt"
POLICY = json.loads(policy.SHIPPED.read_text())
DIRECTORIES, FILES_PER_DIRECTORY = 40, 110


class LimitTests(unittest.TestCase):
    def test_ceilings_fit_real_homes(self):
        self.assertGreaterEqual(contract.MAX_ENTRIES, 200_000)
        self.assertGreaterEqual(contract.MAX_EXPANDED, 256 * 1024 ** 3)
        self.assertEqual(probe.MAX_ENTRIES, contract.MAX_ENTRIES)
        self.assertGreater(contract.MAX_CIPHERTEXT, contract.MAX_EXPANDED + contract.MAX_MANIFEST)

    def test_receipt_and_plan_counts_accept_large_exports(self):
        receipt = json.loads((Path(__file__).resolve().parent / "fixtures/valid/receipt.json").read_text())
        receipt["estimates"] = {"expanded_bytes": 40 * 1024 ** 3, "entries": 150_000}
        receipt["bundle"]["bytes"] = 41 * 1024 ** 3
        contract.validate(receipt)


class ScaleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.age = configured_age()
        if cls.age is None:
            raise unittest.SkipTest("set OMARCHY_TEST_AGE and OMARCHY_TEST_AGE_SHA256 for real age fixtures")
        if os.geteuid() == 0:
            raise unittest.SkipTest("unprivileged fixture")

    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix="migration-scale-")
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.home, self.parent = self.root / "home", self.root / "snapshots"
        self.target, self.job = self.root / "target", self.root / "job"
        for directory in (self.home, self.parent, self.target, self.job):
            directory.mkdir(mode=0o700)

    def export(self, home):
        request = {"schema": collection.REQUEST_SCHEMA, "request_id": str(uuid.uuid4()),
                   "selection": [{"source": "", "archive": ""}], "selected_adapters": [],
                   "selected_mounts": [], "selected_share_stores": []}
        ciphertext = self.root / "bundle.age"
        with collection.collect_fixture(home, request, POLICY, snapshot_parent=self.parent) as snapshot:
            manifest = snapshot.manifest
            probe.encrypt(self.age, SECRET, ciphertext, lambda stream: probe.write_archive(stream, manifest, snapshot.paths))
        return manifest, ciphertext

    def test_thousands_of_entries_restore_with_a_linear_journal(self):
        for directory in range(DIRECTORIES):
            folder = self.home / "Projects" / f"p{directory:02d}"
            folder.mkdir(parents=True, mode=0o700)
            for index in range(FILES_PER_DIRECTORY):
                (folder / f"f{index:03d}.txt").write_bytes(f"{directory}-{index}\n".encode())
        manifest, ciphertext = self.export(self.home)
        expected = DIRECTORIES * FILES_PER_DIRECTORY + DIRECTORIES + 1
        self.assertEqual(len(manifest["entries"]), expected)
        self.assertGreater(expected, 4 * 1024)
        serialized = 0
        original = restore._json_bytes

        def counted(value):
            nonlocal serialized
            serialized += 1
            return original(value)

        with restore.verified_bundle(self.age, SECRET, ciphertext) as bundle:
            with restore.Restorer(bundle, self.target, self.job) as importer:
                with patch.object(restore, "_json_bytes", side_effect=counted):
                    results = importer.apply(importer.plan())
        self.assertEqual({result.status for result in results}, {"restored", "directory"})
        # Each save serializes only what it changed: linear, not quadratic.
        self.assertLess(serialized, 10 * expected)
        self.assertEqual((self.target / "Projects/p39/f109.txt").read_bytes(), b"39-109\n")
        journal = self.job / "journal.json"
        # Appends, not rewrites: the log stays proportional to the import.
        self.assertGreater(len(journal.read_bytes().splitlines()), 1)
        self.assertLess(journal.stat().st_size, 2048 * expected)
        self.assertEqual(len(restore.read_journal(journal)["entries"]), expected)

    def small_bundle(self):
        (self.home / "notes.txt").write_bytes(b"note\n")
        (self.home / "more.txt").write_bytes(b"more\n")
        return self.export(self.home)

    def test_torn_final_append_is_ignored_and_a_corrupt_line_is_rejected(self):
        _, ciphertext = self.small_bundle()
        with restore.verified_bundle(self.age, SECRET, ciphertext) as bundle:
            with restore.Restorer(bundle, self.target, self.job) as importer:
                importer.apply(importer.plan())
            journal = self.job / "journal.json"
            intact = journal.read_bytes()
            with journal.open("ab") as output:
                output.write(b'{"entry":{"state":"pend')  # interrupted before its newline
            with restore.Restorer(bundle, self.target, self.job) as importer:
                self.assertNotIn("conflict", {action.status for action in importer.plan()})
            # Reopening removed the torn tail, so later appends stay well formed.
            self.assertTrue(journal.read_bytes().endswith(b"\n"))
            self.assertEqual(len(journal.read_bytes().splitlines()), 1)
            journal.write_bytes(intact + b'{"entry":null,"object":"objects/00000001","extra":1}\n')
            with self.assertRaisesRegex(probe.Rejected, "invalid restore journal"):
                restore.Restorer(bundle, self.target, self.job)

    def test_overgrown_log_is_compacted_when_the_job_reopens(self):
        _, ciphertext = self.small_bundle()
        with restore.verified_bundle(self.age, SECRET, ciphertext) as bundle:
            with restore.Restorer(bundle, self.target, self.job) as importer:
                importer.apply(importer.plan())
            journal = self.job / "journal.json"
            document = restore.read_journal(journal)
            key, entry = next(iter(document["entries"].items()))
            line = json.dumps({"entry": entry, "object": key}, sort_keys=True, separators=(",", ":")).encode() + b"\n"
            journal.write_bytes(journal.read_bytes() + line * 100)
            with restore.Restorer(bundle, self.target, self.job):
                pass
            self.assertEqual(len(journal.read_bytes().splitlines()), 1)
            self.assertEqual(restore.read_journal(journal), document)

    def test_decoding_refuses_a_bundle_larger_than_free_scratch_space(self):
        _, ciphertext = self.small_bundle()
        with patch.object(restore, "_free_bytes", return_value=restore.SCRATCH_MARGIN + 3):
            with self.assertRaisesRegex(probe.Rejected, "available space"):
                with restore.verified_bundle(self.age, SECRET, ciphertext):
                    self.fail("oversized bundle authenticated into scratch")

    def test_collection_refuses_a_snapshot_larger_than_free_space(self):
        (self.home / "big.txt").write_bytes(b"x" * 4096)
        real = os.statvfs

        def tiny(path):
            status = real(path)
            return os.statvfs_result((status.f_bsize, 1, 0, 0, 0, 0, 0, 0, 0, status.f_namemax))

        with patch.object(collection.os, "statvfs", side_effect=tiny), self.assertRaises(probe.Rejected):
            self.export(self.home)
        self.assertEqual(list(self.parent.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
