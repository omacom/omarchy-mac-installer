"""Behavioral experiments using fake data and the verified real age executable."""

import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import tarfile
import tempfile
import tracemalloc
import unittest
import uuid
from unittest.mock import patch

from omarchy_migration import probe
from omarchy_migration.dependency import configured_age


AGE = Path(os.environ["OMARCHY_TEST_AGE"]) if os.environ.get("OMARCHY_TEST_AGE") else None
# Public, deliberately synthetic test input. Never used for a real export.
SECRET = b"synthetic-only-otter-maple-window-cobalt"


class BundleProbe(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if configured_age() is None:
            raise unittest.SkipTest("set OMARCHY_TEST_AGE and OMARCHY_TEST_AGE_SHA256 for real age tests")
        cls.temporary = tempfile.TemporaryDirectory(prefix="migration-bundle-probe-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        cls.files = {}
        examples = {
            ".config/example-theme/selected": b"catppuccin\n",
            "Projects/demo/changed.txt": b"modified tracked file\n",
            "Projects/demo/untracked \u03bb.txt": b"local untracked draft\n",
            "Projects/demo/run": b"#!/bin/bash\nexit 0\n",
            ".ssh/id_example": b"FAKE-SSH-SECRET-NOT-A-PRIVATE-KEY\n",
            ".config/BraveSoftware/Brave-Origin/Default/example": b"FAKE-BROWSER-TOKEN\n",
            ".config/1Password/example": b"FAKE-VAULT-NOT-REAL-DATA\n",
            ".codex/auth.json": b'{"synthetic": "FAKE-CODEX-CREDENTIAL"}\n',
        }
        for index, (name, content) in enumerate(examples.items()):
            file = cls.root / f"source-{index}"
            file.write_bytes(content)
            file.chmod(0o750 if name.endswith("/run") else 0o600)
            os.utime(file, ns=(1720000000123456789, 1720000000123456789))
            cls.files[name] = file
        cls.manifest = probe.make_manifest(cls.files)
        cls.bundle = cls.root / "complete.age"
        cls.receipt = probe.encrypt(
            AGE, SECRET, cls.bundle,
            lambda stream: probe.write_archive(stream, cls.manifest, cls.files),
        )

    def test_roundtrip_validates_all_bytes_and_metadata(self):
        decoded = probe.decode(AGE, SECRET, self.bundle)
        self.assertEqual(decoded, self.manifest)
        for entry in decoded["entries"]:
            source = self.files[entry["path"]]
            self.assertEqual(entry["sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertEqual(entry["mode"], source.stat().st_mode & 0o777)
            self.assertEqual(entry["mtime_ns"], source.stat().st_mtime_ns)
        self.assertEqual(self.bundle.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.receipt["sha256"], probe.digest_file(self.bundle))
        ciphertext = self.bundle.read_bytes()
        for sentinel in (b"FAKE-CODEX-CREDENTIAL", b"manifest.json", b"catppuccin", b"Projects/demo"):
            self.assertNotIn(sentinel, ciphertext)

    def test_wrong_passphrase_has_no_completed_decode(self):
        with self.assertRaises(probe.Rejected):
            probe.decode(AGE, b"wrong-synthetic-passphrase", self.bundle)

    def test_header_work_limit_is_checked_before_a_crypto_process_starts(self):
        lines = self.bundle.read_bytes().split(b"\n", 4)
        for factor in (b"1", b"19", b"30", b"999999999999", b"018", b"+18"):
            altered = list(lines)
            altered[1] = altered[1].rsplit(b" ", 1)[0] + b" " + factor
            source = self.root / "expensive.age"
            source.write_bytes(b"\n".join(altered))
            with self.subTest(factor=factor), patch.object(probe, "AgeProcess") as process:
                with self.assertRaisesRegex(probe.Rejected, "work factor"):
                    probe.decode(AGE, SECRET, source)
                process.assert_not_called()

    def test_header_encoding_recipients_and_size_are_bounded_before_decryption(self):
        original = self.bundle.read_bytes().split(b"\n", 4)
        malformed = []
        for index, replacement in (
            (0, b"age-encryption.org/v999"),
            (1, original[1].replace(b"-> scrypt", b"-> plugin-example")),
            (1, original[1].replace(b" 18", b"= 18")),
            (2, b"A" * 10000),
            (2, original[2] + b"="),
            (3, original[1]),
            (3, b"--- " + b"A" * 42),
        ):
            lines = list(original)
            lines[index] = replacement
            malformed.append(b"\n".join(lines))
        for index, data in enumerate(malformed):
            source = self.root / "malformed-header.age"
            source.write_bytes(data)
            with self.subTest(case=index), patch.object(probe, "AgeProcess") as process:
                with self.assertRaises(probe.Rejected):
                    probe.decode(AGE, SECRET, source)
                process.assert_not_called()
        oversized = io.BytesIO(b"age-encryption.org/v1\n" + b"A" * 100000)
        with self.assertRaises(probe.Rejected):
            probe.checked_age_header(oversized)
        self.assertLess(oversized.tell(), 128)

    def test_decryptor_receives_checked_header_even_if_source_changes_later(self):
        source = self.root / "header-race.age"
        original = self.bundle.read_bytes()
        source.write_bytes(original)
        create = probe.AgeProcess

        def replace_header(*args, **kwargs):
            lines = original.split(b"\n", 4)
            lines[1] = lines[1].rsplit(b" ", 1)[0] + b" 30"
            source.write_bytes(b"\n".join(lines))
            return create(*args, **kwargs)

        with patch.object(probe, "AgeProcess", side_effect=replace_header):
            self.assertEqual(probe.decode(AGE, SECRET, source), self.manifest)

    def test_oversize_and_nonregular_ciphertext_are_refused_before_age(self):
        alias = self.root / "ciphertext-link"
        alias.symlink_to(self.bundle)
        fifo = self.root / "ciphertext-fifo"
        os.mkfifo(fifo, 0o600)
        with patch.object(probe, "AgeProcess") as process:
            with self.assertRaises(probe.Rejected):
                probe.decode(AGE, SECRET, self.bundle, limit=1)
            with self.assertRaises(OSError):
                probe.decode(AGE, SECRET, alias)
            with self.assertRaises(probe.Rejected):
                probe.decode(AGE, SECRET, fifo)
            process.assert_not_called()

    def test_changed_final_tag_is_rejected_after_valid_tar_content(self):
        data = bytearray(self.bundle.read_bytes())
        data[-1] ^= 1
        altered = self.root / "altered.age"
        altered.write_bytes(data)
        with self.assertRaises(probe.Rejected):
            probe.decode(AGE, SECRET, altered)

    def test_missing_final_bytes_is_rejected(self):
        altered = self.root / "truncated.age"
        altered.write_bytes(self.bundle.read_bytes()[:-16])
        with self.assertRaises(probe.Rejected):
            probe.decode(AGE, SECRET, altered)

    def test_unsupported_encrypted_schema_is_rejected(self):
        manifest = copy.deepcopy(self.manifest)
        manifest["schema"] = "omarchy-migration-probe/999"
        output = self.root / "future.age"
        probe.encrypt(AGE, SECRET, output, lambda stream: probe.write_archive(stream, manifest, self.files))
        with self.assertRaises(probe.Rejected):
            probe.decode(AGE, SECRET, output)

    def test_cancelled_writer_never_publishes_ciphertext(self):
        def cancelled(stream):
            stream.write(b"incomplete synthetic source")
            raise RuntimeError("synthetic capture cancellation")
        output = self.root / "cancelled.age"
        with self.assertRaisesRegex(RuntimeError, "synthetic capture cancellation"):
            probe.encrypt(AGE, SECRET, output, cancelled)
        self.assertFalse(output.exists())
        self.assertEqual(list(self.root.glob(".partial-*")), [])

    def test_publish_does_not_overwrite_existing_file(self):
        output = self.root / "already-exists.age"
        output.write_bytes(b"prior completed export")
        with self.assertRaises(FileExistsError):
            probe.encrypt(AGE, SECRET, output, lambda stream: stream.write(b"replacement"))
        self.assertEqual(output.read_bytes(), b"prior completed export")
        self.assertEqual(list(self.root.glob(".partial-*")), [])

    def test_manifest_rejects_paths_and_conflicting_entries(self):
        for path in ("../escape", "/absolute", "a/../b", "a//b", "./name", "nul\0name"):
            with self.subTest(path=repr(path)):
                manifest = copy.deepcopy(self.manifest)
                manifest["entries"][0]["path"] = path
                with self.assertRaises(probe.Rejected):
                    probe.validate_manifest(manifest)
        for pair in (("duplicate", "duplicate"), ("parent", "parent/child")):
            manifest = copy.deepcopy(self.manifest)
            manifest["entries"][0]["path"], manifest["entries"][1]["path"] = pair
            with self.assertRaises(probe.Rejected):
                probe.validate_manifest(manifest)

    def test_hardlinks_symlinks_devices_and_pax_are_rejected_before_payload(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE, tarfile.XHDTYPE):
            with self.subTest(kind=kind):
                member = tarfile.TarInfo("manifest.json")
                member.type = kind
                member.linkname = "../escape"
                with self.assertRaises(probe.Rejected):
                    probe.validate_archive(io.BytesIO(member.tobuf()))

    def test_oversized_header_rejected_before_read_or_allocation(self):
        member = tarfile.TarInfo("manifest.json")
        member.size = probe.MAX_MANIFEST + 1
        with self.assertRaisesRegex(probe.Rejected, "metadata"):
            probe.validate_archive(io.BytesIO(member.tobuf()))
        manifest = copy.deepcopy(self.manifest)
        manifest["entries"][0]["bytes"] = probe.MAX_TOTAL + 1
        with self.assertRaises(probe.Rejected):
            probe.validate_manifest(manifest)

    def test_payload_digest_mismatch_and_nonzero_trailing_data(self):
        manifest = copy.deepcopy(self.manifest)
        manifest["entries"][0]["sha256"] = "0" * 64
        archive = io.BytesIO()
        probe.write_archive(archive, manifest, self.files)
        archive.seek(0)
        with self.assertRaisesRegex(probe.Rejected, "digest"):
            probe.validate_archive(archive)
        archive = io.BytesIO()
        probe.write_archive(archive, self.manifest, self.files)
        archive.write(b"unexpected second archive")
        archive.seek(0)
        with self.assertRaisesRegex(probe.Rejected, "trailing"):
            probe.validate_archive(archive)

    def test_duplicate_json_fields_rejected(self):
        with self.assertRaises(probe.Rejected):
            json.loads('{"schema":"one","schema":"two"}', object_pairs_hook=probe.unique_json_pairs)

    def test_large_stream_does_not_buffer_whole_archive_in_python(self):
        source = self.root / "large-source"
        with source.open("wb") as stream:
            for _ in range(512):
                stream.write(b"synthetic" * 8192)  # 36 MiB; no compression.
        files = {"Projects/demo/large.bin": source}
        manifest = probe.make_manifest(files)
        output = self.root / "large.age"
        tracemalloc.start()
        try:
            probe.encrypt(AGE, SECRET, output, lambda stream: probe.write_archive(stream, manifest, files))
            self.assertEqual(probe.decode(AGE, SECRET, output), manifest)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertLess(peak, 4 * 1024 * 1024)
        print(f"probe_stream_bytes={source.stat().st_size} python_peak_bytes={peak}")



class ArchiveTailTests(unittest.TestCase):
    def archive(self, sizes):
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, directory)
        files = {}
        for index, size in enumerate(sizes):
            path = directory / f"f{index}"
            path.write_bytes(b"x" * size)
            files[f"f{index}"] = path
        manifest = probe.make_tree_manifest(files)
        stream = io.BytesIO()
        probe.write_archive(stream, manifest, files)
        return manifest, stream.getvalue()

    def test_every_padding_residue_the_writer_produces_is_accepted(self):
        tails = set()
        for size in range(0, 41 * 512, 128):
            manifest, data = self.archive([size, 100])
            end = -(-len(data.rstrip(b"\0")) // 512) * 512
            tails.add(len(data) - end)
            with self.subTest(size=size):
                self.assertEqual(probe.validate_archive(io.BytesIO(data)), manifest)
        # The sweep must reach the largest tail tarfile writes, or it proves nothing.
        self.assertEqual(max(tails), probe.MAX_ARCHIVE_TAIL)

    def test_tail_bounds_are_exact(self):
        manifest, data = self.archive([3])
        body = data.rstrip(b"\0")
        body += b"\0" * (-len(body) % 512)
        self.assertEqual(probe.MAX_ARCHIVE_TAIL, 10752)
        for tail, accepted in ((1024, True), (probe.MAX_ARCHIVE_TAIL, True), (probe.MAX_ARCHIVE_TAIL + 512, False),
                               (512, False), (1024 + 100, False)):
            with self.subTest(tail=tail):
                stream = io.BytesIO(body + b"\0" * tail)
                if accepted:
                    self.assertEqual(probe.validate_archive(stream), manifest)
                else:
                    with self.assertRaises(probe.Rejected):
                        probe.validate_archive(stream)


class ProvenanceTests(unittest.TestCase):
    def manifest(self, **changes):
        provenance = {
            "policy_revision": "try-omarchy/82927e9/4", "policy_sha256": "a" * 64, "request_sha256": "b" * 64,
            "originals": None,
            "metadata": [{"archive": ".config/hypr/input.lua", "lost": ["extended-attributes", "sparse"]}],
            "collection": {"counts": {"included": 2, "transformed": 1, "held-out": 1, "excluded": 1,
                                      "unsupported": 0, "inert-link": 0},
                           "exceptions": [
                               {"source": ".config/hypr/input.lua", "archive": ".config/hypr/input.lua", "outcome": "transformed",
                                "reason": "try_appended_fragment", "store": None, "rule": "try-hypr-input-overrides", "mount": None},
                               {"source": ".ssh", "archive": ".ssh", "outcome": "held-out", "reason": "adapter-unavailable",
                                "store": "ssh", "rule": None, "mount": None},
                               {"source": ".config/hypr/monitors.lua", "archive": ".config/hypr/monitors.lua", "outcome": "excluded",
                                "reason": "display_configuration", "store": None, "rule": "try-hypr-monitors", "mount": None}]}}
        entries = [{"path": ".config", "object": "objects/00000000", "kind": "directory", "mode": 0o700, "mtime_ns": 1},
                   {"path": ".config/hypr", "object": "objects/00000001", "kind": "directory", "mode": 0o700, "mtime_ns": 1},
                   {"path": ".config/hypr/input.lua", "object": "objects/00000002", "kind": "file", "mode": 0o644,
                    "mtime_ns": 1, "bytes": 0, "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"}]
        manifest = {"schema": probe.TREE_SCHEMA, "export_id": str(uuid.uuid4()), "entries": entries, "provenance": provenance}
        for path, value in changes.items():
            target = manifest
            keys = path.split(".")
            for key in keys[:-1]:
                target = target[int(key)] if key.isdigit() else target[key]
            if value is KeyError:
                del target[keys[-1]]
            else:
                target[int(keys[-1]) if keys[-1].isdigit() else keys[-1]] = value
        return manifest

    def test_tree_bundle_schema_is_the_contract_name_and_accepts_provenance(self):
        self.assertEqual(probe.TREE_SCHEMA, "omarchy-migration/bundle/2")
        probe.validate_manifest(self.manifest())
        probe.validate_manifest(self.manifest(provenance=KeyError))
        probe.validate_manifest(self.manifest(**{"provenance.collection.exceptions.1.archive": ""}))
        probe.validate_manifest(self.manifest(**{"provenance.originals": ".local/share/omarchy-migration/originals/x"}))

    def test_malformed_provenance_is_rejected(self):
        for change in ({"schema": probe.SCHEMA}, {"provenance.policy_revision": "Not A Label"},
                       {"provenance.policy_sha256": "A" * 64}, {"provenance.extra": 1},
                       {"provenance.collection.counts.excluded": 2},
                       {"provenance.collection.exceptions.0.outcome": "included"},
                       {"provenance.collection.exceptions.1.source": "../.ssh"},
                       {"provenance.collection.exceptions.2.rule": "Bad Rule"},
                       {"provenance.collection.exceptions.0.mount": 7},
                       {"provenance.policy_revision": "Try-Omarchy/82927e9/1"},
                       {"provenance.originals": "../elsewhere"}, {"provenance.originals": ""},
                       {"provenance.metadata.0.archive": "not/in/the/bundle"},
                       {"provenance.metadata.0.lost": ["sparse", "extended-attributes"]},
                       {"provenance.metadata.0.lost": ["colour"]}, {"provenance.metadata.0.lost": []},
                       {"provenance.metadata": KeyError},
                       {"provenance.metadata": [{"archive": ".config/hypr/input.lua", "lost": ["sparse"]},
                                                {"archive": ".config/hypr/input.lua", "lost": ["sparse"]}]},
                       {"provenance.metadata": [{"archive": ".config/hypr/input.lua", "lost": ["sparse"]},
                                                {"archive": ".config/hypr", "lost": ["acl"]}]},
                       {"provenance.originals": KeyError},
                       {"provenance.collection.exceptions.1.store": "SSH"},
                       {"provenance.collection.exceptions.2.reason": "Display_Configuration"},
                       {"provenance.collection.counts.included": 3},
                       {"provenance.collection.counts.included": 1100},
                       {"provenance.collection.exceptions.1.archive": ".config/hypr"},
                       {"provenance.collection.exceptions.0.archive": "elsewhere/input.lua"},
                       {"provenance.collection.exceptions.1.source": "x" * 256},
                       {"provenance.collection.exceptions.1.source": "/".join(["d"] * 65)},
                       {"unexpected": True}):
            with self.subTest(change=change), self.assertRaises(probe.Rejected):
                probe.validate_manifest(self.manifest(**change))

if __name__ == "__main__":
    unittest.main()
