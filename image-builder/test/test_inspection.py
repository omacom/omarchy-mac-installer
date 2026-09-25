#!/usr/bin/env python3
"""Inspection fails an image whose boot components are missing or differ from the set."""
import hashlib
import os
import re
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fixtures  # noqa: E402

inspection = fixtures.load("inspection", "builder/inspection.py")
inspection.OWNER_UID = os.geteuid()
CHECKS = ("candidate-versions", "minimum-versions", "refused-packages", "installed-boot-payloads", "candidate-files",
          "m1n1-stage2", "aurora-device-trees", "limine-uki", "embedded-initramfs", "boot-splash", "boot-maintenance",
          "image-target", "first-boot", "snapshots", "pacman-config", "installed-system", "factory")
# Fixture roots live on whatever filesystem the tests run on: these paths stand in for btrfs subvolumes.
SUBVOLUMES: set[Path] = set()
real_is_subvolume = inspection.is_subvolume
inspection.is_subvolume = lambda path: path in SUBVOLUMES


class InspectionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.base = Path(cls.tmp.name)
        cls.signer = fixtures.Signer(cls.base / "signer")
        receipt = fixtures.make_set(cls.base / "set", cls.signer)
        cls.candidates = cls.base / "candidates"
        fixtures.import_set(cls.base / "set", cls.candidates, cls.signer, receipt)
        cls.good = cls.base / "root"
        fixtures.make_root(cls.good, cls.candidates)
        cls.summary = __import__("json").loads((cls.candidates / "import.json").read_text())

    @classmethod
    def tearDownClass(cls):
        cls.signer.close()
        for path in cls.base.rglob("*"):
            if path.is_dir() and not path.is_symlink():
                path.chmod(0o700)
        cls.tmp.cleanup()

    def setUp(self):
        work = Path(tempfile.mkdtemp(dir=self.base))
        self.root = work / "root"
        shutil.copytree(self.good, self.root, symlinks=True)
        self.factory = work / "factory"
        fixtures.make_factory(self.factory, self.root, self.summary)
        SUBVOLUMES.clear()
        SUBVOLUMES.add(self.root / ".snapshots")

    def inspect(self, candidates=None):
        return inspection.inspect(self.root, candidates or self.candidates, "edge", self.factory,
                                  trust=self.signer.trust)

    def assertFails(self, check, pattern, candidates=None):
        report = self.inspect(candidates)
        self.assertEqual(report["result"], "failed")
        self.assertEqual(report["checks"][check]["result"], "failed", report["checks"][check])
        self.assertRegex(report["checks"][check]["detail"], pattern)
        return report

    def stage2(self, m1n1=None, dtbs=None, uboot=None, tail=b""):
        m1n1 = m1n1 if m1n1 is not None else fixtures.default_contents()["m1n1-aurora"]["usr/lib/asahi-boot/m1n1.bin"]
        uboot = uboot if uboot is not None else fixtures.default_contents()["uboot-asahi"]["usr/lib/asahi-boot/u-boot-nodtb.bin"]
        if dtbs is None:
            dtbs = sorted(["s8000-n66", *fixtures.MODELS], key=str.encode)
        compressor = zlib.compressobj(wbits=31)
        data = m1n1 + b"".join(fixtures.dtb(name) for name in dtbs) + compressor.compress(uboot) + compressor.flush() + tail
        (self.root / "boot/efi/m1n1/boot.bin").write_bytes(data)

    def test_image_built_from_the_set_passes_inspection(self):
        report = self.inspect()
        self.assertEqual(sorted(report["checks"]), sorted(CHECKS))
        for check in CHECKS:
            self.assertEqual(report["checks"][check]["result"], "passed", (check, report["checks"][check]))
        self.assertEqual(report["result"], "passed")
        self.assertEqual(report["m1n1_stage2"]["device_trees"], 3)
        self.assertEqual(report["supported_models"], list(fixtures.MODELS))
        self.assertEqual(report["hardware_setup"], "build")

    def test_m1n1_stage2_mismatches(self):
        cases = {
            "another m1n1": dict(m1n1=b"some other m1n1 build"),
            "a missing device tree": dict(dtbs=["s8000-n66", "t6000-j314s"]),
            "device trees out of C order": dict(dtbs=["t6000-j314s", "s8000-n66", "t8103-j274"]),
            "an extra device tree": dict(dtbs=[*sorted(["s8000-n66", *fixtures.MODELS]), "t6020-j414s"]),
            "another U-Boot": dict(uboot=b"stock asahi u-boot"),
            "trailing bytes": dict(tail=b"\x00\x01binary"),
            "options the image does not set": dict(tail=b"chosen.bootargs=init=/bin/sh\n"),
            "junk before U-Boot": dict(uboot=b"", tail=b""),
        }
        for name, change in cases.items():
            with self.subTest(name):
                self.stage2(**change)
                self.assertFails("m1n1-stage2", "m1n1|device tree|U-Boot")

    def test_missing_stage2(self):
        (self.root / "boot/efi/m1n1/boot.bin").unlink()
        self.assertFails("m1n1-stage2", "not a regular file")

    def test_installed_payload_differs_from_the_set(self):
        (self.root / "usr/lib/asahi-boot/u-boot-nodtb.bin").write_bytes(b"patched")
        self.assertFails("installed-boot-payloads", "u-boot-nodtb.bin")

    def test_a_set_file_rewritten_during_the_build(self):
        for rel in ("usr/lib/omarchy/initcpio/omarchy-mac-encrypt", "usr/share/libalpm/scripts/limine-apple-gate",
                    "usr/share/omarchy/default/limine/limine.conf"):
            with self.subTest(rel):
                self.setUp()
                (self.root / rel).write_bytes(b"#!/bin/bash\nexit 0\n")
                self.assertFails("candidate-files", re.escape(rel))

    def test_a_set_file_with_another_mode_or_link(self):
        (self.root / "usr/lib/omarchy/initcpio/omarchy-mac-encrypt").chmod(0o644)
        self.assertFails("candidate-files", "omarchy-mac-encrypt.*mode")

    def test_m1n1_options_as_update_m1n1_reads_them(self):
        (self.root / "etc/m1n1.conf").write_text("chosen.asahi,efi-system-partition=EFI\ndisplay=1920x1080")
        self.assertEqual(self.inspect()["checks"]["m1n1-stage2"]["result"], "passed")

    def test_a_set_file_missing(self):
        (self.root / "usr/share/omarchy-mac/source-revision").unlink()
        self.assertFails("candidate-files", "source-revision")

    def test_limine_loader_is_not_its_packages(self):
        loader = fixtures.pe_image({".text": b"Another loader"})
        for rel in ("usr/share/limine/BOOTAA64.EFI", "boot/efi/EFI/BOOT/BOOTAA64.EFI"):
            (self.root / rel).write_bytes(loader)
        self.assertFails("boot-maintenance", "Limine loader")

    def test_mac_without_an_aurora_device_tree(self):
        contents = fixtures.default_contents()
        contents["uboot-asahi"]["usr/lib/asahi-boot/dtb/t6020-j414s.dtb"] = b"uboot t6020"
        work = Path(tempfile.mkdtemp(dir=self.base))
        receipt = fixtures.make_set(work / "set", self.signer, contents=contents)
        fixtures.import_set(work / "set", work / "candidates", self.signer, receipt)
        self.assertFails("aurora-device-trees", "t6020-j414s", work / "candidates")

    def test_limine_loader_menu_and_uki(self):
        esp = type("Esp", (), {"__truediv__": lambda _, rel: self.root / "boot/efi" / rel})()
        cases = {
            "loader": (lambda: (esp / "EFI/BOOT/BOOTAA64.EFI").write_bytes(fixtures.pe_image({".text": b"GRUB"})),
                       "ESP loader"),
            "menu": (lambda: (esp / "limine.conf").write_text("interface_branding: Omarchy Bootloader\n"),
                     "menu does not boot"),
            "hash": (lambda: (esp / "limine.conf").write_text(re.sub(r"#[0-9a-f]{128}", "#" + "0" * 128,
                                                                         (esp / "limine.conf").read_text())),
                     "hash differs"),
            "kernel": (lambda: (self.root / f"usr/lib/modules/{fixtures.RELEASE}/vmlinuz").write_bytes(b"MZ other"),
                       "release differs"),
            "identity": (lambda: (esp / "limine.conf").write_text(
                (esp / "limine.conf").read_text() + "comment: machine-id=" + "1" * 32 + "\n"), "machine identity"),
        }
        for name, (change, pattern) in cases.items():
            with self.subTest(name):
                self.setUp()
                change()
                self.assertFails("limine-uki", pattern)

    def test_embedded_initramfs_without_the_boot_packages_hooks(self):
        limine = inspection.limine
        members = {name: b"x" for name in limine.INITRD_FILES if "omarchy-mac-encrypt" not in name}
        members.update(limine.INITRD_LINKS)
        uki = self.root / "boot/efi/EFI/Linux/omarchy_linux-aurora.efi"
        sections = limine.pe_sections(uki)
        sections[".initrd"] = fixtures.initramfs(members)
        data = fixtures.pe_image(sections)
        uki.write_bytes(data)
        menu = self.root / "boot/efi/limine.conf"
        menu.write_text(menu.read_text().split("#")[0] + "#" + hashlib.blake2b(data).hexdigest() + "\n")
        self.assertFails("embedded-initramfs", "omarchy-mac-encrypt")

    def replace_uki(self, initrd=None, cmdline=None):
        """Rebuilds the UKI with another initramfs or command line, keeping the menu's hash and
        /etc/default/limine in step so only the splash is wrong."""
        limine = inspection.limine
        uki = self.root / "boot/efi/EFI/Linux/omarchy_linux-aurora.efi"
        sections = limine.pe_sections(uki)
        if initrd is not None:
            sections[".initrd"] = initrd
        if cmdline is not None:
            sections[".cmdline"] = cmdline.encode() + b" \n\0"
            fixtures.write(self.root, "etc/default/limine", f'KERNEL_CMDLINE[default]="{cmdline}"\n')
        data = fixtures.pe_image(sections)
        uki.write_bytes(data)
        menu = self.root / "boot/efi/limine.conf"
        menu.write_text(menu.read_text().split("#")[0] + "#" + hashlib.blake2b(data).hexdigest() + "\n")

    def test_boot_splash(self):
        bgrt = b"# Administrator customizations go in this file\n[Daemon]\nTheme=bgrt\n"
        cmdline = self.inspect()["uki"]["cmdline"]
        cases = (
            ("the image's own theme", lambda: fixtures.write(self.root, "etc/plymouth/plymouthd.conf", bgrt),
             "image's Plymouth theme is bgrt"),
            ("no theme chosen, so the packaged bgrt",
             lambda: fixtures.write(self.root, "etc/plymouth/plymouthd.conf",
                                    b"# Administrator customizations go in this file\n#[Daemon]\n#Theme=fade-in\n"),
             "image's Plymouth theme is bgrt"),
            ("the initramfs's theme", lambda: self.replace_uki(
                initrd=fixtures.good_initramfs(fixtures.plymouth_members(config=bgrt))), "initramfs shows the bgrt"),
            ("an initramfs without the configuration", lambda: self.replace_uki(
                initrd=fixtures.good_initramfs(fixtures.plymouth_members(config=None))), "initramfs shows the bgrt"),
            ("an initramfs without Plymouth", lambda: self.replace_uki(initrd=fixtures.good_initramfs({})),
             "initramfs shows the unset"),
            ("an initramfs without the theme", lambda: self.replace_uki(
                initrd=fixtures.good_initramfs(fixtures.plymouth_members(theme=False))), "initramfs lacks"),
            ("a boot line that lets Plymouth fall back to text",
             lambda: self.replace_uki(cmdline=cmdline.replace(" plymouth.ignore-serial-consoles", "")),
             "lacks plymouth.ignore-serial-consoles"),
            ("a boot line without the splash", lambda: self.replace_uki(cmdline=cmdline.replace(" splash", "")),
             "lacks splash"),
        )
        for name, change, pattern in cases:
            with self.subTest(name):
                self.setUp()
                change()
                report = self.assertFails("boot-splash", pattern)
                self.assertEqual(report["failed_checks"], ["boot-splash"])

    def test_snapshots(self):
        snapshots = lambda: self.root / ".snapshots"
        cases = (
            ("flattened by the image copy", lambda: SUBVOLUMES.clear(), "plain directory"),
            ("carrying the build's snapshots", lambda: (snapshots() / "1/snapshot").mkdir(parents=True),
             "taken during the build"),
            ("without snapper's configuration", lambda: (self.root / "etc/snapper/configs/root").unlink(),
             "no snapper root configuration"),
            ("a file", lambda: (snapshots().rmdir(), snapshots().write_text("")), "not a directory"),
            ("missing, with nothing to create it", lambda: snapshots().rmdir(), "no deferred hardware step"),
        )
        for name, change, pattern in cases:
            with self.subTest(name):
                self.setUp()
                change()
                self.assertFails("snapshots", pattern)

    def test_snapshots_created_on_first_boot(self):
        (self.root / ".snapshots").rmdir()
        fixtures.write(self.root, "var/lib/omarchy/image/deferred-steps", "install/hardware/apple/snapshots-subvolume.sh\n")
        fixtures.write(self.root, "usr/bin/omarchy-provision-hardware", "#!/bin/bash\n", 0o755)
        (self.root / "etc/systemd/system/multi-user.target.wants/omarchy-provision-hardware.service").symlink_to(
            "/etc/systemd/system/omarchy-provision-hardware.service")
        report = self.inspect()
        self.assertEqual(report["checks"]["snapshots"]["result"], "passed", report["checks"]["snapshots"])
        self.assertIn("snapshots-subvolume.sh", report["checks"]["snapshots"]["detail"])

    def test_a_plain_directory_is_not_a_subvolume(self):
        self.assertFalse(real_is_subvolume(self.root / ".snapshots"))
        self.assertFalse(real_is_subvolume(self.root / "etc"))

    def test_audio_stack_missing(self):
        for name in ("alsa-ucm-conf-asahi", "asahi-audio"):
            with self.subTest(name):
                self.setUp()
                shutil.rmtree(next((self.root / "var/lib/pacman/local").glob(f"{name}-1.0-1")))
                report = self.assertFails("installed-system", "packages-required-present")
                self.assertIn(name, report["installed_system"]["packages-required-present"]["detail"])

    def test_installed_version_below_the_minimum(self):
        local = self.root / "var/lib/pacman/local"
        entry = next(local.glob("limine-mkinitcpio-hook-*"))
        (entry / "desc").write_text("%NAME%\nlimine-mkinitcpio-hook\n\n%VERSION%\n1.38.0-1.1\n\n")
        report = self.assertFails("minimum-versions", "below the minimum")
        self.assertEqual(report["checks"]["candidate-versions"]["result"], "failed")

    def test_refused_package_installed(self):
        fixtures.local_package(self.root, "linux-asahi", "6.16-1", [])
        self.assertFails("refused-packages", "linux-asahi")

    def test_maintenance_hook_without_the_apple_gate(self):
        hook = self.root / "etc/pacman.d/hooks/90-mkinitcpio-install.hook"
        hook.write_text("[Action]\nExec = /usr/share/libalpm/scripts/limine-mkinitcpio-install\n")
        self.assertFails("boot-maintenance", "incomplete")

    def test_image_target_manifest(self):
        for name, change in (("missing", lambda p: p.unlink()),
                             ("another platform", lambda p: p.write_text("format=1\nplatform=qualcomm\n")),
                             ("writable", lambda p: p.chmod(0o666))):
            with self.subTest(name):
                self.setUp()
                change(self.root / "var/lib/omarchy/image/target")
                self.assertFails("image-target", "missing|apple-silicon|mode")

    def test_factory(self):
        for name, change in (
            ("fresh-image state", lambda f: fixtures.write(f, "var/lib/omarchy/mac-first-boot/pending", b"")),
            ("owner state", lambda f: fixtures.write(f, "var/lib/omarchy/provisioning/pending", b"")),
            ("another set", lambda f: fixtures.write(f, "var/lib/omarchy/factory-sealed", "format=2\ncandidate_set=x\n")),
            ("no image target", lambda f: (f / "var/lib/omarchy/image/target").unlink()),
            ("a keyring", lambda f: (f / "etc/pacman.d/gnupg").mkdir(parents=True)),
        ):
            with self.subTest(name):
                self.setUp()
                change(self.factory)
                self.assertFails("factory", "@factory")

    def test_first_boot_contract(self):
        for rel, change, pattern in (
            ("var/lib/omarchy/mac-first-boot/deferred-steps", lambda p: p.write_text("install/hardware/all.sh\n"),
             "contract"),
            ("var/lib/omarchy/mac-first-boot/pending", lambda p: p.unlink(), "first boot"),
            ("var/lib/omarchy/limine.enabled", lambda p: p.unlink(), "Limine gate"),
            ("var/lib/omarchy/image/deferred-steps", lambda p: p.write_text("install/hardware/apple/audio.sh\n"),
             "never runs"),
        ):
            with self.subTest(rel):
                self.setUp()
                change(self.root / rel)
                self.assertFails("first-boot", pattern)

    def test_pacman_config_is_the_runtimes(self):
        (self.root / "etc/pacman.conf").write_text("[omarchy-candidates]\nServer = file:///work/repos\n")
        self.assertFails("pacman-config", "pacman.conf")


if __name__ == "__main__":
    unittest.main()
