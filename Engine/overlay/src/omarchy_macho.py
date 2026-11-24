# SPDX-License-Identifier: MIT
"""Check a Mach-O Stage 1 boot object before the engine fills and stages it.

On an execute-window chip (omarchy_chip.uses_execute_window) Stage 1 is aura's
Mach-O fileset boot object, not a raw image. kmutil records the size of
__TEXT_BOOT_EXEC as the boot policy's kcbz, and iBoot starts SPTM only when
kcbz is nonzero, so the object must not have that segment. Aura's boot
objects also keep every segment fully file-backed at its virtual offset, which
is how iBoot places them, and enter inside __TEXT_EXEC.

The native Recovery pads __TEXT_EXEC for the execute window just before
`kmutil configure-boot --compress`; the engine only checks the unpadded
object and fills its Stage 1 configuration block in place.

Ported from aurora-silicon/aurora-installer 7be5339 (MIT):
MachOBootObject.layout and readOnlySize in
macOS/Recovery/Sources/Account/AuraStage1.swift.
"""

from collections import namedtuple
import struct

MH_MAGIC_64 = 0xFEEDFACF
CPU_TYPE_ARM64 = 0x0100000C
MH_FILESET = 12
LC_SEGMENT_64 = 0x19
LC_UNIXTHREAD = 0x5
ARM_THREAD_STATE64 = 6
REQUIRED = ("__TEXT", "__TEXT_EXEC", "__PRELINK_INFO", "__LINKEDIT")

Segment = namedtuple("Segment", "vmaddr vmsize fileoff filesize")


class MachOError(ValueError):
    pass


def layout(data):
    """The object's segments by name and its entry point, or MachOError."""
    data = bytes(data)

    def u32(offset):
        if offset < 0 or offset + 4 > len(data):
            raise MachOError("truncated")
        return struct.unpack_from("<I", data, offset)[0]

    def u64(offset):
        return u32(offset) | u32(offset + 4) << 32

    if u32(0) != MH_MAGIC_64 or u32(4) != CPU_TYPE_ARM64:
        raise MachOError("not a 64-bit Arm Mach-O image")
    if u32(12) != MH_FILESET:
        raise MachOError("not a fileset boot object")
    count, size = u32(16), u32(20)
    if 32 + size > len(data):
        raise MachOError("load commands are truncated")
    segments, entry, offset = {}, None, 32
    for _ in range(count):
        command, length = u32(offset), u32(offset + 4)
        if length < 8 or length % 8 or offset + length > 32 + size:
            raise MachOError("malformed load command")
        if command == LC_SEGMENT_64:
            if length < 72:
                raise MachOError("malformed segment")
            name = data[offset + 8:offset + 24].split(b"\0", 1)[0].decode("ascii", "replace")
            if name in segments:
                raise MachOError(f"duplicate segment {name}")
            segments[name] = Segment(u64(offset + 24), u64(offset + 32), u64(offset + 40), u64(offset + 48))
        elif command == LC_UNIXTHREAD:
            if length < 16 + 34 * 8 or u32(offset + 8) != ARM_THREAD_STATE64:
                raise MachOError("malformed entry thread")
            entry = u64(offset + 16 + 32 * 8)
        offset += length
    if offset != 32 + size:
        raise MachOError("load command sizes disagree")
    boot = segments.get("__TEXT_BOOT_EXEC")
    if boot is not None and (boot.vmsize or boot.filesize):
        raise MachOError("it has __TEXT_BOOT_EXEC, so iBoot would start SPTM")
    for name in REQUIRED:
        if name not in segments:
            raise MachOError(f"it has no {name} segment")
    base = segments["__TEXT"].vmaddr
    if segments["__TEXT"].fileoff != 0:
        raise MachOError("__TEXT is not at offset 0")
    for name, segment in segments.items():
        if not segment.vmsize:
            continue
        if (segment.vmaddr < base or segment.fileoff != segment.vmaddr - base
                or segment.filesize != segment.vmsize
                or segment.fileoff > len(data) or segment.filesize > len(data) - segment.fileoff):
            raise MachOError(f"{name} is not file-backed at its virtual offset")
    code = segments["__TEXT_EXEC"]
    if entry is None or not code.vmaddr <= entry < code.vmaddr + code.vmsize:
        raise MachOError("its entry point is outside __TEXT_EXEC")
    return segments, entry


PAGE = 0x4000
# Recovery's AuraStage1.swift (ExecuteWindow, MachOBootObject.expandExecuteWindow).
RAW_ENTRY_OFFSET = 0x800
LOAD_HEADER_OFFSET = 0x808
AURA_SLOT = 16 << 20
EXECUTE_WINDOW = 128 << 20
WINDOW_ALIGNMENT = 2 << 20
LC_SYMTAB, LC_DYSYMTAB, LC_UUID, LC_BUILD_VERSION = 0x2, 0xB, 0x1B, 0x32
LC_DYLD_CHAINED_FIXUPS, LC_FILESET_ENTRY = 0x80000034, 0x80000035


