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

    def test_reads_the_wlan_module_instance(self):
        self.assertEqual(wifi.board(self.ioreg([{"name": b"wlan\0", "module-instance": b"guam\0"}])), "guam")
        self.assertEqual(wifi.board(self.ioreg([{"name": b"wlan\0"}, {"module-instance": b"mriya\0"}])), "mriya")

    def test_no_board_without_a_usable_name(self):
        self.assertIsNone(wifi.board(self.ioreg([])))
        self.assertIsNone(wifi.board(lambda command, **kwargs: SimpleNamespace(stdout=b"")))
        for value in (b"Guam", b"../guam", b"", b"a" * 33, b"guam x"):
            self.assertIsNone(wifi.board(self.ioreg([{"module-instance": value}])), value)

        def fails(command, **kwargs):
            raise subprocess.CalledProcessError(1, command)

        self.assertIsNone(wifi.board(fails))

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

    def test_system_package_is_complete_for_m4_pro_and_max(self):
        files = self.converted(installed_system)
        for machine, (chip, board) in wifi.M4_PRO_MAX_WIFI.items():
            with self.subTest(machine=machine):
                self.assertEqual(wifi.missing_wifi(files, chip, board), [])

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

    def test_names_are_restored_after_collection(self):
        before = (self.stub.WiFiFWCollection, self.stub.BluetoothFWCollection, self.stub.subprocess)
        with self.collecting("j614s"):
            pass
        self.assertEqual((self.stub.WiFiFWCollection, self.stub.BluetoothFWCollection, self.stub.subprocess), before)

    def test_recovery_only_m4_pro_package_is_refused(self):
        for options in ({"read_only": False}, {"board": None}, {"board": "kanaha"}):
            with self.subTest(options=options), self.collecting("j614s", **options) as radios:
                self.stub_collects()
                with self.assertRaises(wifi.WiFiError):
                    radios.require()
                self.assertIsNone(radios.system)
                self.assertNotIn("wifi/C-4388__s-C2/guam.trx", self.staged)

    def test_missing_bluetooth_is_refused(self):
        self.bt_board = "hostomel"
        with self.collecting("j614s") as radios:
            self.stub_collects()
            with self.assertRaisesRegex(wifi.WiFiError, "brcmbt4388c2-apple,mriya-u.bin"):
                radios.require()

    def test_other_macs_get_the_system_set_without_the_refusal(self):
        with self.collecting("j604", board="mriya") as radios:
            self.stub_collects()
            radios.require()
        self.assertIsNotNone(radios.system)
        with self.collecting("j314s", board="mriya", read_only=False) as radios:
            files = self.stub_collects()
            radios.require()
        self.assertFalse([n for n in files if n.endswith(".bin") and "brcmfmac" in n])

    def test_the_neo_keeps_recovery_and_reads_no_board(self):
        def no_board():
            raise AssertionError("the Neo reads no Broadcom board")

        with mock.patch.object(wifi, "system_tree", lambda path: str(self.system)):
            with wifi.collectors(self.stub, UPSTREAM, "j700", read_board=no_board) as radios:
                self.stub_collects()
                radios.require()
        self.assertIsNone(radios.system)

    def test_archive_without_recoverys_tree_is_refused(self):
        with self.collecting("j614s"):
            self.stub.WiFiFWCollection("recovery/usr/share/firmware/wifi/")
            with self.assertRaises(wifi.WiFiError):
                self.stub.subprocess.run(["tar", "czf", "all_firmware.tar.gz", "-C", "elsewhere", "firmware"])


if __name__ == "__main__":
    unittest.main()
