# SPDX-License-Identifier: MIT
"""BCM4388 rev C2 Wi-Fi for M4-family Macs.

A port of aurora-silicon/aurora-installer 34f90a5 (Ryan Murray).

Upstream asahi_firmware.wifi drops the whole C-4388__s-C2 directory, a rule
for a broken 14.6.1 IPSW that cost M1 to M3 nothing. That directory is the
Wi-Fi firmware of the M4-family Macs (BCM4388 rev C2). Even without the rule,
26.6.2 (25G83) Recovery has no usable C2 firmware: its C2 directory holds
aruba NVRAM and regulatory data alone, and its aruba.trx names a paradise.trx
the image does not ship. The installed macOS ships every board's firmware on
its sealed, read-only system volume.

So the collection converts C2 like any other directory and skips a dangling
alias by itself instead of the directory around it. The stub collects Wi-Fi
from the running system's /usr/share/firmware/wifi when that tree carries this
Mac's board (the ADT wlan node's module-instance) and Recovery can't serve it:
the board's files sit in the directory upstream skips, or Recovery has no
firmware for it. Otherwise, as on M1 to M3, it collects Recovery's set as
before; upstream takes the installed system's there too. The fallback archive
then keeps the tree the package came from, so a re-extraction on Linux gives
the same set.

The refusal is keyed on what the Mac presents. When the ADT has a Broadcom
wlan board and that board's files sit in the directory upstream skips, in
Recovery or in the installed macOS, a package without the board's Wi-Fi or
Bluetooth is refused rather than installing a Mac without radios; so is a
Broadcom board no source carries at all. The ADT names the board but not the
chip revision (an M4's wlan function says wlan-pcie,bcm4387 for its BCM4388),
so the chip comes from the directories holding the board's files.
"""
import contextlib
import logging
import os
import plistlib
import re
import shutil
import subprocess
import tempfile

log = logging.getLogger(__name__)

SYSTEM_WIFI = "/usr/share/firmware/wifi"
FALLBACK_ARCHIVE = ["tar", "czf", "all_firmware.tar.gz"]
RECOVERY_FIRMWARE = ["-C", "recovery/usr/share", "firmware"]

# The antenna SKUs Apple ships a Wi-Fi board's aliases for. brcmfmac tries
# <board>-<sku> before <board> and the chip's generic name.
ANTENNA_SKUS = ("ID", "X0", "X2", "X3")

# The directory upstream's load skips (the rule collection_class drops), and
# the chip name its files get in the package.
UPSTREAM_SKIPPED = "C-4388__s-C2"
SKIPPED_CHIP = "4388c2"

# The ADT's wlan PCIe function is compatible with wlan-pcie,bcm<chip> and
# wlan-pcie,bcm on every Broadcom Mac.
BROADCOM_WLAN = "wlan-pcie,bcm"

_BOARD = re.compile(r"[a-z][a-z0-9]{0,31}")


class WiFiError(Exception):
    pass


