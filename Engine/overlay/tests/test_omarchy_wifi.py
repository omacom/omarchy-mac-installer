# SPDX-License-Identifier: MIT
"""BCM4388 rev C2 Wi-Fi collection from 26.6.2 (25G83)-shaped trees.

Recovery's C-4388__s-C2 holds only aruba NVRAM and regulatory data, and its
aruba.trx names a paradise.trx the image does not ship. The installed system
volume holds every board, with hostomel.trx and mriya.trx naming guam.trx.
Fixtures after aurora-silicon/aurora-installer 34f90a5; synthetic bytes only.
"""
import contextlib
import importlib
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import omarchy_wifi as wifi  # noqa: E402

SKUS = ("ID", "X0", "X2", "X3")
FIRMWARE = b"synthetic C2 firmware"


class FWFile:
    def __init__(self, name, data):
        self.name, self.data = name, data


class FWNode:
    def __init__(self):
        self.this = None
        self.leaves = {}

    def __eq__(self, other):
        return self.this == other.this and self.leaves == other.leaves


class UpstreamWiFiFWCollection:
    """The part of asahi_firmware.wifi.WiFiFWCollection the overlay keeps:
    construction, the conversion tables, pruning and upstream's package
    names."""

    EXTMAP = {"trx": "bin", "txt": "txt", "clmb": "clm_blob", "txcb": "txcap_blob", "sig": "sig"}
    DIMS = ["C", "s", "P", "M", "V", "m", "A"]

    def __init__(self, source_path):
        self.root = FWNode()
        self.load(source_path)
        self.prune()

    def load(self, source_path):
        # The upstream rule the overlay replaces.
        raise AssertionError("the overlay collection must replace load")

    def process_nvram(self, data):
        return data

    def prune(self, node=None, depth=0):
        if node is None:
            node = self.root
        for i in node.leaves.values():
            self.prune(i, depth + 1)
        if node.this is None and node.leaves and depth > 3:
            first = next(iter(node.leaves.values()))
            if all(i == first for i in node.leaves.values()):
                node.this = first.this
        for i in node.leaves.values():
            if not i.this or not node.this:
                break
            if i.this != node.this:
                break
        else:
            node.leaves = {}

    def _walk_files(self, node, ident):
        if node.this is not None:
            yield ident, node.this
        for k, subnode in node.leaves.items():
            yield from self._walk_files(subnode, ident + [k])

    def files(self):
        for ident, fwfile in self._walk_files(self.root, []):
            (ext, chip, rev), rest = ident[:3], ident[3:]
            rest = "," + "-".join(rest) if rest else ""
            yield f"brcm/brcmfmac{chip}{rev.lower()}-pcie.apple{rest}.{self.EXTMAP[ext]}", fwfile


UPSTREAM = SimpleNamespace(FWFile=FWFile, FWNode=FWNode, WiFiFWCollection=UpstreamWiFiFWCollection)


def recovery_25g83(root):
    c2 = root / "C-4388__s-C2"
    c2.mkdir(parents=True)
    for vendor in ("a", "u"):
        name = f"P-aruba_M-WLMT_V-{vendor}__m-4.9.txt"
        (c2 / name).write_bytes(b"boardrev=0x1\n")
        for sku in SKUS:
            os.symlink(name, c2 / f"P-aruba-{sku}_M-WLMT_V-{vendor}__m-4.9.txt")
    (c2 / "aruba.clmb").write_bytes(b"aruba clm")
    os.symlink("paradise.trx", c2 / "aruba.trx")
    for sku in SKUS:
        os.symlink("aruba.trx", c2 / f"aruba-{sku}.trx")
        os.symlink("aruba.clmb", c2 / f"aruba-{sku}.clmb")


