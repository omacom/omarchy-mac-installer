# SPDX-License-Identifier: MIT
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omarchy_reclaim import ReclaimError, deletion_decision


def disk():
    return [
        {"name": "disk0s1", "uuid": "ISC", "type": "Apple_APFS_ISC", "offset": 0},
        {"name": "disk0s2", "uuid": "MAC", "type": "Apple_APFS", "offset": 10},
        {"name": "disk0s5", "uuid": "STUB", "type": "Apple_APFS", "offset": 80},
        {"name": "disk0s3", "uuid": "REC", "type": "Apple_APFS_Recovery", "offset": 90},
    ]


class ReclaimDecisionTests(unittest.TestCase):
    def decide(self, **overrides):
        values = dict(
            partitions=disk(),
            target_uuid="MAC",
            stub_uuid="STUB",
            isc_uuid="ISC",
            recovery_uuid="REC",
            owner_is_volume_owner=True,
        )
        values.update(overrides)
        return deletion_decision(**values)

    def test_owned_macos_container_between_the_system_containers_can_be_deleted(self):
        self.assertEqual(self.decide(), {"action": "delete", "device": "disk0s2"})

    def test_deletion_waits_until_the_recovery_account_owns_the_volume(self):
        with self.assertRaisesRegex(ReclaimError, "volume owner"):
            self.decide(owner_is_volume_owner=False)

    def test_iboot_and_system_recovery_are_refused(self):
        for target in ("ISC", "REC", "STUB"):
            with self.subTest(target=target):
                with self.assertRaises(ReclaimError):
                    self.decide(target_uuid=target)

    def test_a_container_that_is_already_gone_is_not_deleted_again(self):
        partitions = [item for item in disk() if item["uuid"] != "MAC"]
        self.assertEqual(
            self.decide(partitions=partitions),
            {"action": "already-absent"},
        )

    def test_a_missing_system_container_stops_the_deletion(self):
        partitions = [item for item in disk() if item["uuid"] != "REC"]
        with self.assertRaisesRegex(ReclaimError, "ends of the disk"):
            self.decide(partitions=partitions)


if __name__ == "__main__":
    unittest.main()
