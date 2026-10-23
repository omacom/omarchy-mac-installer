# SPDX-License-Identifier: MIT
"""A MacBook Neo's Touch ID (Mesa) calibration, read from macOS.

Accept both the comb/fdrd/secb FSCl record handled by Aurora's Linux
extractor and the standalone signed FSC2 format. The enclave loads
calibration only in the combined layout, so a standalone FSC2 image is
rebuilt like the root key, from this Mac's own factory records. No
calibration is read on J700 variants without an on-board SPI2 sensor
controller.
"""

import fcntl
import glob
import hashlib
import os
import plistlib
import stat
import struct
import subprocess
from xml.parsers.expat import ExpatError

FIRMWARE_NAME = "apple/mesacal-j700.bin"
ROOT_KEY_NAME = "apple/msrk-j700.bin"
# The iBoot System Container is about 550 MB; refuse anything far larger.
CONTAINER_MAXIMUM = 1 << 30
# <sys/disk.h>: _IOR('d', 24, uint32_t) and _IOR('d', 25, uint64_t).
DKIOCGETBLOCKSIZE = 0x40046418
DKIOCGETBLOCKCOUNT = 0x40086419
# macOS keeps this Mac's factory records here, world-readable, on the
# Hardware volume of the iBoot System Container.
FACTORY_CACHE = "/System/Volumes/Hardware/FactoryData/System/Library/Caches/com.apple.factorydata"


class MesaError(Exception):
    pass


def _tlv(data, at):
    if at < 0 or at + 2 > len(data):
        raise ValueError("truncated DER header")
    tag, length, header = data[at], data[at + 1], 2
    if length >= 0x80:
        count = length & 0x7F
        if not 1 <= count <= 4 or at + 2 + count > len(data):
            raise ValueError("bad DER length")
        if data[at + 2] == 0:
            raise ValueError("non-minimal DER length")
        length = int.from_bytes(data[at + 2 : at + 2 + count], "big")
        if length < 0x80:
            raise ValueError("non-minimal DER length")
        header = 2 + count
    end = at + header + length
    if end > len(data):
        raise ValueError("DER runs past the input")
    return tag, at + header, end


def _children(data, start, end):
    while start < end:
        child = _tlv(data, start)
        if child[2] > end:
            raise ValueError("DER child runs past its parent")
        yield child
        start = child[2]


def _ia5(data, child):
    tag, start, end = child
    return bytes(data[start:end]) if tag == 0x16 else None


def root_key(data):
    """Preserve a complete combined root-key record supplied by a local collector.

    This only checks the envelope. It does not establish the issuer chain or
    sensor binding, and never builds a replacement manifest or trust bundle.
    """
    return _combined(data, b"MSRk", "root-key", "sensor root key")


def sealed_calibration(data):
    """Check a calibration in the combined layout the enclave loads,
    comb { fdrd: IMG4 { IM4P(FSC2), [0] seal }, secb: trust bundle }, and
    return it unchanged. Like root_key(), this checks only the envelope."""
    return _combined(data, b"FSC2", "calibration", "sensor calibration")