def installed_system(root, boards=("hostomel", "mriya")):
    c2 = root / "C-4388__s-C2"
    c2.mkdir(parents=True)
    (c2 / "guam.trx").write_bytes(FIRMWARE)
    for board in boards:
        os.symlink("guam.trx", c2 / f"{board}.trx")
        (c2 / f"{board}.clmb").write_bytes(f"{board} clm".encode())
        for vendor in ("a", "u"):
            name = f"P-{board}_M-WLMT_V-{vendor}__m-4.9.txt"
            (c2 / name).write_bytes(f"vendor={vendor}\n".encode())
            for sku in SKUS:
                os.symlink(name, c2 / f"P-{board}-{sku}_M-WLMT_V-{vendor}__m-4.9.txt")
        for sku in SKUS:
            os.symlink(f"{board}.trx", c2 / f"{board}-{sku}.trx")
            os.symlink(f"{board}.clmb", c2 / f"{board}-{sku}.clmb")


def bluetooth(board, chip="4388c2"):
    stem = f"brcm/brcmbt{chip}-apple,{board}-u"
    return {stem + ".bin": b"bt", stem + ".ptb": b"ptb"}


class C2Collection:
    """Ryan Murray's two Python checks, for any upstream module."""

    upstream = UPSTREAM

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def collect(self, root):
        return wifi.converted(wifi.collection_class(self.upstream)(str(root)))

    def test_recovery_dangling_alias_is_skipped_not_fatal(self):
        recovery_25g83(self.root)
        files = self.collect(self.root)
        self.assertFalse([n for n in files if n.endswith(".bin")], files.keys())
        self.assertIn("brcm/brcmfmac4388c2-pcie.apple,aruba.clm_blob", files)

    def test_installed_system_c2_directory_is_collected(self):
        installed_system(self.root)
        files = self.collect(self.root)
        for board in ("hostomel", "mriya"):
            prefix = f"brcm/brcmfmac4388c2-pcie.apple,{board}"
            self.assertEqual(files[prefix + ".bin"], FIRMWARE)
            self.assertIn(prefix + ".clm_blob", files)
            self.assertIn(prefix + "-WLMT-a.txt", files)
            self.assertIn(prefix + "-WLMT-u.txt", files)


class C2CollectionTests(C2Collection, unittest.TestCase):
    pass


@unittest.skipUnless(
    os.environ.get("OMARCHY_ASAHI_INSTALLER"),
    "set OMARCHY_ASAHI_INSTALLER to an asahi-installer checkout at the locked commit",
)
class UpstreamC2CollectionTests(C2Collection, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, os.environ["OMARCHY_ASAHI_INSTALLER"])
        cls.upstream = importlib.import_module("asahi_firmware.wifi")

    def test_upstream_alone_drops_c2(self):
        installed_system(self.root)
        files = {name: fw.data for name, fw in self.upstream.WiFiFWCollection(str(self.root)).files()}
        self.assertFalse([n for n in files if "4388c2" in n], files.keys())

    def test_package_names_match_upstream_outside_c2(self):
        c3 = self.root / "C-4387__s-C3"
        c3.mkdir()
        (c3 / "kanaha.trx").write_bytes(b"c3 firmware")
        (c3 / "kanaha.clmb").write_bytes(b"c3 clm")
        (c3 / "P-kanaha_M-RASP_V-m__m-7.9.txt").write_bytes(b"x=1\n")
        upstream = {name: fw.data for name, fw in self.upstream.WiFiFWCollection(str(self.root)).files()}
        self.assertEqual(self.collect(self.root), upstream)


