import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omarchy_mesa import MesaError, calibration, collect  # noqa: E402


def der(tag, body):
    if len(body) < 0x80:
        return bytes([tag, len(body)]) + body
    size = len(body).to_bytes((len(body).bit_length() + 7) // 8, "big")
    return bytes([tag, 0x80 | len(size)]) + size + body


def ia5(text):
    return der(0x16, text.encode())


def img4(kind=b"FSC2", payload=b"CALB" + b"\x5a" * 300, manifest=b"IM4M"):
    im4p = der(0x30, ia5("IM4P") + der(0x16, kind) + ia5("mesa") + der(0x04, payload))
    return der(0x30, ia5("IMG4") + im4p + der(0xA0, der(0x30, der(0x16, manifest))))


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
            self.assertEqual(collect(device), {"apple/mesacal-j700.bin": blob})

    def test_an_empty_device_is_refused(self):
        with tempfile.TemporaryDirectory() as root:
            device = os.path.join(root, "rdisk0s1")
            Path(device).write_bytes(b"")
            with self.assertRaisesRegex(MesaError, "not an iBoot System Container"):
                collect(device)


if __name__ == "__main__":
    unittest.main()
