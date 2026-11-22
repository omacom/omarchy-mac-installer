# SPDX-License-Identifier: MIT
from pathlib import Path
import subprocess
import sys
from types import ModuleType
import unittest


class UpstreamDiskUtil:
    """The parts of the pinned upstream DiskUtil that get_disk_info uses."""

    def __init__(self, answers):
        self.answers = answers
        self.disk_list = list(answers)

    def get(self, *args):
        answer = self.answers[args[-1]]
        if answer is None:
            raise subprocess.CalledProcessError(1, ["diskutil", *args])
        return answer


upstream = ModuleType("diskutil")
upstream.DiskUtil = UpstreamDiskUtil
sys.modules["diskutil"] = upstream
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omarchy_diskutil import DiskUtil  # noqa: E402


class DiskInfoTests(unittest.TestCase):
    def test_a_disk_diskutil_cannot_describe_is_skipped(self):
        dutil = DiskUtil({"disk0": {"Internal": True}, "disk6": None, "disk7": {"Internal": False}})
        dutil.get_disk_info()
        self.assertEqual(dutil.disks, {"disk0": {"Internal": True}, "disk7": {"Internal": False}})

    def test_other_errors_still_stop_the_check(self):
        dutil = DiskUtil({"disk0": {}})
        dutil.get = lambda *args: (_ for _ in ()).throw(ValueError("bad plist"))
        with self.assertRaises(ValueError):
            dutil.get_disk_info()


if __name__ == "__main__":
    unittest.main()
