# SPDX-License-Identifier: MIT
"""A minimal aura-shaped Mach-O fileset boot object for engine tests: the raw
Stage 1 at the start of __TEXT_EXEC, entered at its 0x800 entry point, every
segment file-backed at its virtual offset."""
import struct

PAGE = 0x4000
BASE = 0xFFFFFE0007004000


def segment(name, vmaddr, vmsize, fileoff, filesize):
    return struct.pack("<II16sQQQQiiII", 0x19, 72, name.encode(), vmaddr, vmsize, fileoff, filesize, 5, 5, 0, 0)


def thread(pc):
    registers = [0] * 34
    registers[32] = pc
    return struct.pack("<IIII", 0x5, 16 + 34 * 8, 6, 68) + struct.pack("<34Q", *registers)


def macho_stage1(raw, *, entry_offset=0x800, extra_segments=(), filetype=12, text_exec_bytes=2 * PAGE,
                 load_header=b"AURALOAD", load_size=2 * PAGE, commands_extra=b""):
    """RAW at the start of __TEXT_EXEC of a fileset boot object, with aura's load
    header (start.S: "AURALOAD" at 0x808, the memory aura needs at 0x818) after it
    unless LOAD_HEADER is None. COMMANDS_EXTRA is appended to the load commands."""
    assert len(raw) <= text_exec_bytes and (load_header is None or len(raw) <= 0x808)
    layout = [("__TEXT", PAGE), ("__TEXT_EXEC", text_exec_bytes), ("__PRELINK_INFO", PAGE),
              ("__LINKEDIT", PAGE)] + list(extra_segments)
    commands, offset = b"", 0
    for name, size in layout:
        commands += segment(name, BASE + offset, size, offset, size)
        offset += size
    commands += thread(BASE + PAGE + entry_offset)
    count = len(layout) + 1 + (1 if commands_extra else 0)
    commands += commands_extra
    header = struct.pack("<IIIIIIII", 0xFEEDFACF, 0x0100000C, 0, filetype, count, len(commands), 0, 0)
    data = bytearray(offset)
    data[:len(header) + len(commands)] = header + commands
    data[PAGE:PAGE + len(raw)] = raw
    if load_header is not None:
        data[PAGE + 0x808:PAGE + 0x820] = load_header + bytes(8) + struct.pack("<Q", load_size)
    return bytes(data)
