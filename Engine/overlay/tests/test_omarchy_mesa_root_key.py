# SPDX-License-Identifier: MIT
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import hashlib

from omarchy_mesa import MesaError, ROOT_KEY_NAME, collect, factory_root_key, root_key
from test_omarchy_mesa import der, factory_cache, ia5, img4


def root_image(extra_manifest_fields=0):
    payload = der(0x30, ia5("IM4P") + ia5("MSRk") + ia5("1.0")
                  + der(4, b"synthetic key payload"))
    manifest = der(0xA0, der(0x30, ia5("IM4M") + der(2, b"\0") + der(0x30, b"") * extra_manifest_fields))
    return der(0x30, ia5("IMG4") + payload + manifest)


def combined_root(image=None, security=b"synthetic trust bundle"):
    image = root_image() if image is None else image
    return der(0x30, ia5("comb") + der(0x30, ia5("fdrd") + der(4, image))
               + der(0x30, ia5("secb") + der(4, security)))


class RootKeyEnvelopeTests(unittest.TestCase):
    def test_complete_envelope_is_preserved_byte_for_byte(self):
        data = combined_root()
        self.assertEqual(root_key(data), data)

    def test_bare_image_is_not_a_complete_root_key(self):
        with self.assertRaises(MesaError):
            root_key(root_image())

    def test_incomplete_wrong_or_unbounded_envelopes_are_rejected(self):
        data = combined_root()
        for candidate in (
            b"", data[:-1], data + b"trailing", combined_root(security=b""),
            combined_root(root_image() + b"trailing"),
            data.replace(b"MSRk", b"FSC2"), data.replace(b"IM4M", b"none"),
            data.replace(b"secb", b"none"),
            combined_root(der(0x30, ia5("IMG4"))),
        ):
            with self.subTest(candidate=candidate[:24]), self.assertRaises(MesaError):
                root_key(candidate)

    def test_manifest_field_limit_is_enforced(self):
        candidate = combined_root(root_image(extra_manifest_fields=1023))
        with self.assertRaisesRegex(MesaError, "too many root-key fields"):
            root_key(candidate)

    def test_collect_includes_only_an_explicit_complete_root_key(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "container"
            path.write_bytes(img4())
            factory = Path(directory) / "factory"
            factory.mkdir()
            factory_cache(factory)
            self.assertNotIn(ROOT_KEY_NAME, collect(path, factory=factory))
            data = combined_root()
            self.assertEqual(collect(path, root_key_record=data, factory=factory)[ROOT_KEY_NAME], data)
            with self.assertRaises(MesaError):
                collect(path, root_key_record=root_image(), factory=factory)


class FactoryRootKeyTests(unittest.TestCase):
    payload = der(0x30, ia5("IM4P") + ia5("MSRk") + ia5("1.0") + der(4, b"synthetic key payload"))
    factory = der(0xA0, der(0x30, ia5("IM4M") + der(2, b"\0") + der(4, b"factory manifest")))
    seal = der(0x30, ia5("IM4M") + der(2, b"\0") + der(4, b"sealing manifest"))
    trust = b"synthetic trust bundle"

    def cache(self, directory, current=None):
        root = Path(directory)
        (root / "MSRk-0000").write_bytes(der(0x30, ia5("IMG4") + self.payload + self.factory))
        (root / "seal-0000").write_bytes(self.seal)
        name = "trustobject-" + hashlib.sha256(self.trust).hexdigest().upper()
        (root / name).write_bytes(self.trust)
        (root / "trustobject-current").write_bytes(self.trust if current is None else current)
        return root

    def test_record_is_resealed_with_the_sealing_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            expected = combined_root(der(0x30, ia5("IMG4") + self.payload + der(0xA0, self.seal)),
                                     security=self.trust)
            self.assertEqual(factory_root_key(self.cache(directory)), expected)

    def test_trust_bundle_follows_trustobject_current(self):
        expected = combined_root(der(0x30, ia5("IMG4") + self.payload + der(0xA0, self.seal)),
                                 security=self.trust)
        stale = b"bundle from an older macOS"
        with tempfile.TemporaryDirectory() as directory:
            root = self.cache(directory)
            (root / ("trustobject-" + hashlib.sha256(stale).hexdigest().upper())).write_bytes(stale)
            self.assertEqual(factory_root_key(root), expected)
        with tempfile.TemporaryDirectory() as directory:
            root = self.cache(directory)
            (root / "trustobject-current").unlink()
            self.assertEqual(factory_root_key(root), expected)
            (root / ("trustobject-" + hashlib.sha256(stale).hexdigest().upper())).write_bytes(stale)
            with self.assertRaises(MesaError):
                factory_root_key(root)

    def test_mismatched_or_missing_records_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.cache(directory, current=b"another bundle")
            with self.assertRaises(MesaError):
                factory_root_key(root)
        with tempfile.TemporaryDirectory() as directory:
            root = self.cache(directory)
            (root / "seal-0000").unlink()
            with self.assertRaises(MesaError):
                factory_root_key(root)
        with tempfile.TemporaryDirectory() as directory:
            root = self.cache(directory)
            for path in root.glob("trustobject-[0-9A-F]*"):
                path.write_bytes(b"tampered")
            with self.assertRaises(MesaError):
                factory_root_key(root)


if __name__ == "__main__":
    unittest.main()