def check_execute_window(data, window=EXECUTE_WINDOW, slot=AURA_SLOT):
    """MachOError unless Recovery's expandExecuteWindow can pad DATA's __TEXT_EXEC.

    A check-only walk of the same rules, so an object Recovery would refuse after
    partitioning is refused before any disk change: entered at aura's entry point,
    __TEXT_EXEC starting with aura's load header and fitting the slot kept for it,
    page-aligned, fileset members before __TEXT_EXEC, no chained fixups or symbol
    table, and only load commands Recovery knows how to move.
    """
    data = bytes(data)
    segments, entry = layout(data)

    def u32(offset):
        if offset < 0 or offset + 4 > len(data):
            raise MachOError("truncated")
        return struct.unpack_from("<I", data, offset)[0]

    def u64(offset):
        return u32(offset) | u32(offset + 4) << 32

    code = segments["__TEXT_EXEC"]
    if entry != code.vmaddr + RAW_ENTRY_OFFSET:
        raise MachOError("it does not enter aura at its entry point")
    start = code.fileoff
    if start + 0x820 > len(data) or data[start + LOAD_HEADER_OFFSET:start + LOAD_HEADER_OFFSET + 8] != b"AURALOAD":
        raise MachOError("__TEXT_EXEC does not start with aura")
    if u64(start + LOAD_HEADER_OFFSET + 16) > slot or code.vmsize > slot:
        raise MachOError("aura does not fit the room kept for it")
    growth = slot + (WINDOW_ALIGNMENT - PAGE) + window - code.vmsize
    code_end = code.vmaddr + code.vmsize
    file_end = code.fileoff + code.filesize
    if code_end > 0xFFFFFFFFFFFFFFFF or file_end > 0xFFFFFFFFFFFFFFFF:
        raise MachOError("an address or offset is out of range")
    if file_end % PAGE or code.vmaddr % PAGE:
        raise MachOError("__TEXT_EXEC is not page-aligned")

    def movable32(offset):
        if offset + growth > 0xFFFFFFFF:
            raise MachOError("its execute window is too large")

    def movable64(value):
        if value + growth > 0xFFFFFFFFFFFFFFFF:
            raise MachOError("an address or offset is out of range")

    # Recovery re-reads the padded object, whose load commands must still end
    # before the bytes it moves.
    if 32 + u32(20) > file_end:
        raise MachOError("load commands are truncated")

    headers, index = [0], 0
    while index < len(headers):
        if len(set(headers)) != len(headers):
            raise MachOError("a fileset member repeats")
        header = headers[index]
        index += 1
        if u32(header) != MH_MAGIC_64:
            raise MachOError("a fileset member has no Mach-O header")
        offset = header + 32
        for _ in range(u32(header + 16)):
            command, length = u32(offset), u32(offset + 4)
            if length < 8 or offset + length > len(data):
                raise MachOError("malformed load command")
            if command == LC_SEGMENT_64:
                if length < 72:
                    raise MachOError("malformed segment")
                vmaddr, vmsize = u64(offset + 24), u64(offset + 32)
                name = data[offset + 8:offset + 24].split(b"\0", 1)[0].decode("utf-8", "replace")
                if name == "__TEXT_EXEC":
                    if (vmaddr, vmsize) != (code.vmaddr, code.vmsize):
                        raise MachOError("two headers disagree about __TEXT_EXEC")
                elif vmaddr >= code_end:
                    movable64(vmaddr)
                    movable64(u64(offset + 40))
                    sections = u32(offset + 64)
                    if length != 72 + 80 * sections:
                        raise MachOError("malformed segment")
                    for section in range(sections):
                        movable64(u64(offset + 72 + 80 * section + 32))
                        file_offset = u32(offset + 72 + 80 * section + 48)
                        if file_offset:
                            movable32(file_offset)
                elif vmaddr + vmsize > code.vmaddr:
                    raise MachOError(f"{name} overlaps __TEXT_EXEC")
            elif command == LC_DYLD_CHAINED_FIXUPS:
                fixups = u32(offset + 8)
                starts = fixups + u32(fixups + 4)
                if any(u32(starts + 4 + 4 * segment) for segment in range(u32(starts))):
                    raise MachOError("it has chained fixups")
                if fixups >= file_end:
                    movable32(fixups)
            elif command == LC_FILESET_ENTRY:
                if not (u64(offset + 8) < code.vmaddr and u64(offset + 16) < code.fileoff):
                    raise MachOError("a fileset member is not before __TEXT_EXEC")
                headers.append(u64(offset + 16))
            elif command in (LC_SYMTAB, LC_DYSYMTAB):
                if any(data[offset + 8:offset + length]):
                    raise MachOError("it has a symbol table")
            elif command not in (LC_UNIXTHREAD, LC_UUID, LC_BUILD_VERSION):
                raise MachOError("it has a load command that cannot be moved")
            offset += length


def read_only_size(data):
    """The kcrz kmutil should derive: the load address to the end of __TEXT_EXEC."""
    code = layout(data)[0]["__TEXT_EXEC"]
    return code.fileoff + code.vmsize


def text_exec_range(data):
    """The file range of __TEXT_EXEC, where aura's boot object carries its raw Stage 1."""
    code = layout(data)[0]["__TEXT_EXEC"]
    return code.fileoff, code.fileoff + code.filesize
