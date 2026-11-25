# SPDX-License-Identifier: MIT
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import omarchy_isp as isp

LEGACY = isp.SetFile(0x248, 0x18200103, "1820_01XX", 0x442C)
IMX558_1921 = isp.SetFile(0x558, 0x19210106, "1921_01XX", 0xAD40)


def blob(entry, fill=0):
    data = bytearray([fill] * ((entry.size + 63) & ~63))
    struct.pack_into(">8I", data, 0, entry.magic, 1, 0, 0, 0x1000000, 0x10000, 0, 0x3C00000)
    return bytes(data)


class IspTests(unittest.TestCase):
    def test_extends_registry_without_changing_inherited_entries(self):
        custom = isp.SetFile(1, 0xABCD1234, "future_01XX", 100)
        result = isp.extended_setfiles([LEGACY, IMX558_1921, custom])
        self.assertEqual(result, (LEGACY, IMX558_1921, custom, isp.IMX558_1925))
        self.assertEqual(isp.extended_setfiles(result), result)
        with self.assertRaises(isp.IspError):
            isp.extended_setfiles([isp.IMX558_1925._replace(size=64)])

    def test_extracts_1925_full_kernel_size(self):
        data = blob(isp.IMX558_1925, 0xAB)
        output = isp.extract(bytes(4096) + data, [LEGACY])
        self.assertEqual(output, {"apple/isp_1925_03XX.dat": data})
        self.assertEqual(len(next(iter(output.values()))), 52160)

    def test_extracts_2224_full_kernel_size_only_when_asked(self):
        data = blob(isp.IMX958_2224, 0xCD)
        self.assertEqual(isp.extract(bytes(4096) + data, [LEGACY]), {})
        output = isp.extract(bytes(4096) + data, [LEGACY, isp.IMX958_2224])
        self.assertEqual(output, {"apple/isp_2224_01XX.dat": data})
        self.assertEqual(len(data), 28672)

    def test_both_daemons_and_legacy_only_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "appleh13camerad").write_bytes(blob(LEGACY))
            self.assertEqual(isp.collect(tmp, [LEGACY]), {"apple/isp_1820_01XX.dat": blob(LEGACY)})
            (directory / "appleh16camerad").write_bytes(blob(isp.IMX558_1925))
            self.assertEqual(set(isp.collect(tmp, [LEGACY])),
                             {"apple/isp_1820_01XX.dat", "apple/isp_1925_03XX.dat"})

    def test_h16_only_and_single_file_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "appleh16camerad"
            path.write_bytes(blob(isp.IMX558_1925))
            self.assertEqual(isp.collect(path, []), isp.collect(tmp, []))
            self.assertTrue(isp.collect(tmp, []))

    def test_deduplicates_identical_and_rejects_conflicting_copies(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = [Path(tmp) / name for name in isp.DAEMON_NAMES]
            for path in paths:
                path.write_bytes(blob(LEGACY))
            self.assertEqual(len(isp.collect(tmp, [LEGACY])), 1)
            paths[1].write_bytes(blob(LEGACY, 2))
            with self.assertRaisesRegex(isp.IspError, "conflicting copies"):
                isp.collect(tmp, [LEGACY])

    def test_truncated_known_setfile_never_published(self):
        for entry in (LEGACY, isp.IMX558_1925):
            with self.subTest(entry=entry), self.assertRaisesRegex(isp.IspError, "truncated"):
                isp.extract(blob(entry)[:-1], [LEGACY])

    def test_bad_header_unknown_magic_and_unaligned_match_ignored(self):
        for word in (2, 3, 4, 5, 6, 7):
            data = bytearray(blob(LEGACY))
            struct.pack_into(">I", data, word * 4, 1)
            self.assertEqual(isp.extract(data, [LEGACY]), {})
        self.assertEqual(isp.extract(blob(LEGACY._replace(magic=0x99999999)), [LEGACY]), {})
        self.assertEqual(isp.extract(b"x" + blob(LEGACY), [LEGACY]), {})
        self.assertEqual(isp.extract(bytes(31), [LEGACY]), {})

    def test_collection_preserves_FWFile_interface(self):
        class FWFile:
            def __init__(self, name, data):
                self.name, self.data = name, data

        class Original:
            def __init__(self, source):
                self.fwfiles = []
                self.load(source)

            def files(self):
                return self.fwfiles

        upstream = SimpleNamespace(ISP_SETFILES=[LEGACY], FWFile=FWFile, ISPFWCollection=Original)
        collection = isp.collection_class(upstream)
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "appleh16camerad").write_bytes(blob(isp.IMX558_1925))
            name, firmware = collection(tmp).files()[0]
            self.assertIsInstance(firmware, FWFile)
            self.assertEqual(name, firmware.name)
            self.assertEqual(firmware.data, blob(isp.IMX558_1925))


