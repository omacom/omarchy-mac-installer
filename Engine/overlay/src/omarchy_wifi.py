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
Mac's board (the ADT wlan node's module-instance), and from Recovery
otherwise. The fallback archive then keeps the tree the package came from, so
a re-extraction on Linux gives the same set. On the M4 Pro and M4 Max laptops
a package without its board's Wi-Fi or Bluetooth is refused rather than
installing a Mac without radios.
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

# M4 Pro and M4 Max laptops and the Wi-Fi board of their BCM4388 rev C2 (the
# ADT wlan node's module-instance). The M4 Max boards share their M4 Pro
# sibling's board, as every M1 to M3 Pro/Max pair does.
M4_PRO_MAX_WIFI = {
    "j614s": ("4388c2", "mriya"),
    "j614c": ("4388c2", "mriya"),
    "j616s": ("4388c2", "hostomel"),
    "j616c": ("4388c2", "hostomel"),
}

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


def board(run=subprocess.run):
    """This Mac's Wi-Fi board, the ADT wlan node's module-instance (the name
    Linux gets as brcm,board-type), or None when no such node exists."""
    try:
        output = run(
            ["/usr/sbin/ioreg", "-a", "-r", "-p", "IODeviceTree", "-n", "wlan", "-d", "1"],
            check=True,
            capture_output=True,
        ).stdout
        found = plistlib.loads(output) if output.strip() else []
    except (OSError, subprocess.CalledProcessError, plistlib.InvalidFileException, ValueError):
        return None
    for node in found if isinstance(found, list) else [found]:
        value = node.get("module-instance") if isinstance(node, dict) else None
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


def wifi_source(board, system):
    """Prefer the installed system, and use Recovery only when it alone has
    this board's firmware or the board is unknown."""
    if not board or system is None or not carries_wifi(system, board):
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
            shutil.copytree(
                recovery,
                firmware,
                symlinks=True,
                ignore=lambda directory, names: ["wifi"] if os.path.samefile(directory, recovery) else [],
            )
            shutil.copytree(tree, os.path.join(firmware, "wifi"), symlinks=True)
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

    def require(self):
        """An M4 Pro or M4 Max package must bring up its own board's radios."""
        required = M4_PRO_MAX_WIFI.get(self.machine)
        if required is None:
            return
        chip = required[0]
        if self.board is None:
            raise WiFiError(f"{self.machine}: the device tree names no Wi-Fi board")
        missing = missing_wifi(self.wifi or {}, chip, self.board)
        missing += missing_bluetooth(self.bluetooth or {}, chip, self.board)
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
        if machine == "j700":
            # The MacBook Neo's MT7932 radios are collected on their own.
            return recovery
        radios.board = read_board()
        system = None
        tree = system_tree(system_path)
        if tree is not None:
            try:
                system = collection(tree)
            except (OSError, ValueError, AssertionError) as error:
                # An unreadable system volume leaves Recovery as the only source.
                log.warning(f"Installed macOS Wi-Fi firmware unreadable: {error}")
        if system is not None and wifi_source(radios.board, converted(system)) == "system":
            log.info(f"Collecting Wi-Fi for board {radios.board} from the installed macOS")
            radios.wifi, radios.system = converted(system), tree
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
