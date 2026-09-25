#!/usr/bin/env python3
"""The candidate importer accepts exactly a set its pinned key signed, and nothing else."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fixtures  # noqa: E402

c = fixtures.candidate_set


class CandidateSetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.base = Path(cls.tmp.name)
        cls.signer = fixtures.Signer(cls.base / "signer")
        cls.stranger = fixtures.Signer(cls.base / "stranger", uid="Stranger <stranger@example.invalid>")
        cls.good = cls.base / "good"
        cls.receipt = fixtures.make_set(cls.good, cls.signer)

    @classmethod
    def tearDownClass(cls):
        cls.signer.close()
        cls.stranger.close()
        for path in cls.base.rglob("*"):
            if path.is_dir() and not path.is_symlink():
                path.chmod(0o700)
        cls.tmp.cleanup()

    def setUp(self):
        self.work = Path(tempfile.mkdtemp(dir=self.base))
        self.input = self.work / "input"
        shutil.copytree(self.good, self.input)

    def verify(self, directory=None, receipt=None, source=fixtures.SOURCE, trust=None, manifest=None):
        return c.snapshot(directory or self.input, self.work / "output", receipt or self.receipt, source,
                          manifest, trust or self.signer.trust)

    def variant(self, **changes):
        directory = self.work / "variant"
        return directory, fixtures.make_set(directory, self.signer, **changes)

    def test_signed_set_creates_readonly_snapshot(self):
        summary = self.verify()
        self.assertEqual(len(summary["packages"]), 9)
        self.assertEqual(summary["signer"], self.signer.fingerprint)
        self.assertEqual((self.work / "output").stat().st_mode & 0o777, 0o555)
        imported = json.loads((self.work / "output/import.json").read_text())
        self.assertEqual({p["group"] for p in imported["packages"]}, {"runtime", "boot"})
        self.assertFalse((self.work / "output/candidate-signing-key.asc").exists())

    def test_output_is_created_exclusively(self):
        (self.work / "output").mkdir()
        with self.assertRaises(FileExistsError):
            self.verify()

    def test_tampered_archive(self):
        next(self.input.glob("linux-aurora-7*.pkg.tar.xz")).write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.verify()

    def test_receipt_manifest_and_source_must_match_the_inputs(self):
        for change, pattern in (("signature", "signature"), ("receipt", "receipt checksum"),
                                ("manifest", "build manifest"), ("source", "wrong build manifest"),
                                ("symlink", "")):
            with self.subTest(change=change):
                work = self.work / change
                shutil.copytree(self.good, work)
                if change == "signature":
                    (work / "signing.json.sig").unlink()
                if change == "symlink":
                    (work / "manifest.json").unlink()
                    (work / "manifest.json").symlink_to(self.good / "manifest.json")
                with self.assertRaisesRegex((ValueError, OSError), pattern):
                    c.snapshot(work, self.work / (change + "-out"),
                               "0" * 64 if change == "receipt" else self.receipt,
                               "c" * 40 if change == "source" else fixtures.SOURCE,
                               "0" * 64 if change == "manifest" else None, self.signer.trust)

    def test_set_cannot_supply_its_own_trust_anchor(self):
        trust = self.work / "trust"
        shutil.copytree(self.signer.trust, trust)
        (trust / "public.asc").write_bytes((self.stranger.trust / "public.asc").read_bytes())
        with self.assertRaisesRegex(ValueError, "trust anchor"):
            self.verify(trust=trust)

    def test_key_travelling_with_the_set_is_ignored(self):
        directory = self.work / "stranger-set"
        receipt = fixtures.make_set(directory, self.signer, key_signer=self.stranger)
        # The set carries the key that signed it; only the pinned key is ever used.
        self.assertEqual((directory / "candidate-signing-key.asc").read_bytes(),
                         (self.stranger.trust / "public.asc").read_bytes())
        with self.assertRaisesRegex(ValueError, "invalid signature: signing.json"):
            self.verify(directory, receipt)

    def test_missing_or_extra_package(self):
        directory, receipt = self.variant(names=[n for n in fixtures.VERSIONS if n != "uboot-asahi"])
        with self.assertRaisesRegex(ValueError, "wrong package set"):
            self.verify(directory, receipt)

    def test_refused_package(self):
        versions = dict(fixtures.VERSIONS, m1n1="1.5.0-1")
        contents = fixtures.default_contents()
        directory = self.work / "refused"
        receipt = fixtures.make_set(directory, self.signer, versions=versions, contents=contents,
                                    names=[*fixtures.VERSIONS, "m1n1"])
        with self.assertRaisesRegex(ValueError, "refused package"):
            self.verify(directory, receipt)

    def test_version_below_a_minimum(self):
        for name, low in (("limine-mkinitcpio-hook", "1.38.0-1.1"), ("omarchy-mac-boot", "20260921-9")):
            with self.subTest(name=name):
                directory = self.work / name
                receipt = fixtures.make_set(directory, self.signer, versions={name: low})
                with self.assertRaisesRegex(ValueError, "below the minimum"):
                    self.verify(directory, receipt)
                shutil.rmtree(self.work / "output", ignore_errors=True)

    def test_file_with_two_owners(self):
        contents = fixtures.default_contents()
        contents["omarchy-mac"]["usr/lib/asahi-boot/m1n1.bin"] = b"another m1n1"
        directory, receipt = self.variant(contents=contents)
        with self.assertRaisesRegex(ValueError, "ownership conflict"):
            self.verify(directory, receipt)

    def test_boot_payload_in_the_wrong_package(self):
        contents = fixtures.default_contents()
        contents["omarchy-mac"]["usr/lib/omarchy/initcpio/omarchy-mac-encrypt"] = \
            contents["omarchy-mac-boot"].pop("usr/lib/omarchy/initcpio/omarchy-mac-encrypt")
        directory, receipt = self.variant(contents=contents)
        with self.assertRaisesRegex(ValueError, "is not in omarchy-mac-boot"):
            self.verify(directory, receipt)

    def test_runtime_package_from_another_commit(self):
        contents = fixtures.default_contents()
        contents["omarchy-mac"]["usr/share/omarchy-mac/source-revision"] = ("c" * 40 + "\n").encode()
        directory, receipt = self.variant(contents=contents)
        with self.assertRaisesRegex(ValueError, "mixed package sources"):
            self.verify(directory, receipt)

    def test_set_digest_must_match_its_packages(self):
        directory, receipt = self.variant(manifest_edit=lambda m: m.update(set_sha256="0" * 64))
        with self.assertRaisesRegex(ValueError, "set digest"):
            self.verify(directory, receipt)

    def test_boot_package_pins_the_sets_runtime(self):
        directory = self.work / "pin"
        versions = dict(fixtures.VERSIONS)
        contents = fixtures.default_contents()
        original = fixtures.depends
        fixtures.depends = lambda name, v: ["omarchy=3.9.0-1"] if name == "omarchy-mac-boot" else original(name, v)
        try:
            receipt = fixtures.make_set(directory, self.signer, versions=versions, contents=contents)
        finally:
            fixtures.depends = original
        with self.assertRaisesRegex(ValueError, "pins another omarchy"):
            self.verify(directory, receipt)

    def test_describe_prints_what_an_inputs_record_pins(self):
        summary = c.describe(self.input, self.signer.trust)
        self.assertEqual(summary["receipt_sha256"], self.receipt)
        self.assertEqual(summary["source_commit"], fixtures.SOURCE)


class VercmpTest(unittest.TestCase):
    def test_pacman_ordering(self):
        for a, b, expected in (
            ("1.39.0-2", "1.39.0-2", 0), ("1.38.0-1.1", "1.39.0-2", -1),
            ("20260921-10.361203357120001", "20260921-10", 1), ("1.0a", "1.0", -1), ("1.0", "1.0.1", -1),
            ("1:1.0", "2.0", 1), ("1.0-1", "1.0-2", -1), ("1.0", "1.0-5", 0), ("1.0.a", "1.0.1", -1),
            ("4.0.0.alpha.quattro.r1-1", "4.0.2-1", -1), ("2026.07.asahi2-4", "2026.07.asahi1-1", 1),
        ):
            with self.subTest(a=a, b=b):
                self.assertEqual(c.vercmp(a, b), expected)
                self.assertEqual(c.vercmp(b, a), -expected)


if __name__ == "__main__":
    unittest.main()
