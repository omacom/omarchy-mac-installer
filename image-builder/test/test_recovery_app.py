#!/usr/bin/env python3
"""The producer carries a frozen native Recovery template without per-install data."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fixtures

recovery = fixtures.load("recovery_app", "builder/recovery_app.py")


class RecoveryAppTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.digest = fixtures.make_recovery_app(self.source)

    def test_frozen_app_and_zip_preserve_every_byte_after_source_changes(self):
        frozen = self.root / "frozen"
        recovery.snapshot(self.source, frozen, self.digest)
        for original in self.source.rglob("*"):
            if original.is_file():
                self.assertEqual(original.read_bytes(), (frozen / original.relative_to(self.source)).read_bytes())
        (self.source / recovery.APP / "Contents/MacOS/OmarchyRecovery").write_bytes(b"changed")
        self.assertEqual(recovery.validate(frozen, self.digest), self.digest)
        payload = self.root / "payload.zip"
        fixtures.write_payload(payload, "4f4d5801-424f-4f54-8000-000000000001",
                               "4f4d5801-524f-4f54-8000-000000000001", recovery_app=frozen)
        with zipfile.ZipFile(payload) as archive:
            self.assertEqual(recovery.validate_zip(archive, self.digest), self.digest)
            with self.assertRaisesRegex(ValueError, "pinned input"):
                recovery.validate_zip(archive, "0" * 64)

    def test_mismatched_manifest_extra_missing_changed_and_linked_files_fail(self):
        for defect in ("manifest", "extra", "missing", "changed", "link", "root-link"):
            with self.subTest(defect=defect):
                directory = self.root / defect
                shutil.copytree(self.source, directory)
                executable = directory / recovery.APP / "Contents/MacOS/OmarchyRecovery"
                if defect == "manifest":
                    (directory / recovery.MANIFEST).write_text("{}")
                elif defect == "extra":
                    (directory / recovery.APP / "Contents/extra").write_bytes(b"extra")
                elif defect == "missing":
                    executable.unlink()
                elif defect == "changed":
                    executable.write_bytes(b"x" * executable.stat().st_size)
                elif defect == "link":
                    executable.unlink()
                    executable.symlink_to(self.source / recovery.APP / "Contents/MacOS/OmarchyRecovery")
                else:
                    shutil.rmtree(directory)
                    directory.symlink_to(self.source, target_is_directory=True)
                with self.assertRaises(ValueError):
                    recovery.validate(directory, self.digest)

    def test_preview_and_embedded_data_layout_are_refused(self):
        for changes in ({"OmarchyRecoveryBuildMode": "preview"},
                        {"OmarchyRecoveryDataLayout": "embedded"},
                        {"CFBundleDisplayName": "Another Installer"}):
            with self.subTest(changes=changes):
                digest = fixtures.make_recovery_app(self.source, **changes)
                with self.assertRaises(ValueError):
                    recovery.validate(self.source, digest)

    def test_zip_rejects_stray_files_and_directories_outside_the_frozen_app(self):
        for stray in ('extra', 'InstallData/config.json', 'extra/',
                      recovery.APP + '/Contents/extra/'):
            with self.subTest(stray=stray):
                payload = self.root / 'payload.zip'
                fixtures.write_payload(payload, '4f4d5801-424f-4f54-8000-000000000001',
                                       '4f4d5801-524f-4f54-8000-000000000001', recovery_app=self.source)
                with zipfile.ZipFile(payload, 'a') as archive:
                    archive.writestr(recovery.PREFIX + stray, b'')
                with zipfile.ZipFile(payload) as archive, self.assertRaisesRegex(ValueError, 'unexpected Recovery'):
                    recovery.validate_zip(archive, self.digest)

    def test_paths_and_sizes_are_bounded_before_file_reads(self):
        for name, size in (("../outside", 1), ("Contents/../outside", 1),
                           ("Contents/MacOS/OmarchyRecovery", recovery.MAX_FILE + 1),
                           ("Contents/Resources/config.json", 1),
                           ("Contents/Resources/install-config.json", 1),
                           ("Contents/Resources/AuraPayloads/stage1.bin", 1)):
            with self.subTest(name=name, size=size):
                data = json.dumps({"schema": 1, "buildMode": "recovery", "files": {
                    name: {"bytes": size, "sha256": "0" * 64}}}).encode()
                with self.assertRaises(ValueError):
                    recovery.manifest_files(data)


if __name__ == "__main__":
    unittest.main()
