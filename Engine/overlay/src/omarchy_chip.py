# SPDX-License-Identifier: MIT
"""Hardware properties the engine selects by chip, never by a board list.

Whether a chip's CPUs run kernel-mode code only inside a configured execute
window is a property of the CPU generation: M4, A18 Pro, M5 and every later
chip have it (T8132, T6040/T6041, T8140, T8142, T605x...), M1 to M3 (T8103,
T8112, T8122, T600x, T602x, T603x) do not. On such a chip Stage 1 is a Mach-O
padded to cover the window, enrolled with `kmutil configure-boot --compress`,
and it loads the confined Stage 2, m1n1/boot-ctxr.bin.

Chip IDs rise with each generation, so the rule is one range per chip family.
The ranges are scripts/stage1-execute-window.json, the single definition the
image builder and the native Recovery also follow; test_omarchy_chip fails if
this copy drifts from it. It is a constant here so the engine archive needs no
data file.

Ported from aurora-silicon/aurora-installer (7be5339, MIT): AuraStage1Format
.usesExecuteWindow (macOS/Recovery/Sources/Account/AuraStage1.swift) and
prores.chip() (asahi_firmware/prores.py), both by Ryan Murray.
"""

import re

EXECUTE_WINDOW_CHIP_IDS = ((0x8130, 0x8FFF), (0x6040, 0x6FFF))

_COMPATIBLE_CHIP = re.compile(rb"apple,t([0-9a-f]{4})")


def uses_execute_window(chip_id):
    """Whether CHIP_ID's CPUs confine kernel code to an execute window."""
    if not isinstance(chip_id, int) or isinstance(chip_id, bool):
        return False
    return any(first <= chip_id <= last for first, last in EXECUTE_WINDOW_CHIP_IDS)


def chip_from_compatible(compatible):
    """The chip ID named by a device tree root compatible (NUL-separated
    bytes, as /proc/device-tree/compatible), or None."""
    if not isinstance(compatible, (bytes, bytearray)):
        return None
    for entry in bytes(compatible).split(b"\0"):
        found = _COMPATIBLE_CHIP.fullmatch(entry)
        if found:
            return int(found.group(1), 16)
    return None