def collection_class(upstream):
    """upstream.WiFiFWCollection, converting C2 like any other directory."""

    class WiFiFWCollection(upstream.WiFiFWCollection):
        def load(self, source_path):
            # Upstream's load, without the C-4388__s-C2 rule and with a
            # dangling alias skipped by itself.
            for dirpath, dirnames, filenames in os.walk(source_path):
                if "perf" in dirnames:
                    dirnames.remove("perf")
                if "assert" in dirnames:
                    dirnames.remove("assert")
                subpath = os.path.relpath(dirpath, source_path)
                for name in sorted(filenames):
                    if not any(name.endswith("." + i) for i in self.EXTMAP):
                        continue
                    # macOS 14.6/14.8 contains {java,sumatra}_gen* files, as
                    # upstream skips them.
                    if "_gen" in name:
                        continue
                    path = os.path.join(dirpath, name)
                    relpath = os.path.join(subpath, name)
                    # Some Recovery images ship aliases whose target is absent
                    # (25G83's C-4388__s-C2/aruba.trx names a missing
                    # paradise.trx). Such a link carries no firmware: skip it,
                    # like a file the image lacks, and keep the rest of its
                    # directory.
                    if not os.path.exists(path):
                        log.info(f"Skipping dangling firmware alias {relpath}")
                        continue
                    if not name.endswith(".txt"):
                        name = "P-" + name
                    idpath, ext = os.path.join(subpath, name).rsplit(".", 1)
                    props = {}
                    for i in idpath.replace("/", "_").split("_"):
                        if not i:
                            continue
                        k, v = i.split("-", 1)
                        if k == "P" and "-" in v:
                            plat, ant = v.split("-", 1)
                            props["P"] = plat
                            props["A"] = ant
                        else:
                            props[k] = v
                    ident = [ext]
                    for dim in self.DIMS:
                        if dim in props:
                            ident.append(props.pop(dim))
                    assert not props

                    node = self.root
                    for k in ident:
                        node = node.leaves.setdefault(k, upstream.FWNode())
                    with open(path, "rb") as fd:
                        data = fd.read()

                    if name.endswith(".txt"):
                        data = self.process_nvram(data)

                    node.this = upstream.FWFile(relpath, data)

    return WiFiFWCollection


def admitted(value):
    """A board name is a short lowercase word, as every Apple board is."""
    text = bytes(value).split(b"\0", 1)[0].decode("utf-8", "replace")
    return text if _BOARD.fullmatch(text) else None


def broadcom(value):
    """Whether an ADT compatible value names a Broadcom wlan function."""
    if isinstance(value, str):
        value = value.encode()
    if not isinstance(value, bytes):
        return False
    return any(entry.startswith(BROADCOM_WLAN.encode()) for entry in value.split(b"\0"))


def board(run=subprocess.run):
    """This Mac's Broadcom Wi-Fi board: the ADT wlan node's module-instance
    (the name Linux gets as brcm,board-type) when the ADT also has a Broadcom
    wlan function. None when the ADT has no Broadcom wlan board, as on the
    MacBook Neo, or its name is unusable. WiFiError when the device tree
    cannot be read: that is not a Mac without a Broadcom board."""
    try:
        output = run(
            ["/usr/sbin/ioreg", "-a", "-r", "-p", "IODeviceTree", "-n", "wlan", "-d", "1"],
            check=True,
            capture_output=True,
        ).stdout
        found = plistlib.loads(output) if output.strip() else []
    except (OSError, subprocess.CalledProcessError, plistlib.InvalidFileException, ValueError) as error:
        raise WiFiError(f"cannot read the device tree's wlan node: {error}") from error
    nodes = [node for node in (found if isinstance(found, list) else [found]) if isinstance(node, dict)]
    if not any(broadcom(node.get("compatible")) for node in nodes):
        return None
    for node in nodes:
        value = node.get("module-instance")
        if isinstance(value, bytes):
            return admitted(value)
    return None


def system_tree(path=SYSTEM_WIFI):
    """The running system's Wi-Fi tree, read only from a read-only volume."""
    try:
        if not os.path.isdir(path) or not os.statvfs(path).f_flag & os.ST_RDONLY:
            return None
    except OSError:
        return None
    return path


def converted(collection):
    """{package name: bytes} of a collection."""
    return {name: fwfile.data for name, fwfile in collection.files()}


def carries_wifi(outputs, board):
    """Whether a converted Wi-Fi set carries firmware for this board."""
    return any(
        data
        and name.startswith("brcm/brcmfmac")
        and name.endswith(".bin")
        and ("-pcie.apple," + board + "." in name or "-pcie.apple," + board + "-" in name)
        for name, data in outputs.items()
    )


def board_chips(outputs, board):
    """The chips whose directories hold this board's files in a converted
    set, any file type: {"4388c2", ...}. A board can sit under more than one
    revision of a chip."""
    pattern = re.compile(r"brcm/brcmfmac([0-9a-z]+)-pcie\.apple," + re.escape(board) + r"[-.]")
    return {match.group(1) for match in map(pattern.match, outputs) if match}