def _combined(data, payload_type, name, title):
    """data, if it is one complete comb { fdrd: IMG4 { IM4P(payload_type),
    [0] IM4M }, secb: trust bundle } record with nothing after it."""
    def children(start, end):
        fields = []
        for field in _children(data, start, end):
            if len(fields) >= 1024:
                raise ValueError(f"too many {name} fields")
            fields.append(field)
        return fields

    try:
        if not 0 < len(data) <= 16 << 20:
            raise ValueError(f"invalid {name} size")
        outer = _tlv(data, 0)
        if outer[0] != 0x30 or outer[2] != len(data):
            raise ValueError("invalid combined envelope")
        fields = children(outer[1], outer[2])
        if (len(fields) != 3 or _ia5(data, fields[0]) != b"comb"
                or fields[1][0] != 0x30 or fields[2][0] != 0x30):
            raise ValueError(f"{name} requires its complete combined envelope")
        record = children(fields[1][1], fields[1][2])
        security = children(fields[2][1], fields[2][2])
        if (len(record) != 2 or _ia5(data, record[0]) != b"fdrd"
                or record[1][0] != 4 or len(security) != 2
                or _ia5(data, security[0]) != b"secb" or security[1][0] != 4
                or security[1][1] == security[1][2]):
            raise ValueError(f"missing {name} record or trust bundle")
        image = _tlv(data, record[1][1])
        if image[0] != 0x30 or image[2] != record[1][2]:
            raise ValueError(f"invalid embedded {name} image")
        parts = children(image[1], image[2])
        if (len(parts) != 3 or _ia5(data, parts[0]) != b"IMG4"
                or parts[1][0] != 0x30 or parts[2][0] != 0xA0):
            raise ValueError(f"missing signed {name} envelope")
        payload = children(parts[1][1], parts[1][2])
        if (len(payload) != 4 or _ia5(data, payload[0]) != b"IM4P"
                or _ia5(data, payload[1]) != payload_type or payload[2][0] != 0x16
                or payload[2][1] == payload[2][2] or payload[3][0] != 4
                or payload[3][1] == payload[3][2]):
            raise ValueError(f"invalid {name} payload")
        wrapper = children(parts[2][1], parts[2][2])
        if len(wrapper) != 1 or wrapper[0][0] != 0x30:
            raise ValueError(f"invalid {name} manifest wrapper")
        manifest = children(wrapper[0][1], wrapper[0][2])
        if len(manifest) < 2 or _ia5(data, manifest[0]) != b"IM4M":
            raise ValueError(f"missing {name} manifest")
    except (ValueError, IndexError) as error:
        raise MesaError(f"invalid signed {title}: {error}") from error
    return bytes(data)


def _der(tag, value):
    n = len(value)
    if n < 0x80:
        head = bytes([n])
    else:
        size = n.to_bytes((n.bit_length() + 7) // 8, "big")
        head = bytes([0x80 | len(size)]) + size
    return bytes([tag]) + head + value


def _one(directory, pattern):
    found = [path for path in glob.glob(os.path.join(directory, pattern))
             if not path.endswith("-current")]
    if len(found) != 1:
        raise MesaError(f"expected one {pattern} factory record, found {len(found)}")
    with open(found[0], "rb") as fd:
        return os.path.basename(found[0]), fd.read(16 << 20)


def _trust_bundle(directory):
    """The trust bundle the enclave is given: the trustobject-<sha256>
    record that trustobject-current holds. A macOS update can leave older
    named bundles in the cache, so only without trustobject-current must
    there be exactly one."""
    current = os.path.join(directory, "trustobject-current")
    if not os.path.exists(current):
        name, trust = _one(directory, "trustobject-*")
        if hashlib.sha256(trust).hexdigest() != name.split("-", 1)[1].lower():
            raise MesaError("trust bundle does not match its name")
        return trust
    with open(current, "rb") as fd:
        trust = fd.read(16 << 20)
    digest = hashlib.sha256(trust).hexdigest()
    named = [path for path in glob.glob(os.path.join(directory, "trustobject-*"))
             if os.path.basename(path).split("-", 1)[1].lower() == digest]
    if not named:
        raise MesaError("no named trust bundle matches trustobject-current")
    with open(named[0], "rb") as fd:
        if fd.read(len(trust) + 1) != trust:
            raise MesaError("trustobject-current differs from the named trust bundle")
    return trust


def factory_root_key(directory=FACTORY_CACHE):
    """This Mac's combined sensor root-key container, rebuilt from its own
    factory cache the way macOS hands it to the enclave.

    The cached MSRk record carries its factory manifest, which the enclave
    does not trust. macOS pairs the record's IM4P with the FDR sealing
    manifest (seal-<ECID>) and adds the trust bundle as secb, as it does for
    every other record in the cache (appv, fCfg). Nothing is synthesized:
    every byte comes from this Mac's own records, and the result is
    checked with root_key().
    """
    _, record = _one(directory, "MSRk-*")
    _, seal = _one(directory, "seal-*")
    return root_key(_reseal(record, seal, _trust_bundle(directory), "MSRk", "root key"))


def factory_calibration(image, directory=FACTORY_CACHE):
    """A standalone signed FSC2 calibration rebuilt the way the enclave
    loads it.

    The enclave refuses the plain IMG4 (LOAD_CALIBRATION fails), but accepts
    the same IM4P under the FDR sealing manifest with the trust bundle as
    secb, exactly like the root key. Every byte comes from this Mac's own
    calibration and factory records; the result is checked with
    sealed_calibration().
    """
    _, seal = _one(directory, "seal-*")
    return sealed_calibration(_reseal(image, seal, _trust_bundle(directory), "FSC2", "calibration"))


def _reseal(record, seal, trust, kind, name):
    """comb { fdrd: IMG4 { record's IM4P, [0] seal }, secb: trust }: one of
    this Mac's signed records with its own manifest replaced by the FDR
    sealing manifest, as macOS stores appv and fCfg."""
    try:
        outer = _tlv(record, 0)
        parts = list(_children(record, outer[1], outer[2]))
        if (outer[0] != 0x30 or outer[2] != len(record) or len(parts) != 3
                or _ia5(record, parts[0]) != b"IMG4" or parts[1][0] != 0x30):
            raise ValueError(f"{kind} record is not an IMG4")
        payload_start = parts[0][2]
        im4p = bytes(record[payload_start:parts[1][2]])
        manifest = _tlv(seal, 0)
        if manifest[0] != 0x30 or manifest[2] != len(seal):
            raise ValueError("seal record is not one DER manifest")
    except ValueError as error:
        raise MesaError(f"cannot rebuild the sensor {name}: {error}") from error
    image = _der(0x30, _der(0x16, b"IMG4") + im4p + _der(0xA0, seal))
    return _der(0x30, _der(0x16, b"comb")
                + _der(0x30, _der(0x16, b"fdrd") + _der(0x04, image))
                + _der(0x30, _der(0x16, b"secb") + _der(0x04, trust)))


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
                    and b"CALB" in data[im4p[3][1] : min(im4p[3][2], im4p[3][1] + 256)]
                    and len(parts) >= 3
                    and parts[2][0] == 0xA0
                    and b"IM4M" in data[parts[2][1] : parts[2][2]]
                ):
                    found.add(bytes(data[start:end]))
            except (ValueError, IndexError):
                continue
        at = data.find(b"\x16\x04IMG4", at + 1)
    return found