class BoardTests(unittest.TestCase):
    def ioreg(self, nodes):
        output = plistlib.dumps(nodes, fmt=plistlib.FMT_XML)
        return lambda command, **kwargs: SimpleNamespace(stdout=output)

    # The ADT's wlan PCIe function as an M4 presents it (bcm4387 for its
    # BCM4388) and as an M1 does.
    M4_FUNCTION = {"name": b"wlan\0", "compatible": b"wlan-pcie,bcm4387\0wlan-pcie,bcm\0"}
    M1_FUNCTION = {"name": b"wlan\0", "compatible": b"wlan-pcie,bcm4378\0wlan-pcie,bcm\0"}

    def test_reads_the_wlan_module_instance(self):
        nodes = [{"name": b"wlan\0", "module-instance": b"guam\0"}, self.M4_FUNCTION, self.M4_FUNCTION]
        self.assertEqual(wifi.board(self.ioreg(nodes)), "guam")
        nodes = [self.M1_FUNCTION, {"name": b"wlan\0"}, {"module-instance": b"shikoku\0"}]
        self.assertEqual(wifi.board(self.ioreg(nodes)), "shikoku")

    def test_no_board_without_a_broadcom_wlan_function(self):
        # A wlan node with a board name but no Broadcom function is not a
        # Broadcom board (the MacBook Neo's radios are MediaTek).
        self.assertIsNone(wifi.board(self.ioreg([{"name": b"wlan\0", "module-instance": b"guam\0"}])))
        other = {"name": b"wlan\0", "compatible": b"wlan-pcie,mt7932\0"}
        self.assertIsNone(wifi.board(self.ioreg([{"module-instance": b"guam\0"}, other])))
        self.assertFalse(wifi.broadcom(b"wlan-pcie,bc\0"))
        self.assertTrue(wifi.broadcom("wlan-pcie,bcm4388"))

    def test_no_board_without_a_usable_name(self):
        self.assertIsNone(wifi.board(self.ioreg([])))
        self.assertIsNone(wifi.board(lambda command, **kwargs: SimpleNamespace(stdout=b"")))
        self.assertIsNone(wifi.board(self.ioreg([self.M4_FUNCTION])))
        for value in (b"Guam", b"../guam", b"", b"a" * 33, b"guam x"):
            self.assertIsNone(wifi.board(self.ioreg([{"module-instance": value}, self.M4_FUNCTION])), value)

    def test_a_failed_lookup_is_not_a_missing_board(self):
        # ioreg failing or answering garbage says nothing about the radio.
        def fails(command, **kwargs):
            raise subprocess.CalledProcessError(1, command)

        def missing(command, **kwargs):
            raise FileNotFoundError(command[0])

        def garbage(command, **kwargs):
            return SimpleNamespace(stdout=b"not a property list")

        for run in (fails, missing, garbage):
            with self.subTest(run=run.__name__), \
                    self.assertRaisesRegex(wifi.WiFiError, "cannot read the device tree's wlan node"):
                wifi.board(run)

    def test_asks_the_device_tree_for_the_wlan_node(self):
        seen = []

        def run(command, **kwargs):
            seen.append(command)
            return SimpleNamespace(stdout=b"")

        wifi.board(run)
        self.assertEqual(seen, [["/usr/sbin/ioreg", "-a", "-r", "-p", "IODeviceTree", "-n", "wlan", "-d", "1"]])


