# SPDX-License-Identifier: MIT
"""A MacBook Neo's Touch ID (Mesa) calibration, read from macOS.

Older Macs keep the sensor calibration in a comb/fdrd/secb record that
aurora-touchid's extractor reads on Linux. Newer ones, such as the MacBook
Neo, store it in the iBoot System Container as one standalone signed IMG4
whose IM4P type is FSC2. The apple_sep driver loads that IMG4 as the
firmware its device tree names, apple/mesacal-j700.bin on the Neo.
"""

import os

FIRMWARE_NAME = "apple/mesacal-j700.bin"
# The iBoot System Container is about 550 MB; refuse anything far larger.
CONTAINER_MAXIMUM = 1 << 30


class MesaError(Exception):
    pass


def _tlv(data, at):
    tag, length, header = data[at], data[at + 1], 2
    if length >= 0x80:
        count = length & 0x7F
        if not 1 <= count <= 4:
            raise ValueError("bad DER length")
        length = int.from_bytes(data[at + 2 : at + 2 + count], "big")
        header = 2 + count
    end = at + header + length
    if end > len(data):
        raise ValueError("DER runs past the input")
    return tag, at + header, end


def _children(data, start, end):
    while start < end:
        child = _tlv(data, start)
        yield child
        start = child[2]


def _ia5(data, child):
    tag, start, end = child
    return bytes(data[start:end]) if tag == 0x16 else None


def fsc2_calibrations(data):
    """Every distinct signed FSC2 IMG4 (IM4P payload holding CALB, IM4M
    manifest) in data, as bytes."""
    found = set()
    at = data.find(b"\x16\x04IMG4")
    while at >= 0:
        for header in range(2, 7):
            start = at - header
            if start < 0 or data[start] != 0x30:
                continue
            try:
                _, content, end = _tlv(data, start)
                if content != at:
                    continue
                parts = list(_children(data, content, end))
                im4p = list(_children(data, parts[1][1], parts[1][2]))
                if (
                    _ia5(data, parts[0]) == b"IMG4"
                    and _ia5(data, im4p[0]) == b"IM4P"
                    and _ia5(data, im4p[1]) == b"FSC2"
                    and im4p[3][0] == 0x04
                    and b"CALB" in data[im4p[3][1] : im4p[3][1] + 256]
                    and len(parts) >= 3
                    and parts[2][0] == 0xA0
                    and b"IM4M" in data[parts[2][1] : parts[2][2]]
                ):
                    found.add(bytes(data[start:end]))
            except (ValueError, IndexError):
                continue
        at = data.find(b"\x16\x04IMG4", at + 1)
    return found


def calibration(container):
    """The one signed FSC2 calibration in an iBoot System Container image."""
    found = fsc2_calibrations(container)
    if len(found) != 1:
        raise MesaError(f"expected one signed FSC2 calibration, found {len(found)}")
    return found.pop()


def collect(device):
    """{firmware path: bytes} for this Mac's Touch ID, read from the raw
    iBoot System Container device, never written."""
    fd = os.open(device, os.O_RDONLY)
    try:
        size = os.lseek(fd, 0, os.SEEK_END)
        if not 0 < size <= CONTAINER_MAXIMUM:
            raise MesaError(f"{device} is {size} bytes, not an iBoot System Container")
        os.lseek(fd, 0, os.SEEK_SET)
        container = bytearray()
        while len(container) < size:
            block = os.read(fd, min(8 << 20, size - len(container)))
            if not block:
                break
            container += block
    finally:
        os.close(fd)
    return {FIRMWARE_NAME: calibration(container)}
