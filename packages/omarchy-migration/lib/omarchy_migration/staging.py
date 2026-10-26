"""The staged export on a native disk: its region rules and its ESP marker.

The installer writes the bundle raw into the Root partition's free space and
commits it with a marker on the ESP. Both sides compute the region with
`staging_region`; see docs/native-import.md in the installer repository.
"""

import json

from . import contract

MARKER_SCHEMA = "omarchy-migration/staging/1"
MAX_MARKER = 64 * 1024
MARKER_FIELDS = ("schema", "export_id", "partition_uuid", "offset", "length", "sha256", "receipt")

MiB = 1024 ** 2
GiB = 1024 ** 3
BLOCK = 4096
TAIL = 64 * MiB
SUPERBLOCK_LOW = 256 * GiB - MiB
SUPERBLOCK_HIGH = 256 * GiB + 64 * MiB
IMAGE_FLOOR = 33 * GiB
MARGIN = 4 * GiB


class MarkerError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _round_down(value, unit):
    return value // unit * unit


def staging_region(partition_bytes, bundle_bytes):
    """The offset of the staged bundle in its partition, or None when it can't be staged."""
    written = -(-bundle_bytes // BLOCK) * BLOCK
    end = _round_down(partition_bytes - TAIL, MiB)
    offset = _round_down(end - written, MiB)
    if offset < SUPERBLOCK_HIGH and offset + written > SUPERBLOCK_LOW:
        offset = _round_down(SUPERBLOCK_LOW - written, MiB)
    if offset < IMAGE_FLOOR + bundle_bytes + MARGIN:
        return None
    return offset


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise MarkerError("marker_invalid")
        result[key] = value
    return result


def _constant(value):
    raise MarkerError("marker_invalid")


def parse_marker(data):
    """A staging marker's fields, checked strictly, with its receipt validated."""
    if len(data) > MAX_MARKER:
        raise MarkerError("marker_oversized")
    try:
        marker = json.loads(data.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise MarkerError("marker_invalid") from None
    if not isinstance(marker, dict) or set(marker) != set(MARKER_FIELDS) or marker["schema"] != MARKER_SCHEMA:
        raise MarkerError("marker_invalid")
    try:
        contract._uuid(marker["export_id"], "$.export_id")
        contract._uuid(marker["partition_uuid"], "$.partition_uuid")
        contract._integer(marker["offset"], "$.offset")
        contract._integer(marker["length"], "$.length", 1, contract.MAX_CIPHERTEXT)
        contract._pattern(marker["sha256"], "$.sha256", contract.SHA256)
    except contract.ContractError:
        raise MarkerError("marker_invalid") from None
    receipt = marker["receipt"]
    try:
        if not isinstance(receipt, dict) or contract.validate(receipt) != contract.RECEIPT:
            raise MarkerError("receipt_invalid")
    except contract.ContractError:
        raise MarkerError("receipt_invalid") from None
    if (receipt["export_id"] != marker["export_id"] or receipt["bundle"]["bytes"] != marker["length"]
            or receipt["bundle"]["sha256"] != marker["sha256"]):
        raise MarkerError("receipt_mismatch")
    return marker