def wifi_source(board, system, recovery=None):
    """The installed system when it carries this board and Recovery can't
    serve it: the board sits in the directory upstream skips, or Recovery has
    no firmware for it. Recovery otherwise, and when the board is unknown.
    Without Recovery's set, the installed system whenever it carries the
    board."""
    if not board or system is None or not carries_wifi(system, board):
        return "recovery"
    if recovery is not None and carries_wifi(recovery, board):
        if SKIPPED_CHIP not in board_chips(system, board) | board_chips(recovery, board):
            return "recovery"
    return "system"


def missing_wifi(outputs, chip, board):
    """The package names a board's Wi-Fi still lacks: firmware (.bin),
    regulatory data (.clm_blob) and NVRAM (.txt), for the board itself and
    each antenna SKU, resolved the way brcmfmac resolves them. An empty result
    means Linux can bring the radio up on every SKU of that board."""
    base = "brcm/brcmfmac" + chip + "-pcie.apple"
    named = {name for name, data in outputs.items() if data}

    def nvram(stem, sku_specific):
        # NVRAM names carry the module and vendor after the board (and SKU).
        # A board-wide name must not be another SKU's.
        for name in named:
            if not name.endswith(".txt"):
                continue
            if name == stem + ".txt":
                return True
            if not name.startswith(stem + "-"):
                continue
            if sku_specific:
                return True
            if re.split(r"[-.]", name[len(stem) + 1:], 1)[0] not in ANTENNA_SKUS:
                return True
        return False

    missing = []
    for sku in ("",) + ANTENNA_SKUS:
        stems = ([base + "," + board + "-" + sku] if sku else []) + [base + "," + board]
        for ext in ("bin", "clm_blob"):
            candidates = [stem + "." + ext for stem in stems] + [base + "." + ext]
            if not any(candidate in named for candidate in candidates):
                missing.append(candidates[0])
        present = (
            (bool(sku) and nvram(stems[0], True))
            or nvram(base + "," + board, False)
            or base + ".txt" in named
        )
        if not present:
            missing.append(stems[0] + ".txt")
    return missing


def missing_bluetooth(outputs, chip, board):
    """The Bluetooth names a board still lacks: firmware and its patch table
    (.bin and .ptb) for at least one radio module vendor."""
    stem = "brcm/brcmbt" + chip + "-apple," + board
    for vendor in ("-a", "-m", "-u", ""):
        if all(outputs.get(stem + vendor + "." + ext) for ext in ("bin", "ptb")):
            return []
    return [stem + "-u.bin", stem + "-u.ptb"]


def copy_contents(source, destination, skip=()):
    """shutil.copytree(source, destination, symlinks=True), with SKIP's names left
    out of SOURCE's top level, copying contents and permission bits only.

    copytree's copy2 and copystat also set each file's and directory's flags,
    times and extended attributes. On a J616s on macOS 26.6.2, setting the
    recovery ramdisk's flags on the staged copies fails with EPERM on every
    file under usr/share/firmware/bluetooth and on the directories, which
    stopped the whole collection. The archive needs only the bytes."""
    os.makedirs(destination)
    for name in sorted(os.listdir(source)):
        if name in skip:
            continue
        path, target = os.path.join(source, name), os.path.join(destination, name)
        if os.path.islink(path):
            os.symlink(os.readlink(path), target)
        elif os.path.isdir(path):
            copy_contents(path, target)
        else:
            shutil.copyfile(path, target)
            shutil.copymode(path, target)


class _SystemWiFiArchive:
    """subprocess for stub: when Wi-Fi came from the installed system, the
    fallback archive carries that tree as firmware/wifi."""

    def __init__(self, module, radios):
        self._module, self._radios = module, radios

    def __getattr__(self, name):
        return getattr(self._module, name)

    def run(self, command, *args, **kwargs):
        tree = self._radios.system
        if tree is None or list(command[:3]) != FALLBACK_ARCHIVE:
            return self._module.run(command, *args, **kwargs)
        command = list(command)
        at = next((i for i in range(len(command) - 2) if command[i:i + 3] == RECOVERY_FIRMWARE), None)
        if at is None:
            raise WiFiError("the fallback archive does not take Recovery's firmware tree")
        recovery = os.path.join(*RECOVERY_FIRMWARE[1:])
        with tempfile.TemporaryDirectory(prefix="omarchy-wifi-") as staging:
            firmware = os.path.join(staging, "firmware")
            copy_contents(recovery, firmware, skip=("wifi",))
            copy_contents(tree, os.path.join(firmware, "wifi"))
            # Later -C arguments are relative to Recovery's directory: return
            # there after the staged tree.
            command[at:at + 3] = ["-C", staging, "firmware", "-C", os.path.abspath(RECOVERY_FIRMWARE[1])]
            return self._module.run(command, *args, **kwargs)