def fscl_calibrations(data):
    """Carve complete FSCl comb records using the Linux extractor's checks.

    Keep the outer comb/fdrd/secb wrapper, which is part of the calibration
    consumed by the SEP driver; do not return only its embedded IMG4.
    """
    found = set()
    marker = data.find(b"\x16\x04comb")
    while marker >= 0:
        for header in range(2, 7):
            start = marker - header
            if start < 0 or data[start] != 0x30:
                continue
            try:
                _, content, end = _tlv(data, start)
                if content != marker:
                    continue
                outer = list(_children(data, content, end))
                if len(outer) != 3 or _ia5(data, outer[0]) != b"comb":
                    continue
                if outer[1][0] != 0x30 or outer[2][0] != 0x30:
                    continue
                fdrd = list(_children(data, outer[1][1], outer[1][2]))
                secb = list(_children(data, outer[2][1], outer[2][2]))
                if len(fdrd) != 2 or _ia5(data, fdrd[0]) != b"fdrd":
                    continue
                if fdrd[1][0] != 0x04 or _ia5(data, secb[0]) != b"secb":
                    continue
                image = _tlv(data, fdrd[1][1])
                if image[0] != 0x30 or image[2] != fdrd[1][2]:
                    continue
                parts = list(_children(data, image[1], image[2]))
                if _ia5(data, parts[0]) != b"IMG4" or parts[1][0] != 0x30:
                    continue
                im4p = list(_children(data, parts[1][1], parts[1][2]))
                if (_ia5(data, im4p[0]) != b"IM4P"
                        or _ia5(data, im4p[1]) != b"FSCl"
                        or im4p[2][0] != 0x16 or im4p[3][0] != 0x04):
                    continue
                if b"CALB" not in data[im4p[3][1]:min(im4p[3][2], im4p[3][1] + 64)]:
                    continue
                remainder = data[parts[1][2]:image[2]]
                if b"IM4M" not in remainder or b"FSCl" not in remainder:
                    continue
                found.add(bytes(data[start:end]))
            except (ValueError, IndexError):
                continue
        marker = data.find(b"\x16\x04comb", marker + 1)
    return found


