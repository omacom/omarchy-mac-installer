# SPDX-License-Identifier: MIT
"""Single-boot deletion rules and the Linux migration script.

The macOS container is deleted only after the recovery account is an Omarchy
volume owner, and only when iBoot and System Recovery still bookend the disk.
The script that moves the btrfs root into the freed space runs from Omarchy.
"""

from pathlib import Path


class ReclaimError(ValueError):
    pass


def initial_plan(*, root_uuid, boot_uuid, isc_uuid, recovery_uuid):
    """The plan staged at install time. Recovery sets macosDeleted after the owner exists."""
    return {
        "schema": 1,
        "experimental": True,
        "filesystemUUID": "discover",
        "oldPartitionUUID": root_uuid,
        "bootPartitionUUID": boot_uuid,
        "iscUUID": isc_uuid,
        "recoveryUUID": recovery_uuid,
        "encrypted": True,
        "macosDeleted": False,
    }


def script_text():
    path = Path(__file__).with_name("experimental_reclaim_root.sh")
    if not path.is_file():
        raise ReclaimError("the experimental reclaim script is not in the engine")
    return path.read_text(encoding="utf-8")


def deletion_decision(
    partitions,
    *,
    target_uuid,
    stub_uuid,
    isc_uuid,
    recovery_uuid,
    owner_is_volume_owner,
):
    """Return delete or already-absent. Raise rather than touch a system container."""
    if owner_is_volume_owner is not True:
        raise ReclaimError("the recovery account is not an Omarchy volume owner")
    identities = (target_uuid, stub_uuid, isc_uuid, recovery_uuid)
    if any(not isinstance(value, str) or not value for value in identities):
        raise ReclaimError("single-boot container identity is incomplete")
    if len(set(identities)) != 4:
        raise ReclaimError("single-boot container identity is ambiguous")
    if not isinstance(partitions, list):
        raise ReclaimError("the disk map is unreadable")
    occupied = []
    seen = set()
    for item in sorted(partitions, key=lambda part: (part.get("offset", 0), part.get("name", ""))):
        if item.get("free"):
            continue
        uuid = item.get("uuid")
        if not isinstance(uuid, str) or not uuid or uuid in seen:
            raise ReclaimError("duplicate or missing partition identity")
        seen.add(uuid)
        occupied.append(item)
    if not occupied:
        raise ReclaimError("the disk has no partitions")
    by_uuid = {item["uuid"]: item for item in occupied}
    isc = by_uuid.get(isc_uuid)
    recovery = by_uuid.get(recovery_uuid)
    if (
        isc is None
        or recovery is None
        or isc.get("type") != "Apple_APFS_ISC"
        or recovery.get("type") != "Apple_APFS_Recovery"
        or occupied[0]["uuid"] != isc_uuid
        or occupied[-1]["uuid"] != recovery_uuid
    ):
        raise ReclaimError("iBoot and System Recovery are not the ends of the disk")
    target = by_uuid.get(target_uuid)
    if target is None:
        return {"action": "already-absent"}
    if target.get("type") != "Apple_APFS" or not (
        isc["offset"] < target["offset"] < recovery["offset"]
    ):
        raise ReclaimError("the recorded container is not the macOS container between iBoot and System Recovery")
    device = target.get("name")
    if not isinstance(device, str) or not device:
        raise ReclaimError("the recorded macOS container has no device")
    return {"action": "delete", "device": device}
