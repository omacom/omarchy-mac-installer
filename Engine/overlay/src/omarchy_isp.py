# SPDX-License-Identifier: MIT
"""Extend the engine's ISP registry and scan both Apple camera daemons.

Keep every inherited supported sensor. IMX558 1925's and IMX958 2224's
magic and length are the apple_isp driver's setfile entries; other
unqualified sensor IDs are not enabled merely because a byte pattern occurs
in the daemon.
"""

from collections import namedtuple
import contextlib
import logging
import os
from pathlib import Path
import struct

SetFile = namedtuple("SetFile", "sensor magic name size")
IMX558_1925 = SetFile(0x558, 0x19250306, "1925_03XX", 0xCBC0)
IMX958_2224 = SetFile(0x958, 0x22240106, "2224_01XX", 0x6FF4)
DAEMON_NAMES = ("appleh13camerad", "appleh16camerad")
NEWER_DAEMON = "appleh16camerad"
ALIGNMENT = 0x1000
FALLBACK_ARCHIVE = ["tar", "czf", "all_firmware.tar.gz"]
# The M4 family's chips (T8132, T6040, T6041). Their front cameras are tuned
# by setfiles that only appleh16camerad carries (1925_03XX, 2224_01XX).
M4_CHIPS = frozenset({0x8132, 0x6040, 0x6041})
log = logging.getLogger(__name__)


class IspError(ValueError):
    pass


def extended_setfiles(inherited):
    """Preserve inherited entries, adding 1925 once without silent conflicts."""
    entries = {}
    names = {}
    for entry in (*inherited, IMX558_1925):
        previous = entries.get(entry.magic)
        if previous is not None and tuple(previous) != tuple(entry):
            raise IspError(f"conflicting ISP registry entry {entry.magic:#x}")
        if entry.name in names and names[entry.name] != entry.magic:
            raise IspError(f"conflicting ISP firmware name {entry.name}")
        if entry.size < 32 or not entry.name or any(c in entry.name for c in "/\\"):
            raise IspError("invalid ISP setfile registry entry")
        entries[entry.magic] = entry
        names[entry.name] = entry.magic
    return tuple(entries.values())


def _valid_header(header):
    return (
        header[2] == header[3] == header[6] == 0
        and header[4] != 0
        and header[4] & 0xFF000000 == header[4]
        and header[5] != 0
        and header[5] & 0xFFFF0000 == header[5]
        and header[7] == 0x03C00000
    )


def _add(files, name, data):
    if name in files and files[name] != data:
        raise IspError(f"conflicting copies of {name}")
    files[name] = data


def extract(data, setfiles):
    """Extract complete known setfiles at 4 KiB boundaries from one binary."""
    known = {entry.magic: entry for entry in extended_setfiles(setfiles)}
    files = {}
    for offset in range(0, len(data) - 31, ALIGNMENT):
        header = struct.unpack_from(">8I", data, offset)
        if not _valid_header(header) or header[0] not in known:
            continue
        entry = known[header[0]]
        size = (entry.size + 63) & ~63
        if offset + size > len(data):
            raise IspError(f"truncated ISP setfile {entry.name} at {offset:#x}")
        _add(files, f"apple/isp_{entry.name}.dat", bytes(data[offset:offset + size]))
    return files


def collect(source_path, setfiles):
    """Scan the supplied Recovery directory, or a single archived daemon."""
    source = Path(source_path)
    paths = [source / name for name in DAEMON_NAMES] if source.is_dir() else [source]
    files = {}
    found_daemon = False
    for path in paths:
        if not path.exists():
            continue
        found_daemon = True
        log.info("Extracting ISP firmware from %s", path)
        for name, data in extract(path.read_bytes(), setfiles).items():
            _add(files, name, data)
    if not found_daemon:
        log.warning("No supported Apple camera daemon found in %s", source)
    return files


def collection_class(upstream, extra=()):
    """A drop-in collection preserving upstream's FWFile and registry API,
    also knowing the EXTRA setfiles."""
    setfiles = extended_setfiles((*upstream.ISP_SETFILES, *extra))

    class ISPFWCollection(upstream.ISPFWCollection):
        def extract_isp(self, data):
            for name, blob in sorted(extract(data, setfiles).items()):
                yield upstream.FWFile(name, blob)

        def load(self, source_path):
            for name, blob in sorted(collect(source_path, setfiles).items()):
                self.fwfiles.append((name, upstream.FWFile(name, blob)))

    return ISPFWCollection


class _WithNewerDaemon:
    """subprocess for stub: the fallback archive also carries appleh16camerad,
    so a re-extraction on Linux finds the setfiles the package took from it."""

    def __init__(self, module, directory):
        self._module, self._directory = module, directory

    def __getattr__(self, name):
        return getattr(self._module, name)

    def run(self, command, *args, **kwargs):
        directory = os.path.abspath(self._directory)
        if list(command[:3]) == FALLBACK_ARCHIVE and os.path.isfile(os.path.join(directory, NEWER_DAEMON)):
            command = [*command, "-C", directory, NEWER_DAEMON]
        return self._module.run(command, *args, **kwargs)


@contextlib.contextmanager
def m4_collectors(stub, upstream, chip_id, directory="recovery/usr/sbin"):
    """On an M4-family chip, rebind stub's ISP collection to read both camera
    daemons, and its subprocess to archive appleh16camerad from DIRECTORY.
    Every other Mac keeps stub's own collection and archive."""
    if chip_id not in M4_CHIPS:
        yield
        return
    old_isp, old_process = stub.ISPFWCollection, stub.subprocess
    try:
        stub.ISPFWCollection = collection_class(upstream, extra=(IMX958_2224,))
        stub.subprocess = _WithNewerDaemon(old_process, directory)
        yield
    finally:
        stub.ISPFWCollection, stub.subprocess = old_isp, old_process