class M4CollectorTests(unittest.TestCase):
    M1_TO_M3 = (0x8103, 0x6000, 0x6001, 0x6002, 0x8112, 0x6020, 0x6021, 0x6022,
                0x8122, 0x6030, 0x6031, 0x6034)

    def setUp(self):
        class FWFile:
            def __init__(self, name, data):
                self.name, self.data = name, data

        class Original:
            def __init__(self, source):
                self.fwfiles = []
                self.load(source)

            def load(self, source):
                self.fwfiles.append(("upstream", source))

            def files(self):
                return self.fwfiles

        self.upstream = SimpleNamespace(ISP_SETFILES=[LEGACY], FWFile=FWFile, ISPFWCollection=Original)
        self.commands = []
        self.process = SimpleNamespace(run=lambda command, *args, **kwargs: self.commands.append(command),
                                       PIPE=-1)
        self.stub = SimpleNamespace(ISPFWCollection=Original, subprocess=self.process)
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.sbin = Path(work.name, "recovery/usr/sbin")
        self.sbin.mkdir(parents=True)
        (self.sbin / "appleh13camerad").write_bytes(blob(LEGACY))
        # Setfiles start on 4 KiB boundaries in the daemon.
        h16 = bytearray(0xD000 + 0x7000)
        h16[:0xCBC0] = blob(isp.IMX558_1925)
        h16[0xD000:] = blob(isp.IMX958_2224)
        (self.sbin / "appleh16camerad").write_bytes(h16)

    def test_other_macs_keep_stubs_collection_and_archive(self):
        for chip in (*self.M1_TO_M3, 0x8140, None):
            with self.subTest(chip=chip), isp.m4_collectors(self.stub, self.upstream, chip, self.sbin):
                self.assertIs(self.stub.ISPFWCollection, self.upstream.ISPFWCollection)
                self.assertIs(self.stub.subprocess, self.process)

    def test_m4_collects_both_daemons_and_archives_appleh16camerad(self):
        for chip in sorted(isp.M4_CHIPS):
            with self.subTest(chip=hex(chip)):
                self.commands.clear()
                with isp.m4_collectors(self.stub, self.upstream, chip, self.sbin):
                    names = [name for name, _ in self.stub.ISPFWCollection(str(self.sbin)).files()]
                    self.assertEqual(names, ["apple/isp_1820_01XX.dat", "apple/isp_1925_03XX.dat",
                                             "apple/isp_2224_01XX.dat"])
                    self.stub.subprocess.run(["tar", "czf", "all_firmware.tar.gz", "fud_firmware",
                                              "-C", "../../usr/sbin", "appleh13camerad"], check=True)
                    self.stub.subprocess.run(["hdiutil", "detach", "-quiet", "recovery"])
                    self.assertEqual(self.stub.subprocess.PIPE, -1)
                self.assertEqual(self.commands, [
                    ["tar", "czf", "all_firmware.tar.gz", "fud_firmware", "-C", "../../usr/sbin",
                     "appleh13camerad", "-C", str(self.sbin.resolve()), "appleh16camerad"],
                    ["hdiutil", "detach", "-quiet", "recovery"],
                ])
                self.assertIs(self.stub.ISPFWCollection, self.upstream.ISPFWCollection)
                self.assertIs(self.stub.subprocess, self.process)

    def test_m4_without_appleh16camerad_archives_as_before(self):
        (self.sbin / "appleh16camerad").unlink()
        command = ["tar", "czf", "all_firmware.tar.gz", "-C", "../../usr/sbin", "appleh13camerad"]
        with isp.m4_collectors(self.stub, self.upstream, 0x8132, self.sbin):
            self.stub.subprocess.run(command)
            names = [name for name, _ in self.stub.ISPFWCollection(str(self.sbin)).files()]
        self.assertEqual(self.commands, [command])
        self.assertEqual(names, ["apple/isp_1820_01XX.dat"])

    def test_m4_hooks_restored_after_an_error(self):
        with self.assertRaises(isp.IspError):
            with isp.m4_collectors(self.stub, self.upstream, 0x8132, self.sbin):
                raise isp.IspError("stop")
        self.assertIs(self.stub.ISPFWCollection, self.upstream.ISPFWCollection)
        self.assertIs(self.stub.subprocess, self.process)

    def test_extra_setfiles_leave_the_default_registry_alone(self):
        self.assertNotIn(isp.IMX958_2224, isp.extended_setfiles([LEGACY]))
        (self.sbin / "appleh13camerad").unlink()
        names = [name for name, _ in isp.collection_class(self.upstream)(str(self.sbin)).files()]
        self.assertEqual(names, ["apple/isp_1925_03XX.dat"])


if __name__ == "__main__":
    unittest.main()
