import hashlib
import os
import plistlib
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omarchy_mesa import (  # noqa: E402
    DKIOCGETBLOCKCOUNT, DKIOCGETBLOCKSIZE, MesaError, calibration, collect, device_size,
    factory_calibration, has_builtin_sensor, sealed_calibration,
)


def der(tag, body):
    if len(body) < 0x80:
        return bytes([tag, len(body)]) + body
    size = len(body).to_bytes((len(body).bit_length() + 7) // 8, "big")
    return bytes([tag, 0x80 | len(size)]) + size + body


def ia5(text):
    return der(0x16, text.encode())


def im4p(kind=b"FSC2", payload=b"CALB" + b"\x5a" * 300):
    return der(0x30, ia5("IM4P") + der(0x16, kind) + ia5("mesa") + der(0x04, payload))


def img4(kind=b"FSC2", payload=b"CALB" + b"\x5a" * 300, manifest=b"IM4M"):
    return der(0x30, ia5("IMG4") + im4p(kind, payload) + der(0xA0, der(0x30, der(0x16, manifest))))


SEAL = der(0x30, ia5("IM4M") + der(2, b"\0") + der(4, b"sealing manifest"))
TRUST = b"synthetic trust bundle"


def factory_cache(directory, seal=SEAL, trust=TRUST):
    """The seal and trust records of a factory cache, as macOS names them."""
    root = Path(directory)
    (root / "seal-0000").write_bytes(seal)
    (root / ("trustobject-" + hashlib.sha256(trust).hexdigest().upper())).write_bytes(trust)
    (root / "trustobject-current").write_bytes(trust)
    return root


def sealed():
    """What the enclave loads for img4(): its IM4P under SEAL, with TRUST as secb."""
    image = der(0x30, ia5("IMG4") + im4p() + der(0xA0, SEAL))
    return der(0x30, ia5("comb") + der(0x30, ia5("fdrd") + der(0x04, image))
               + der(0x30, ia5("secb") + der(0x04, TRUST)))


def comb(image=None):
    if image is None:
        image = img4(kind=b"FSCl", manifest=b"IM4M FSCl")
    return der(0x30, ia5("comb") + der(0x30, ia5("fdrd") + der(0x04, image))
               + der(0x30, ia5("secb")))


class Fsc2CalibrationTests(unittest.TestCase):
    def test_the_one_signed_fsc2_image_is_returned_whole(self):
        blob = img4()
        container = b"\0" * 4096 + blob + b"\xff" * 4096
        self.assertEqual(calibration(container), blob)

    def test_the_same_image_stored_twice_counts_once(self):
        blob = img4()
        self.assertEqual(calibration(blob + b"\0" * 512 + blob), blob)

    def test_other_images_are_ignored(self):
        container = img4(kind=b"FSCl") + img4(payload=b"none" * 80) + img4(manifest=b"NOPE")
        with self.assertRaisesRegex(MesaError, "found 0"):
            calibration(container)

    def test_two_different_calibrations_are_refused(self):
        with self.assertRaisesRegex(MesaError, "found 2"):
            calibration(img4() + img4(payload=b"CALB" + b"\x33" * 300))

    def test_collect_reads_the_device_and_names_the_neos_firmware(self):
        blob = img4()
        with tempfile.TemporaryDirectory() as root:
            device = os.path.join(root, "rdisk0s1")
            Path(device).write_bytes(b"\0" * 9000 + blob + b"\0" * 9000)
            factory = factory_cache(tempfile.mkdtemp(dir=root))
            self.assertEqual(collect(device, factory=factory),
                             {"apple/mesacal-j700.bin": sealed()})

    def test_a_raw_disk_device_is_sized_by_its_driver(self):
        # Seeking to the end of /dev/rdiskN reports 0 on macOS, which refused
        # every Touch ID Neo's container; its driver reports the real size.
        answers = {DKIOCGETBLOCKSIZE: struct.pack("I", 4096),
                   DKIOCGETBLOCKCOUNT: struct.pack("Q", 140800)}
        fd = os.open(os.devnull, os.O_RDONLY)
        try:
            size = device_size(fd, ioctl=lambda _fd, request, _arg: answers[request])
        finally:
            os.close(fd)
        self.assertEqual(size, 576716800)

    def test_an_empty_device_is_refused(self):
        with tempfile.TemporaryDirectory() as root:
            device = os.path.join(root, "rdisk0s1")
            Path(device).write_bytes(b"")
            with self.assertRaisesRegex(MesaError, "not an iBoot System Container"):
                collect(device)

    def test_fscl_comb_wrapper_is_preserved(self):
        blob = comb()
        self.assertEqual(calibration(b"\0" * 4096 + blob + blob), blob)

    def test_fscl_requires_complete_signed_comb(self):
        for blob in (comb()[:-1], comb(img4(kind=b"FSCl", manifest=b"IM4M")),
                     comb(img4(kind=b"FSCl", payload=b"no calibration", manifest=b"IM4M FSCl")),
                     comb().replace(b"secb", b"nope")):
            with self.subTest(blob=blob[:20]), self.assertRaisesRegex(MesaError, "found 0"):
                calibration(blob)

    def test_different_supported_formats_are_ambiguous(self):
        with self.assertRaisesRegex(MesaError, "found 2"):
            calibration(comb() + img4())

    def test_nested_der_cannot_run_beyond_parent(self):
        blob = comb()
        # Delimit the outer sequence early while leaving trailing bytes in
        # the scan window: parent bounds must still reject the record.
        header = 2 + (blob[1] & 0x7f)
        length = len(blob) - header - 1
        damaged = bytes([0x30, blob[1]]) + length.to_bytes(header - 2, "big") + blob[header:]
        with self.assertRaisesRegex(MesaError, "found 0"):
            calibration(damaged)


class SealedCalibrationTests(unittest.TestCase):
    def test_standalone_fsc2_is_resealed_like_the_root_key(self):
        # The enclave refuses the plain FSC2 IMG4 and loads its IM4P under the
        # FDR sealing manifest with the trust bundle as secb.
        with tempfile.TemporaryDirectory() as directory:
            result = factory_calibration(img4(), factory_cache(directory))
        self.assertEqual(result, sealed())
        self.assertNotIn(b"IM4M FSC2", result)
        self.assertEqual(sealed_calibration(result), result)

    def test_collect_seals_fsc2_but_keeps_an_fscl_record_as_is(self):
        with tempfile.TemporaryDirectory() as root:
            device = Path(root) / "rdisk0s1"
            device.write_bytes(comb())
            missing = Path(root) / "no-factory-cache"
            self.assertEqual(collect(device, factory=missing), {"apple/mesacal-j700.bin": comb()})
            device.write_bytes(img4())
            with self.assertRaises(MesaError):
                collect(device, factory=missing)

    def test_unsealable_records_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            factory = factory_cache(directory)
            for image in (img4()[:-1], comb()):
                with self.subTest(image=image[:12]), self.assertRaises(MesaError):
                    factory_calibration(image, factory)
        with tempfile.TemporaryDirectory() as directory:
            factory = factory_cache(directory, seal=b"not a manifest")
            with self.assertRaises(MesaError):
                factory_calibration(img4(), factory)

    def test_only_a_complete_sealed_fsc2_is_accepted(self):
        good = sealed()
        for candidate in (img4(), good[:-1], good + b"trailing", good.replace(b"FSC2", b"MSRk"),
                          good.replace(b"secb", b"none"), comb()):
            with self.subTest(candidate=candidate[:24]), self.assertRaises(MesaError):
                sealed_calibration(candidate)


class SensorPresenceTests(unittest.TestCase):
    def query(self, nodes):
        def run(command, **kwargs):
            self.assertIn("IODeviceTree", command)
            self.assertIn("arm-io", command)
            return SimpleNamespace(stdout=plistlib.dumps(nodes))
        return has_builtin_sensor(run=run)

    def test_builtin_spi2_is_present(self):
        self.assertTrue(self.query([{"IORegistryEntryName": "arm-io", "IORegistryEntryChildren": [
            {"IORegistryEntryName": "spi2@385108000"}, {"IORegistryEntryName": "spi3@7510C000"}
        ]}]))

    def test_other_spi_and_external_touch_id_do_not_count(self):
        self.assertFalse(self.query([{"IORegistryEntryName": "arm-io", "IORegistryEntryChildren": [
            {"IORegistryEntryName": "spi3@7510C000"}, {"IORegistryEntryName": "qspi@75110000"},
            {"IORegistryEntryName": "AppleMesaAccessory"},
        ]}]))

    def test_missing_or_malformed_query_is_not_absent_hardware(self):
        for nodes in ([], [{"IORegistryEntryName": "arm-io"}],
                      [{"IORegistryEntryName": "wrong", "IORegistryEntryChildren": []}]):
            with self.subTest(nodes=nodes), self.assertRaises(MesaError):
                self.query(nodes)


if __name__ == "__main__":
    unittest.main()