class Radios:
    """What one firmware collection took for the radios."""

    def __init__(self, machine):
        self.machine = machine
        self.board = None
        self.wifi = None
        self.bluetooth = None
        self.system = None
        # Every converted Wi-Fi set read: Recovery's, and the installed
        # system's when it was readable.
        self.sources = []
        # A failed board lookup, refused once the collection is done.
        self.error = None

    def require(self):
        """A Mac whose ADT names a Broadcom Wi-Fi board must get firmware for
        it from some source, and when that board's files sit in the directory
        upstream skips, the package must bring up its radios. A board that
        could not be looked up is refused, never taken as no Broadcom board."""
        if self.error is not None:
            raise self.error
        if self.board is None:
            return
        chips = set()
        for outputs in self.sources:
            chips |= board_chips(outputs, self.board)
        if not chips:
            raise WiFiError(
                f"{self.machine}: neither Recovery nor the installed macOS has firmware "
                f"for the device tree's Wi-Fi board {self.board}"
            )
        if SKIPPED_CHIP not in chips:
            return
        missing = missing_wifi(self.wifi or {}, SKIPPED_CHIP, self.board)
        missing += missing_bluetooth(self.bluetooth or {}, SKIPPED_CHIP, self.board)
        if missing:
            raise WiFiError(f"{self.machine}: the firmware package lacks " + ", ".join(missing))


@contextlib.contextmanager
def collectors(stub, upstream, machine, read_board=board, system_path=SYSTEM_WIFI):
    """Rebind stub's Wi-Fi and Bluetooth collections and its subprocess for
    one firmware collection; yield the Radios it records."""
    collection = collection_class(upstream)
    radios = Radios(machine)
    old_wifi, old_bluetooth, old_process = stub.WiFiFWCollection, stub.BluetoothFWCollection, stub.subprocess

    def wifi(source_path):
        recovery = collection(source_path)
        radios.wifi = converted(recovery)
        radios.sources = [radios.wifi]
        try:
            radios.board = read_board()
        except WiFiError as error:
            radios.error = WiFiError(f"{machine}: {error}")
            return recovery
        if radios.board is None:
            # No Broadcom wlan board in the ADT: Recovery's set, as upstream
            # collects it. The MacBook Neo's MT7932 radios are collected on
            # their own.
            return recovery
        system = None
        tree = system_tree(system_path)
        if tree is not None:
            try:
                system = collection(tree)
            except (OSError, ValueError, AssertionError) as error:
                # An unreadable system volume leaves Recovery as the only source.
                log.warning(f"Installed macOS Wi-Fi firmware unreadable: {error}")
        if system is not None:
            radios.sources.append(converted(system))
            if wifi_source(radios.board, radios.sources[-1], radios.wifi) == "system":
                log.info(f"Collecting Wi-Fi for board {radios.board} from the installed macOS")
                radios.wifi, radios.system = radios.sources[-1], tree
                return system
        return recovery

    def bluetooth(source_path):
        result = old_bluetooth(source_path)
        radios.bluetooth = converted(result)
        return result

    try:
        stub.WiFiFWCollection, stub.BluetoothFWCollection = wifi, bluetooth
        stub.subprocess = _SystemWiFiArchive(old_process, radios)
        yield radios
    finally:
        stub.WiFiFWCollection, stub.BluetoothFWCollection, stub.subprocess = old_wifi, old_bluetooth, old_process