class SourceTests(unittest.TestCase):
    def test_prefers_the_system_only_when_it_carries_the_board(self):
        system = {"brcm/brcmfmac4388c2-pcie.apple,mriya.bin": FIRMWARE}
        self.assertEqual(wifi.wifi_source("mriya", system), "system")
        self.assertEqual(wifi.wifi_source("hostomel", system), "recovery")
        self.assertEqual(wifi.wifi_source(None, system), "recovery")
        self.assertEqual(wifi.wifi_source("mriya", None), "recovery")
        self.assertEqual(wifi.wifi_source("mriya", {"brcm/brcmfmac4388c2-pcie.apple,mriya.bin": b""}), "recovery")
        self.assertTrue(wifi.carries_wifi({"brcm/brcmfmac4388c2-pcie.apple,mriya-X0.bin": b"x"}, "mriya"))

    def test_recovery_keeps_a_board_it_serves_outside_the_c2_directory(self):
        # M1 to M3: the installed system carries every board, Recovery too.
        system = {"brcm/brcmfmac4387c3-pcie.apple,kanaha.bin": b"newer"}
        recovery = {"brcm/brcmfmac4387c3-pcie.apple,kanaha.bin": b"recovery"}
        self.assertEqual(wifi.wifi_source("kanaha", system, recovery), "recovery")
        self.assertEqual(wifi.wifi_source("kanaha", system, {}), "system")
        self.assertEqual(wifi.wifi_source("kanaha", system, {"brcm/brcmfmac4387c3-pcie.apple,kanaha.bin": b""}), "system")
        # A C2 board takes the installed system's set even beside Recovery's.
        c2 = {"brcm/brcmfmac4388c2-pcie.apple,mriya.bin": FIRMWARE}
        self.assertEqual(wifi.wifi_source("mriya", c2, {"brcm/brcmfmac4388c2-pcie.apple,mriya.bin": b"partial"}), "system")
        self.assertEqual(wifi.wifi_source("mriya", c2, {"brcm/brcmfmac4388c2-pcie.apple,mriya.txt": b"x=1\n"}), "system")
        self.assertFalse(wifi.carries_wifi({"brcm/brcmfmac4388c2-pcie.apple,mriyax.bin": b"x"}, "mriya"))

    def test_a_writable_tree_is_not_the_installed_system(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(wifi.system_tree(tmp))
        self.assertIsNone(wifi.system_tree("/nonexistent/firmware/wifi"))


class ConformanceTests(unittest.TestCase):
    def converted(self, maker):
        with tempfile.TemporaryDirectory() as tmp:
            maker(Path(tmp))
            return wifi.converted(wifi.collection_class(UPSTREAM)(tmp))

    def test_system_package_is_complete_for_its_c2_boards(self):
        files = self.converted(installed_system)
        for board in ("hostomel", "mriya"):
            with self.subTest(board=board):
                self.assertEqual(wifi.board_chips(files, board), {wifi.SKIPPED_CHIP})
                self.assertEqual(wifi.missing_wifi(files, wifi.SKIPPED_CHIP, board), [])

    def test_the_chip_comes_from_the_directories_holding_the_boards_files(self):
        def two_revisions(root):
            installed_system(root)
            c0 = root / "C-4388__s-C0"
            c0.mkdir()
            (c0 / "mriya.trx").write_bytes(b"c0 firmware")
            m1 = root / "C-4378__s-B1"
            m1.mkdir()
            (m1 / "P-shikoku_M-RASP_V-m__m-7.9.txt").write_bytes(b"x=1\n")

        self.assertEqual(wifi.SKIPPED_CHIP, "4388c2")
        self.assertEqual(wifi.UPSTREAM_SKIPPED, "C-4388__s-C2")
        files = self.converted(two_revisions)
        self.assertEqual(wifi.board_chips(files, "mriya"), {"4388c0", "4388c2"})
        self.assertEqual(wifi.board_chips(files, "hostomel"), {"4388c2"})
        self.assertEqual(wifi.board_chips(files, "shikoku"), {"4378b1"})
        self.assertEqual(wifi.board_chips(files, "mriy"), set())
        self.assertEqual(wifi.board_chips(self.converted(recovery_25g83), "mriya"), set())

    def test_recovery_package_lacks_every_board_file(self):
        files = self.converted(recovery_25g83)
        missing = wifi.missing_wifi(files, "4388c2", "mriya")
        self.assertIn("brcm/brcmfmac4388c2-pcie.apple,mriya.bin", missing)
        self.assertIn("brcm/brcmfmac4388c2-pcie.apple,mriya-X3.txt", missing)

    def test_board_wide_nvram_is_not_another_skus(self):
        files = {
            "brcm/brcmfmac4388c2-pcie.apple,mriya.bin": b"x",
            "brcm/brcmfmac4388c2-pcie.apple,mriya.clm_blob": b"x",
            "brcm/brcmfmac4388c2-pcie.apple,mriya-X0-WLMT-a.txt": b"x",
        }
        missing = wifi.missing_wifi(files, "4388c2", "mriya")
        self.assertIn("brcm/brcmfmac4388c2-pcie.apple,mriya.txt", missing)
        self.assertNotIn("brcm/brcmfmac4388c2-pcie.apple,mriya-X0.txt", missing)

    def test_bluetooth_needs_firmware_and_patch_table(self):
        self.assertEqual(wifi.missing_bluetooth(bluetooth("mriya"), "4388c2", "mriya"), [])
        half = {k: v for k, v in bluetooth("mriya").items() if k.endswith(".bin")}
        self.assertEqual(
            wifi.missing_bluetooth(half, "4388c2", "mriya"),
            ["brcm/brcmbt4388c2-apple,mriya-u.bin", "brcm/brcmbt4388c2-apple,mriya-u.ptb"],
        )


class FakeCollection:
    def __init__(self, files):
        self._files = files

    def files(self):
        return [(name, FWFile(name, data)) for name, data in self._files.items()]


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.work = Path(self.tmp.name)
        share = self.work / "recovery/usr/share/firmware"
        recovery_25g83(share / "wifi")
        # An M1-to-M3 board, which Recovery carries outside the C2 directory.
        c3 = share / "wifi/C-4387__s-C3"
        c3.mkdir()
        (c3 / "kanaha.trx").write_bytes(b"c3 firmware")
        (c3 / "kanaha.clmb").write_bytes(b"c3 clm")
        (c3 / "P-kanaha_M-RASP_V-m__m-7.9.txt").write_bytes(b"x=1\n")
        (share / "bluetooth").mkdir(parents=True)
        (share / "bluetooth/recovery-bt.bin").write_bytes(b"bt")
        (self.work / "recovery/usr/sbin").mkdir(parents=True)
        self.system = self.work / "system-wifi"
        installed_system(self.system)
        self.commands = []
        self.staged = {}

        def run(command, *args, **kwargs):
            self.commands.append(list(command))
            if command[:3] == ["tar", "czf", "all_firmware.tar.gz"]:
                at = command.index("firmware")
                staged = Path(command[at - 1]) / "firmware"
                self.staged = {
                    str(p.relative_to(staged)): p.is_symlink() for p in staged.rglob("*") if p.is_file() or p.is_symlink()
                }
                self.after = command[at + 1:at + 3]
            return SimpleNamespace(returncode=0)

        self.process = SimpleNamespace(run=run, CompletedProcess=object)
        self.stub = SimpleNamespace(
            WiFiFWCollection=lambda path: self.fail("the overlay collection must be used"),
            BluetoothFWCollection=lambda path: FakeCollection(bluetooth(self.bt_board)),
            subprocess=self.process,
        )
        self.bt_board = "mriya"
        self.cwd = os.getcwd()
        os.chdir(self.work)

    def tearDown(self):
        os.chdir(self.cwd)
        self.tmp.cleanup()

    @contextlib.contextmanager
    def collecting(self, machine, board="mriya", read_only=True):
        tree = str(self.system) if read_only else None
        with mock.patch.object(wifi, "system_tree", lambda path: tree):
            with wifi.collectors(self.stub, UPSTREAM, machine, read_board=lambda: board) as radios:
                yield radios

    def stub_collects(self):
        """What stub.collect_firmware does with the names this module rebinds."""
        files = dict(self.stub.WiFiFWCollection("recovery/usr/share/firmware/wifi/").files())
        files.update(self.stub.BluetoothFWCollection("recovery/usr/share/firmware/bluetooth/").files())
        self.stub.subprocess.run(
            ["tar", "czf", "all_firmware.tar.gz", "fud_firmware", "-C", "recovery/usr/share", "firmware",
             "-C", "../../usr/sbin", "appleh13camerad"],
            check=True,
        )
        return files

    def test_m4_pro_takes_the_installed_systems_wifi_and_archives_that_tree(self):
        with self.collecting("j614s") as radios:
            files = self.stub_collects()
            radios.require()
        self.assertEqual(files["brcm/brcmfmac4388c2-pcie.apple,mriya.bin"].data, FIRMWARE)
        self.assertEqual(radios.system, str(self.system))
        self.assertIn("wifi/C-4388__s-C2/guam.trx", self.staged)
        self.assertNotIn("wifi/C-4388__s-C2/aruba.clmb", self.staged)
        self.assertTrue(self.staged["wifi/C-4388__s-C2/mriya.trx"])
        self.assertIn("bluetooth/recovery-bt.bin", self.staged)
        # The next relative -C still starts from Recovery's share directory.
        self.assertEqual(self.after, ["-C", str(self.work / "recovery/usr/share")])
        self.assertEqual(self.commands[-1][-3:], ["-C", "../../usr/sbin", "appleh13camerad"])

    def test_the_staged_archive_takes_contents_even_where_flags_cannot_be_set(self):
        # A J616s on macOS 26.6.2: setting the ramdisk's flags on the staged copies is refused.
        def refused(*args, **kwargs):
            raise PermissionError(1, "Operation not permitted")

        with mock.patch("shutil.copystat", refused), mock.patch.object(os, "chflags", refused, create=True), \
                mock.patch.object(os, "utime", refused), self.collecting("j614s") as radios:
            files = self.stub_collects()
            radios.require()
        self.assertEqual(files["brcm/brcmfmac4388c2-pcie.apple,mriya.bin"].data, FIRMWARE)
        self.assertIn("bluetooth/recovery-bt.bin", self.staged)
        self.assertIn("wifi/C-4388__s-C2/guam.trx", self.staged)
        self.assertTrue(self.staged["wifi/C-4388__s-C2/mriya.trx"], "links stay links")

    def test_the_staged_tree_is_a_byte_identical_copy(self):
        snapshot = {}

        def run(command, *args, **kwargs):
            if command[:3] == ["tar", "czf", "all_firmware.tar.gz"]:
                staged = Path(command[command.index("firmware") - 1]) / "firmware"
                snapshot.update({str(p.relative_to(staged)): (os.readlink(p) if p.is_symlink() else p.read_bytes())
                                 for p in staged.rglob("*") if p.is_file() or p.is_symlink()})
            return SimpleNamespace(returncode=0)

        self.stub.subprocess = SimpleNamespace(run=run, CompletedProcess=object)
        with self.collecting("j614s") as radios:
            self.stub_collects()
            radios.require()
        share = self.work / "recovery/usr/share/firmware"
        expected = {"bluetooth/recovery-bt.bin": b"bt"}
        for path in self.system.rglob("*"):
            if path.is_file() or path.is_symlink():
                expected["wifi/" + str(path.relative_to(self.system))] = (
                    os.readlink(path) if path.is_symlink() else path.read_bytes())
        self.assertEqual(snapshot, expected)
        self.assertFalse(any(name.startswith("wifi/") and (share / name).exists() and name not in expected
                             for name in snapshot), "Recovery's own wifi tree is left out")

    def test_names_are_restored_after_collection(self):
        before = (self.stub.WiFiFWCollection, self.stub.BluetoothFWCollection, self.stub.subprocess)
        with self.collecting("j614s"):
            pass
        self.assertEqual((self.stub.WiFiFWCollection, self.stub.BluetoothFWCollection, self.stub.subprocess), before)

    def test_a_board_no_source_carries_is_refused(self):
        # The installed system unreadable, or a board neither tree has: no
        # Wi-Fi firmware for this Mac anywhere. The machine id plays no part.
        for machine in ("j614s", "j604", "j999"):
            for options in ({"read_only": False}, {"board": "tasmania"}):
                with self.subTest(machine=machine, options=options), self.collecting(machine, **options) as radios:
                    self.stub_collects()
                    with self.assertRaisesRegex(wifi.WiFiError, "neither Recovery nor the installed macOS"):
                        radios.require()
                    self.assertIsNone(radios.system)
                    self.assertNotIn("wifi/C-4388__s-C2/guam.trx", self.staged)

    def test_a_c2_board_the_package_lacks_is_refused(self):
        # The installed system has the board's files in the C2 directory but
        # no firmware for it, so Recovery's set is taken: the chip comes from
        # the system's files, and Recovery's set lacks the board.
        partial = self.work / "partial-system/C-4388__s-C2"
        partial.mkdir(parents=True)
        (partial / "P-mriya_M-WLMT_V-u__m-4.9.txt").write_bytes(b"vendor=u\n")
        self.system = partial.parent
        for machine in ("j614s", "j999"):
            with self.subTest(machine=machine), self.collecting(machine) as radios:
                self.stub_collects()
                with self.assertRaisesRegex(wifi.WiFiError, "brcmfmac4388c2-pcie.apple,mriya.bin"):
                    radios.require()
                self.assertIsNone(radios.system)

    def test_missing_bluetooth_is_refused(self):
        self.bt_board = "hostomel"
        with self.collecting("j614s") as radios:
            self.stub_collects()
            with self.assertRaisesRegex(wifi.WiFiError, "brcmbt4388c2-apple,mriya-u.bin"):
                radios.require()

    def test_any_c2_board_takes_the_system_set_whatever_the_machine_id(self):
        for machine, board in (("j604", "mriya"), ("j999", "hostomel")):
            self.bt_board = board
            with self.subTest(machine=machine), self.collecting(machine, board=board) as radios:
                files = self.stub_collects()
                radios.require()
            self.assertEqual(radios.system, str(self.system))
            self.assertEqual(files[f"brcm/brcmfmac4388c2-pcie.apple,{board}.bin"].data, FIRMWARE)

    def test_boards_outside_the_c2_directory_keep_recoverys_set(self):
        # An M1-to-M3 board: Recovery carries it, the installed system (in
        # this fixture) does not, and the C2 requirement does not apply.
        self.bt_board = "hostomel"
        for read_only in (True, False):
            with self.subTest(read_only=read_only), self.collecting("j314s", board="kanaha", read_only=read_only) as radios:
                files = self.stub_collects()
                radios.require()
            self.assertIsNone(radios.system)
            self.assertEqual(files["brcm/brcmfmac4387c3-pcie.apple,kanaha.bin"].data, b"c3 firmware")

    def test_an_m1_to_m3_board_keeps_recoverys_set_beside_the_installed_systems(self):
        # A real installed macOS carries every board, the M1-to-M3 ones too.
        c3 = self.system / "C-4387__s-C3"
        c3.mkdir()
        (c3 / "kanaha.trx").write_bytes(b"installed c3 firmware")
        (c3 / "kanaha.clmb").write_bytes(b"installed c3 clm")
        (c3 / "P-kanaha_M-RASP_V-m__m-7.9.txt").write_bytes(b"x=2\n")
        self.bt_board = "hostomel"
        with self.collecting("j314s", board="kanaha") as radios:
            files = self.stub_collects()
            radios.require()
        self.assertIsNone(radios.system)
        self.assertEqual(files["brcm/brcmfmac4387c3-pcie.apple,kanaha.bin"].data, b"c3 firmware")
        self.assertEqual(self.after, ["-C", "../../usr/sbin"])

    def test_no_broadcom_board_keeps_recovery_without_the_system_tree(self):
        # The MacBook Neo's ADT has no Broadcom wlan board: read_board gives
        # None and the installed system is not consulted.
        def no_system(path):
            raise AssertionError("no Broadcom board, no system tree")

        with mock.patch.object(wifi, "system_tree", no_system):
            with wifi.collectors(self.stub, UPSTREAM, "j700", read_board=lambda: None) as radios:
                self.stub_collects()
                radios.require()
        self.assertIsNone(radios.system)
        self.assertIsNone(radios.board)
        self.assertEqual(radios.sources, [radios.wifi])

    def test_a_failed_board_lookup_is_refused(self):
        # A lookup failure must not pass for a Mac without a Broadcom board:
        # Recovery's set is collected, then the package is refused.
        def lookup_fails():
            raise wifi.WiFiError("cannot read the device tree's wlan node: ioreg failed")

        def no_system(path):
            raise AssertionError("no board, no system tree")

        with mock.patch.object(wifi, "system_tree", no_system):
            with wifi.collectors(self.stub, UPSTREAM, "j604", read_board=lookup_fails) as radios:
                self.stub_collects()
                with self.assertRaisesRegex(wifi.WiFiError, "j604: cannot read the device tree's wlan node"):
                    radios.require()
        self.assertIsNone(radios.board)
        self.assertIsNone(radios.system)

    def test_archive_without_recoverys_tree_is_refused(self):
        with self.collecting("j614s"):
            self.stub.WiFiFWCollection("recovery/usr/share/firmware/wifi/")
            with self.assertRaises(wifi.WiFiError):
                self.stub.subprocess.run(["tar", "czf", "all_firmware.tar.gz", "-C", "elsewhere", "firmware"])


if __name__ == "__main__":
    unittest.main()