def calibration(container):
    """The one distinct supported calibration in an iBoot container."""
    found = fsc2_calibrations(container) | fscl_calibrations(container)
    if len(found) != 1:
        raise MesaError(f"expected one signed FSCl/FSC2 calibration, found {len(found)}")
    return found.pop()


def has_builtin_sensor(run=subprocess.run):
    """Read the on-board controller from ADT, never an external keyboard.

    J700 variants lacking Touch ID omit spi2 from arm-io. A failed or
    malformed query is an error, not evidence that the sensor is absent.
    """
    output = run(
        ["/usr/sbin/ioreg", "-a", "-r", "-p", "IODeviceTree", "-n", "arm-io", "-d", "2"],
        check=True, capture_output=True,
    ).stdout
    try:
        roots = plistlib.loads(output)
        if not isinstance(roots, list) or len(roots) != 1:
            raise ValueError("expected one arm-io node")
        root = roots[0]
        if root.get("IORegistryEntryName", "").split("@")[0] != "arm-io":
            raise ValueError("not the arm-io node")
        children = root["IORegistryEntryChildren"]
        if not isinstance(children, list) or not children:
            raise ValueError("arm-io children are missing")
        names = [child["IORegistryEntryName"].split("@")[0] for child in children]
    except (ValueError, KeyError, TypeError, AttributeError, ExpatError, plistlib.InvalidFileException) as error:
        raise MesaError(f"cannot determine built-in Touch ID hardware: {error}") from error
    return "spi2" in names


def device_size(fd, ioctl=fcntl.ioctl):
    """Bytes in a disk device or file. Seeking to the end of a macOS raw disk
    device (/dev/rdiskN) reports 0, so a character device's size comes from
    its driver's block size and count."""
    if not stat.S_ISCHR(os.fstat(fd).st_mode):
        return os.lseek(fd, 0, os.SEEK_END)
    block_size, = struct.unpack("I", ioctl(fd, DKIOCGETBLOCKSIZE, bytes(4)))
    block_count, = struct.unpack("Q", ioctl(fd, DKIOCGETBLOCKCOUNT, bytes(8)))
    return block_size * block_count


def loadable_calibration(found, factory=FACTORY_CACHE):
    """found in the combined layout the enclave loads: an FSCl comb record
    as it is, a standalone FSC2 IMG4 rebuilt from the factory records."""
    try:
        outer = _tlv(found, 0)
        standalone = _ia5(found, _tlv(found, outer[1])) == b"IMG4"
    except ValueError as error:
        raise MesaError(f"invalid calibration record: {error}") from error
    return factory_calibration(found, factory) if standalone else found


def collect(device, *, root_key_record=None, factory=FACTORY_CACHE):
    """{firmware path: bytes} for this Mac's Touch ID, read from the raw
    iBoot System Container device, never written."""
    # The production adapter supplies factory_root_key(), rebuilt from this
    # Mac's own factory cache; collection without one still succeeds.
    root = root_key(root_key_record) if root_key_record is not None else None
    fd = os.open(device, os.O_RDONLY)
    try:
        size = device_size(fd)
        if not 0 < size <= CONTAINER_MAXIMUM:
            raise MesaError(f"{device} is {size} bytes, not an iBoot System Container")
        os.lseek(fd, 0, os.SEEK_SET)
        container = bytearray()
        while len(container) < size:
            block = os.read(fd, min(8 << 20, size - len(container)))
            if not block:
                raise MesaError("short read of the iBoot System Container")
            container += block
    finally:
        os.close(fd)
    files = {FIRMWARE_NAME: loadable_calibration(calibration(container), factory)}
    if root is not None:
        files[ROOT_KEY_NAME] = root
    return files
