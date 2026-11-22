# SPDX-License-Identifier: MIT
"""The pinned upstream disk inventory, minus one failure mode."""

import logging
import subprocess

import diskutil


class DiskUtil(diskutil.DiskUtil):
    # Upstream stops at the first whole disk diskutil can't describe, such as
    # a disk image being attached or ejected. The engine only needs the system
    # disk, and find_system_disk still refuses when that one is missing.
    def get_disk_info(self):
        logging.info("DiskUtil.get_disk_info()")
        self.disks = {}
        for name in self.disk_list:
            try:
                self.disks[name] = self.get("info", "-plist", name)
            except subprocess.CalledProcessError as error:
                logging.warning(
                    f"diskutil info {name} failed (exit {error.returncode}); skipping it"
                )
                continue
            logging.debug(f" {name}: {self.disks[name]}")
