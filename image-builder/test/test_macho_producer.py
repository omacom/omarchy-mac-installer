#!/usr/bin/env python3
"""Tests for the source-only m1n1-aurora Mach-O producer boundary."""

import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/build-m1n1-aurora-macho.py"
SPEC = importlib.util.spec_from_file_location("macho_producer", SCRIPT)
producer = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(producer)


MACHO = b"\xcf\xfa\xed\xfe" + b"\x0c\x00\x00\x01" + b"\0" * 24 + b"mach-o payload"
# M1N1SPT2, version 1, then the ten u64 package fields. The final field is
# the exact package length, as emitted by tools/sptm_stage2_pack.py.
STAGE2 = b"M1N1SPT2" + struct.pack("<10Q", 1, 0, 0, 0, 0, 0, 0, 0, 0, 88)


class MachOProducerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / "m1n1-macho"
        (self.source / "build").mkdir(parents=True)
        (self.source / "build/m1n1.macho").write_bytes(MACHO)
        (self.source / "build/m1n1-sptm-stage2.pkg").write_bytes(STAGE2)
        self.output = self.root / "package"

    def tearDown(self):
        self.tmp.cleanup()

    def test_produces_authenticated_members_and_explicit_nv2_off(self):
        sizes = producer.produce(self.source, self.output)
        destination = self.output / "usr/share/m1n1-aurora/macho"
        self.assertEqual((destination / "aura-boot.macho").read_bytes(), MACHO)
        self.assertEqual((destination / "aura-stage2.pkg").read_bytes(), STAGE2)
        self.assertEqual((destination / "nv-config").read_bytes(), b"x1n1.nv2=0\n")
        self.assertEqual(sizes, {"aura-boot.macho": len(MACHO),
                                 "aura-stage2.pkg": len(STAGE2), "nv-config": 11})

    def test_nv2_on_changes_only_the_config(self):
        producer.produce(self.source, self.output, nv2=True)
        destination = self.output / "usr/share/m1n1-aurora/macho"
        self.assertEqual((destination / "nv-config").read_bytes(), b"x1n1.nv2=1\n")
        self.assertEqual((destination / "aura-boot.macho").read_bytes(), MACHO)
        self.assertEqual((destination / "aura-stage2.pkg").read_bytes(), STAGE2)

    def test_missing_or_empty_inputs_fail_before_writing(self):
        for relative in ("build/m1n1.macho", "build/m1n1-sptm-stage2.pkg"):
            with self.subTest(relative=relative):
                path = self.source / relative
                original = path.read_bytes()
                path.unlink()
                with self.assertRaisesRegex(producer.ProducerError, "missing"):
                    producer.produce(self.source, self.output)
                self.assertFalse(self.output.exists())
                path.write_bytes(original)
        for relative in ("build/m1n1.macho", "build/m1n1-sptm-stage2.pkg"):
            with self.subTest(relative=relative):
                path = self.source / relative
                original = path.read_bytes()
                path.write_bytes(b"")
                with self.assertRaisesRegex(producer.ProducerError, "empty"):
                    producer.produce(self.source, self.output)
                self.assertFalse(self.output.exists())
                path.write_bytes(original)

    def test_invalid_magic_and_symlinks_fail_closed(self):
        self.source.joinpath("build/m1n1.macho").write_bytes(b"bad" + b"\0" * 40)
        with self.assertRaisesRegex(producer.ProducerError, "Mach-O.*invalid"):
            producer.produce(self.source, self.output)
        self.source.joinpath("build/m1n1.macho").write_bytes(MACHO)
        self.source.joinpath("build/m1n1-sptm-stage2.pkg").write_bytes(b"bad" + b"\0" * 100)
        with self.assertRaisesRegex(producer.ProducerError, "package.*invalid"):
            producer.produce(self.source, self.output)
        self.source.joinpath("build/m1n1-sptm-stage2.pkg").unlink()
        self.source.joinpath("build/m1n1-sptm-stage2.pkg").symlink_to(self.root / "missing")
        with self.assertRaisesRegex(producer.ProducerError, "must not be a symlink"):
            producer.produce(self.source, self.output)

    def test_existing_member_symlink_is_refused(self):
        destination = self.output / "usr/share/m1n1-aurora/macho"
        destination.mkdir(parents=True)
        (destination / "aura-boot.macho").symlink_to(self.root / "elsewhere")
        with self.assertRaisesRegex(producer.ProducerError, "not a regular file"):
            producer.produce(self.source, self.output)

    def test_output_path_symlink_is_refused(self):
        real = self.root / "real-package"
        real.mkdir()
        self.output.symlink_to(real, target_is_directory=True)
        with self.assertRaisesRegex(producer.ProducerError, "staging path"):
            producer.produce(self.source, self.output)

    def test_package_component_symlink_is_refused(self):
        self.output.mkdir()
        (self.output / "usr").symlink_to(self.root / "elsewhere", target_is_directory=True)
        with self.assertRaisesRegex(producer.ProducerError, "staging path"):
            producer.produce(self.source, self.output)


if __name__ == "__main__":
    unittest.main()
