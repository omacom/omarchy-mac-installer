"""The staging region rules and the ESP marker match the shared contract."""

import copy
import json
from pathlib import Path
import unittest

from omarchy_migration import staging

FIXTURES = Path(__file__).resolve().parent / "fixtures"
RECEIPT = json.loads((FIXTURES / "valid/receipt.json").read_text())


def marker(**changes):
    document = {
        "schema": staging.MARKER_SCHEMA, "export_id": RECEIPT["export_id"],
        "partition_uuid": "6a1f5c2e-0b7d-4d3e-9c1a-5e2f8b7d4c3a",
        "offset": staging.staging_region(100 * staging.GiB, RECEIPT["bundle"]["bytes"]),
        "length": RECEIPT["bundle"]["bytes"], "sha256": RECEIPT["bundle"]["sha256"], "receipt": copy.deepcopy(RECEIPT),
    }
    document.update(changes)
    return document


def encoded(document):
    return json.dumps(document).encode()


class RegionTests(unittest.TestCase):
    def test_every_shared_case_matches(self):
        document = json.loads((FIXTURES / "staging-regions.json").read_text())
        self.assertEqual(document["schema"], "omarchy-migration/staging-regions/1")
        self.assertGreaterEqual(len(document["cases"]), 10)
        for case in document["cases"]:
            with self.subTest(case=case):
                self.assertEqual(staging.staging_region(case["partition_bytes"], case["bundle_bytes"]), case["offset"])

    def test_region_is_aligned_inside_the_partition_and_clear_of_the_superblock_copy(self):
        for partition in (40, 64, 100, 255, 256, 257, 258, 260, 300, 1000):
            for bundle in (1, 4096, 4097, 10 * staging.MiB + 3, staging.GiB, 20 * staging.GiB):
                offset = staging.staging_region(partition * staging.GiB, bundle)
                if offset is None:
                    continue
                written = -(-bundle // 4096) * 4096
                with self.subTest(partition=partition, bundle=bundle):
                    self.assertEqual(offset % staging.MiB, 0)
                    self.assertLessEqual(offset + written, partition * staging.GiB - staging.TAIL)
                    self.assertFalse(offset < staging.SUPERBLOCK_HIGH and offset + written > staging.SUPERBLOCK_LOW)
                    self.assertGreaterEqual(offset, staging.IMAGE_FLOOR + bundle + staging.MARGIN)

    def test_small_partitions_cannot_stage(self):
        for partition in (0, staging.MiB, 37 * staging.GiB):
            self.assertIsNone(staging.staging_region(partition, staging.GiB))


class MarkerTests(unittest.TestCase):
    def refused(self, data, code):
        with self.assertRaises(staging.MarkerError) as caught:
            staging.parse_marker(data)
        self.assertEqual(caught.exception.code, code)

    def test_a_valid_marker_is_accepted(self):
        self.assertEqual(staging.parse_marker(encoded(marker())), marker())

    def test_unknown_missing_and_duplicate_keys_are_refused(self):
        self.refused(encoded(marker(extra=1)), "marker_invalid")
        document = marker()
        del document["sha256"]
        self.refused(encoded(document), "marker_invalid")
        raw = encoded(marker())
        self.refused(raw[:-1] + b', "offset": 0}', "marker_invalid")
        nested = encoded(marker()).replace(b'"bytes":', b'"bytes": 1, "bytes":')
        self.refused(nested, "marker_invalid")

    def test_malformed_input_is_refused(self):
        for data in (b"", b"[]", b"\xff\xfe", b"{", encoded(marker()).replace(b'"offset": ', b'"offset": NaN, "o": ')):
            with self.subTest(data=data[:20]):
                self.refused(data, "marker_invalid")
        self.assertEqual(staging.parse_marker(encoded(marker()).ljust(staging.MAX_MARKER)), marker())
        self.refused(encoded(marker()).ljust(staging.MAX_MARKER + 1), "marker_oversized")

    def test_fields_are_strict(self):
        for changes in ({"schema": "omarchy-migration/staging/2"}, {"export_id": RECEIPT["export_id"].upper()},
                        {"partition_uuid": "6A1F5C2E-0B7D-4D3E-9C1A-5E2F8B7D4C3A"}, {"partition_uuid": "not-a-uuid"},
                        {"offset": -1}, {"offset": True}, {"offset": 1.5}, {"length": 0}, {"length": "5"},
                        {"sha256": "A" * 64}, {"sha256": "0" * 63}):
            with self.subTest(changes=changes):
                self.refused(encoded(marker(**changes)), "marker_invalid")

    def test_receipt_must_be_valid_and_describe_the_staged_bytes(self):
        self.refused(encoded(marker(receipt="receipt")), "receipt_invalid")
        broken = copy.deepcopy(RECEIPT)
        broken["bundle"]["format"] = "zip"
        self.refused(encoded(marker(receipt=broken)), "receipt_invalid")
        other = "1b4e28ba-2fa1-41d2-883f-0016d3cca427"
        self.refused(encoded(marker(export_id=other)), "receipt_mismatch")
        self.refused(encoded(marker(length=RECEIPT["bundle"]["bytes"] + 1)), "receipt_mismatch")
        self.refused(encoded(marker(sha256="0" * 64)), "receipt_mismatch")


if __name__ == "__main__":
    unittest.main()
