"""The first boot claims exactly the staged region, once, and records why it couldn't."""

import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

from omarchy_migration import claim, staging

RECEIPT = json.loads((Path(__file__).resolve().parent / "fixtures/valid/receipt.json").read_text())
PARTITION_BYTES = 40 * staging.GiB
PARTUUID = "6a1f5c2e-0b7d-4d3e-9c1a-5e2f8b7d4c3a"


@unittest.skipUnless(sys.platform.startswith("linux"), "the claim reads Linux sysfs layouts")
class ClaimTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="migration-claim-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.bundle = os.urandom(3 * 4096 + 123)
        self.receipt = copy.deepcopy(RECEIPT)
        self.receipt["bundle"].update(bytes=len(self.bundle), sha256=hashlib.sha256(self.bundle).hexdigest())
        self.export_id = self.receipt["export_id"]
        self.offset = staging.staging_region(PARTITION_BYTES, len(self.bundle))
        self.partition = self.root / "nvme0n1p5"
        with open(self.partition, "wb") as disk:
            disk.truncate(PARTITION_BYTES)
            disk.seek(self.offset)
            disk.write(self.bundle)
        self.mapper = self.root / "mapper-root"
        self.mapper.write_bytes(b"")
        self.other = self.root / "nvme0n1p4"
        self.other.write_bytes(b"")
        self.numbers = {}
        self.sysfs = self.root / "sys"
        self.block("nvme0n1p5", "259:5", self.partition, partition=True)
        self.block("nvme0n1p4", "259:4", self.other, partition=True)
        self.block("dm-0", "253:0", self.mapper, uuid="CRYPT-LUKS2-0123456789abcdef-root", slaves=("nvme0n1p5",))
        self.by_partuuid = self.root / "by-partuuid"
        self.by_partuuid.mkdir()
        (self.by_partuuid / PARTUUID).symlink_to(self.partition)
        self.mountinfo = self.root / "mountinfo"
        self.mount(self.mapper)
        self.esp = self.root / "esp/omarchy"
        self.esp.mkdir(parents=True)
        self.marker = self.esp / "migration.json"
        self.write_marker()
        self.state = self.root / "state"

    def block(self, name, number, node, *, partition=False, uuid=None, slaves=()):
        directory = self.sysfs / "class/block" / name
        directory.mkdir(parents=True)
        (directory / "dev").write_text(number + "\n")
        (directory / "size").write_text(f"{os.stat(node).st_size // 512}\n")
        if partition:
            (directory / "partition").write_text("5\n")
        if uuid:
            (directory / "dm").mkdir()
            (directory / "dm/uuid").write_text(uuid + "\n")
        (directory / "slaves").mkdir()
        for slave in slaves:
            (directory / "slaves" / slave).symlink_to(f"../../{slave}")
        (self.sysfs / "dev/block").mkdir(parents=True, exist_ok=True)
        (self.sysfs / "dev/block" / number).symlink_to(f"../../class/block/{name}")
        self.numbers[os.stat(node).st_ino] = number

    def mount(self, source, point="/"):
        self.mountinfo.write_text(
            "22 1 0:21 / /proc rw - proc proc rw\n"
            f"29 1 0:25 /@ {point} rw,noatime shared:1 - btrfs {source} rw,compress=zstd,subvol=/@\n")

    def write_marker(self, **changes):
        document = {"schema": staging.MARKER_SCHEMA, "export_id": self.export_id, "partition_uuid": PARTUUID,
                    "offset": self.offset, "length": len(self.bundle), "sha256": self.receipt["bundle"]["sha256"],
                    "receipt": self.receipt}
        document.update(changes)
        self.marker.write_text(json.dumps(document))

    def device_number(self, target):
        try:
            return self.numbers[os.stat(target).st_ino]
        except KeyError:
            raise claim.ClaimError("root_device_unknown") from None

    def claim(self, **options):
        return claim.claim(self.marker, state=self.state, sysfs=self.sysfs, by_partuuid=self.by_partuuid,
                           mountinfo=self.mountinfo, device_number=self.device_number,
                           **{"margin": 0, **options})

    def command(self):
        errors = io.StringIO()
        with patch.object(claim, "block_device_number", self.device_number), patch.object(claim, "SPACE_MARGIN", 0), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(errors):
            status = claim.main(["--marker", str(self.marker), "--state", str(self.state), "--sysfs", str(self.sysfs),
                                 "--by-partuuid", str(self.by_partuuid), "--mountinfo", str(self.mountinfo)])
        return status, errors.getvalue()

    def failure(self):
        return json.loads((self.state / "claim-failed.json").read_text())

    def assert_failed(self, code, export_id="same"):
        status, errors = self.command()
        self.assertEqual(status, 0, errors)
        self.assertEqual(json.loads(errors.strip().splitlines()[-1]), {"error": code})
        self.assertEqual(self.failure(), {"schema": claim.FAILED_SCHEMA, "reason": code,
                                          "export_id": self.export_id if export_id == "same" else export_id})
        self.assertEqual(stat.S_IMODE((self.state / "claim-failed.json").stat().st_mode), 0o644)
        self.assertFalse(os.path.lexists(self.marker))
        self.assertFalse((self.state / "incoming" / self.export_id).exists())
        self.assertEqual(list((self.state / "pending").iterdir()) if (self.state / "pending").exists() else [], [])

    def assert_claimed(self):
        export = self.state / "incoming" / self.export_id
        self.assertEqual((export / "bundle.age").read_bytes(), self.bundle)
        self.assertEqual(json.loads((export / "receipt.json").read_text()), self.receipt)
        self.assertEqual(sorted(os.listdir(export)), ["bundle.age", "receipt.json"])
        for path, mode in ((self.state / "incoming", 0o700), (export, 0o700), (export / "bundle.age", 0o600),
                           (export / "receipt.json", 0o600), (self.state / "pending", 0o755),
                           (self.state / "pending" / self.export_id, 0o644), (self.state / "claimed", 0o700)):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), mode, path)
        self.assertFalse(self.marker.exists())
        self.assertFalse((self.state / "claim-failed.json").exists())

    def test_claims_through_the_luks_mapping(self):
        self.assertEqual(self.claim(), self.export_id)
        self.assert_claimed()

    def test_claims_from_an_unencrypted_root(self):
        self.mount(self.partition)
        self.assertEqual(self.claim(), self.export_id)
        self.assert_claimed()

    def test_command_claims_and_reports_the_export(self):
        status, errors = self.command()
        self.assertEqual((status, errors), (0, ""))
        self.assert_claimed()

    def test_no_marker_claims_nothing(self):
        self.marker.unlink()
        self.assertIsNone(self.claim())
        self.assertFalse(self.state.exists())

    def test_a_completed_claim_only_deletes_the_marker(self):
        self.claim()
        with open(self.partition, "r+b") as disk:
            disk.seek(self.offset)
            disk.write(b"\0" * len(self.bundle))
        self.write_marker()
        self.assertEqual(self.claim(), self.export_id)
        self.assert_claimed()

    def test_a_handed_off_export_is_not_claimed_again(self):
        self.claim()
        export = self.state / "incoming" / self.export_id
        for name in os.listdir(export):
            (export / name).unlink()
        export.rmdir()
        (self.state / "pending" / self.export_id).unlink()
        self.write_marker()
        self.assertEqual(self.claim(), self.export_id)
        self.assertFalse(export.exists())
        self.assertEqual(list((self.state / "pending").iterdir()), [])
        self.assertFalse(self.marker.exists())

    def test_an_interrupted_claim_finishes_without_rereading_a_complete_copy(self):
        self.claim()
        (self.state / "claimed" / self.export_id).unlink()
        (self.state / "pending" / self.export_id).unlink()
        with open(self.partition, "r+b") as disk:
            disk.seek(self.offset)
            disk.write(b"\0" * len(self.bundle))
        self.write_marker()
        self.assertEqual(self.claim(), self.export_id)
        self.assert_claimed()

    def test_an_interrupted_copy_is_redone(self):
        export = self.state / "incoming" / self.export_id
        export.mkdir(parents=True, mode=0o700)
        (export / "bundle.age.partial").write_bytes(b"torn")
        (export / "bundle.age").write_bytes(b"short")
        self.assertEqual(self.claim(), self.export_id)
        self.assert_claimed()

    def test_a_symlinked_marker_is_refused_and_removed(self):
        target = self.root / "elsewhere.json"
        self.marker.rename(target)
        self.marker.symlink_to(target)
        self.assert_failed("marker_unsafe", export_id=None)
        self.assertTrue(target.exists())

    def test_a_marker_that_is_not_a_regular_file_is_refused(self):
        self.marker.unlink()
        self.marker.mkdir()
        status, _ = self.command()
        self.assertEqual(status, 0)
        self.assertEqual(self.failure()["reason"], "marker_unsafe")

    def test_marker_and_receipt_problems_are_recorded(self):
        cases = (
            ("marker_oversized", lambda: self.marker.write_bytes(self.marker.read_bytes().ljust(staging.MAX_MARKER + 1)), "same"),
            ("marker_invalid", lambda: self.marker.write_text("{not json"), None),
            ("marker_invalid", lambda: self.write_marker(extra=True), "same"),
            ("receipt_invalid", lambda: self.write_marker(receipt={"schema": "omarchy-migration/receipt/1"}), "same"),
            ("receipt_mismatch", lambda: self.write_marker(sha256="0" * 64), "same"),
        )
        for code, damage, export_id in cases:
            with self.subTest(code=code):
                self.write_marker()
                damage()
                self.assert_failed(code, export_id)

    def test_a_partition_that_is_not_under_root_is_refused(self):
        (self.by_partuuid / PARTUUID).unlink()
        (self.by_partuuid / PARTUUID).symlink_to(self.other)
        self.assert_failed("partition_mismatch")
        self.write_marker(partition_uuid=str(uuid.uuid4()))
        self.assert_failed("partition_mismatch")

    def test_a_partition_whose_length_differs_from_sysfs_is_refused(self):
        (self.sysfs / "class/block/nvme0n1p5/size").write_text(f"{PARTITION_BYTES // 512 - 8}\n")
        self.assert_failed("partition_mismatch")

    def test_an_unknown_or_unsupported_root_device_is_refused(self):
        self.mount(self.mapper, point="/elsewhere")
        self.assert_failed("root_device_unknown")
        self.mount(self.mapper)
        (self.sysfs / "class/block/dm-0/dm/uuid").write_text("LVM-abc\n")
        self.write_marker()
        self.assert_failed("root_device_unsupported")
        (self.sysfs / "class/block/dm-0/dm/uuid").write_text("CRYPT-LUKS2-abc-root\n")
        (self.sysfs / "class/block/dm-0/slaves/nvme0n1p4").symlink_to("../../nvme0n1p4")
        self.write_marker()
        self.assert_failed("root_device_unsupported")

    def test_only_the_computed_offset_is_accepted(self):
        for offset in (self.offset - staging.MiB, self.offset + staging.MiB, self.offset + 4096, 0):
            with self.subTest(offset=offset):
                self.write_marker(offset=offset)
                self.assert_failed("region_invalid")

    def test_a_region_too_low_for_the_partition_is_refused(self):
        length = 2 * staging.GiB
        receipt = copy.deepcopy(self.receipt)
        receipt["bundle"]["bytes"] = length
        self.assertIsNone(staging.staging_region(PARTITION_BYTES, length))
        self.write_marker(length=length, receipt=receipt, offset=PARTITION_BYTES - 3 * staging.GiB)
        self.assert_failed("region_invalid")

    def test_a_digest_mismatch_is_recorded_and_leaves_nothing(self):
        with open(self.partition, "r+b") as disk:
            disk.seek(self.offset + 10)
            disk.write(b"x")
        self.assert_failed("digest_mismatch")

    def test_a_root_without_room_for_the_copy_is_refused(self):
        with self.assertRaises(claim.ClaimError) as caught:
            self.claim(margin=1 << 62)
        self.assertEqual(caught.exception.code, "insufficient_space")
        self.assertFalse((self.state / "incoming" / self.export_id).exists())
        self.assertTrue(self.marker.exists())
        with patch.object(claim, "SPACE_MARGIN", 1 << 62):
            self.main_with_real_margin()
        self.assertEqual(self.failure()["reason"], "insufficient_space")

    def main_with_real_margin(self):
        errors = io.StringIO()
        with patch.object(claim, "block_device_number", self.device_number), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(errors):
            status = claim.main(["--marker", str(self.marker), "--state", str(self.state), "--sysfs", str(self.sysfs),
                                 "--by-partuuid", str(self.by_partuuid), "--mountinfo", str(self.mountinfo)])
        return status, errors.getvalue()

    def test_an_unexpected_error_is_recorded_without_stopping_the_boot(self):
        with patch.object(claim, "_copy", side_effect=RuntimeError("boom")):
            self.assert_failed("claim_operation_failed")

    def test_mount_points_with_escaped_characters_are_read(self):
        self.mountinfo.write_text(f"29 1 0:25 /@ /mnt/with\\040space rw - btrfs {self.mapper} rw\n")
        self.assertEqual(claim.mount_source(self.mountinfo, Path("/mnt/with space")), str(self.mapper))


if __name__ == "__main__":
    unittest.main()
