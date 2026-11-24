# SPDX-License-Identifier: MIT
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import omarchy_chip as chip

DEFINITION = Path(__file__).resolve().parents[3] / "scripts/stage1-execute-window.json"


def ranges_from_definition():
    document = json.loads(DEFINITION.read_text())
    assert document["schema_version"] == 1
    return [(int(entry["first"], 16), int(entry["last"], 16)) for entry in document["chip_id_ranges"]]


class ExecuteWindowTests(unittest.TestCase):
    # The engine archive ships these tests without the repository's scripts/.
    @unittest.skipUnless(DEFINITION.is_file(), "outside the repository")
    def test_the_copy_matches_the_definition_for_every_16_bit_chip_id(self):
        ranges = ranges_from_definition()
        drift = [chip_id for chip_id in range(0x10000)
                 if chip.uses_execute_window(chip_id)
                 != any(first <= chip_id <= last for first, last in ranges)]
        self.assertEqual(drift, [])

    def test_m4_and_later_use_the_window_and_m1_to_m3_do_not(self):
        for chip_id in (0x8132, 0x6040, 0x6041, 0x8140, 0x8142, 0x6050, 0x8152):
            with self.subTest(hex(chip_id)):
                self.assertTrue(chip.uses_execute_window(chip_id))
        for chip_id in (0x8103, 0x8112, 0x8122, 0x6000, 0x6001, 0x6002,
                        0x6020, 0x6021, 0x6022, 0x6030, 0x6031, 0x6032, 0x6034):
            with self.subTest(hex(chip_id)):
                self.assertFalse(chip.uses_execute_window(chip_id))

    def test_an_unknown_chip_is_not_an_execute_window_chip(self):
        for value in (None, "0x8132", True, 0x8132 + 0.0):
            with self.subTest(value):
                self.assertFalse(chip.uses_execute_window(value))


class CompatibleTests(unittest.TestCase):
    def test_the_soc_compatible_names_the_chip(self):
        self.assertEqual(chip.chip_from_compatible(b"apple,j713\0apple,t8132\0apple,arm-platform\0"), 0x8132)
        self.assertEqual(chip.chip_from_compatible(b"apple,j616s\0apple,t6041\0apple,arm-platform\0"), 0x6041)

    def test_no_soc_compatible_names_no_chip(self):
        for value in (b"", b"apple,j713\0apple,arm-platform\0", b"apple,t81320\0", None, "apple,t8132"):
            with self.subTest(value):
                self.assertIsNone(chip.chip_from_compatible(value))


if __name__ == "__main__":
    unittest.main()
