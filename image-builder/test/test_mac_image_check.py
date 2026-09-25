#!/usr/bin/env python3
"""A build directory's payload, PROVENANCE and IMAGE must describe the same image and candidate set."""
import hashlib
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("mac_image_check", str(ROOT / "bin/mac-image-check"))
spec = importlib.util.spec_from_loader("mac_image_check", loader)
check = importlib.util.module_from_spec(spec)
loader.exec_module(check)

NAME = "omarchy-2026.09.25-aarch64-apple-silicon-mac-edge-os-package.zip"
INPUTS = {
    "candidate_set": "apple-test-fixture",
    "candidate_source_commit": "a" * 40,
    "candidate_signer": "E" * 40,
    "candidate_receipt_sha256": "1" * 64,
    "candidate_manifest_sha256": "2" * 64,
    "omarchy_channel": "edge",
}
PACKAGES = [
    ("omarchy", "4.0.0-1", "omarchy-candidates", "omarchy-4.0.0-1-aarch64.pkg.tar.xz", "3" * 64),
    ("uboot-asahi", "2026.07-1", "omarchy-candidates", "uboot-asahi-2026.07-1-aarch64.pkg.tar.zst", "4" * 64),
    ("glibc", "2.43-1", "core", "glibc-2.43-1-aarch64.pkg.tar.xz", "5" * 64),
]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pe(extra: bytes) -> bytes:
    data = bytearray(0x200)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", data, 0x84, 0xAA64)
    return bytes(data) + extra


def package_set(packages) -> str:
    lines = sorted((f"{n}|{v}|{s}\n" for n, v, _, _, s in packages), key=str.encode)
    return hashlib.sha256("".join(lines).encode()).hexdigest()


def build(out: Path, packages=PACKAGES, candidates=PACKAGES[:2], inspection="passed", image_edit=None) -> None:
    payload = out / "payload"
    for directory in ("esp/EFI/BOOT", "esp/EFI/Linux", "esp/m1n1", "esp/omarchy"):
        (payload / directory).mkdir(parents=True)
    boot = bytearray(1 << 16)
    boot[0x438:0x43A] = b"\x53\xef"
    boot[0x468:0x478] = uuid.UUID(check.BOOT_UUID).bytes
    boot[0x478:0x484] = b"OMARCHY_BOOT"
    (payload / "boot.img").write_bytes(boot)
    root = bytearray(1 << 17)
    root[0x10020:0x10030] = uuid.UUID(check.ROOT_UUID).bytes
    root[0x10040:0x10048] = b"_BHRfS_M"
    root[0x1012B:0x1012B + 12] = b"OMARCHY_ROOT"
    (payload / "root.img").write_bytes(root)
    (payload / "esp/EFI/BOOT/BOOTAA64.EFI").write_bytes(pe(b"Limine 12.9.0\n"))
    (payload / "esp/EFI/Linux/omarchy_linux-aurora.efi").write_bytes(pe(b".linux\0.initrd\0"))
    (payload / "esp/limine.conf").write_text("interface_branding: Omarchy Bootloader\n//linux-aurora\n"
                                             "  protocol: efi\n  path: boot():/EFI/Linux/omarchy_linux-aurora.efi\n")
    (payload / "esp/m1n1/boot.bin").write_bytes(b"m1n1" * 64)
    shutil.copy(ROOT / "builder/omarchy-volume.icns", payload / "omarchy-volume.icns")
    zip_path = out / NAME
    subprocess.run(["bsdtar", "--format", "zip", "-cf", str(zip_path), "esp", "boot.img", "root.img",
                    "omarchy-volume.icns"], cwd=payload, check=True)
    (out / "installer_data.json").write_text(json.dumps(check.expected_metadata(NAME)))
    (out / "INSPECTION").write_text(json.dumps({"result": inspection}))
    (out / "inputs").write_text("format=2\n" + "".join(f"{k}={v}\n" for k, v in INPUTS.items()))
    lines = ["format=2", "kind=mac-image", "lane=edge", "kernel=linux-aurora", "platform=apple-silicon",
             "builder_commit=" + "c" * 40, "builder_tree_clean=true", f"inputs_sha256={sha(out / 'inputs')}"]
    lines += [f"input.{k}={v}" for k, v in INPUTS.items()]
    lines += [f"candidate={n}|{v}|{f}|{s}" for n, v, _, f, s in candidates]
    lines += ["hardware_setup=build", f"package_set_sha256={package_set(packages)}", f"package_count={len(packages)}"]
    lines += [f"package={i}|{'|'.join(p)}" for i, p in enumerate(packages, 1)]
    lines += [f"installer_data_sha256={sha(out / 'installer_data.json')}", f"inspection_sha256={sha(out / 'INSPECTION')}",
              f"payload={NAME}|{zip_path.stat().st_size}|{sha(zip_path)}"]
    (out / "PROVENANCE").write_text("\n".join(lines) + "\n")
    digests = check.check_payload("edge", zip_path, out / "installer_data.json")
    image = ["format=2", "lane=edge", "platform=apple-silicon", "builder_commit=" + "c" * 40, "builder_tree_clean=true",
             *[f"{k}={v}" for k, v in INPUTS.items()],
             "hardware_setup=build", f"package_set_sha256={package_set(packages)}",
             *[f"image_sha256={m}|{d}" for m, d in sorted(digests.items())], f"input_digest={sha(out / 'inputs')}"]
    if image_edit:
        image = image_edit(image)
    (out / "IMAGE").write_text("\n".join(image) + "\n")
    shutil.rmtree(payload)


class BuildDirectoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "out"
        self.out.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_consistent_build_directory_passes(self):
        build(self.out)
        check.check_provenance("edge", self.out)
        check.check_descriptor("edge", self.out)

    def test_a_candidate_installed_from_another_repository(self):
        packages = [PACKAGES[0], ("uboot-asahi", "2026.07-1", "asahi-alarm", PACKAGES[1][3], PACKAGES[1][4]), PACKAGES[2]]
        build(self.out, packages=packages)
        with self.assertRaisesRegex(check.CheckError, "uboot-asahi from the candidate set"):
            check.check_provenance("edge", self.out)

    def test_a_failed_inspection(self):
        build(self.out, inspection="failed")
        with self.assertRaisesRegex(check.CheckError, "INSPECTION did not pass"):
            check.check_provenance("edge", self.out)

    def test_image_names_another_candidate_set(self):
        build(self.out, image_edit=lambda lines: [l.replace("apple-test-fixture", "apple-test-other") for l in lines])
        with self.assertRaisesRegex(check.CheckError, "candidate_set"):
            check.check_descriptor("edge", self.out)

    def test_image_names_another_builder(self):
        build(self.out, image_edit=lambda lines: [("builder_commit=" + "d" * 40) if l.startswith("builder_commit")
                                                  else l for l in lines])
        with self.assertRaisesRegex(check.CheckError, "builder_commit"):
            check.check_descriptor("edge", self.out)

    def test_image_from_an_uncommitted_tree_says_so(self):
        build(self.out, image_edit=lambda lines: [l.replace("builder_tree_clean=true", "builder_tree_clean=false")
                                                  for l in lines])
        with self.assertRaisesRegex(check.CheckError, "builder_tree_clean"):
            check.check_descriptor("edge", self.out)

    def test_image_names_another_package_set(self):
        build(self.out, image_edit=lambda lines: [("package_set_sha256=" + "0" * 64) if l.startswith("package_set")
                                                  else l for l in lines])
        with self.assertRaisesRegex(check.CheckError, "package_set_sha256"):
            check.check_descriptor("edge", self.out)

    def test_a_changed_payload(self):
        build(self.out)
        with (self.out / NAME).open("ab") as stream:
            stream.write(b"\0")
        with self.assertRaises(check.CheckError):
            check.check_provenance("edge", self.out)

    def test_payload_without_limine(self):
        build(self.out)
        payload = self.out / "unpacked"
        payload.mkdir()
        subprocess.run(["bsdtar", "-xf", str(self.out / NAME), "-C", str(payload)], check=True)
        (payload / "esp/EFI/BOOT/BOOTAA64.EFI").write_bytes(pe(b"GRUB 2.12\n"))
        (self.out / NAME).unlink()
        subprocess.run(["bsdtar", "--format", "zip", "-cf", str(self.out / NAME), "esp", "boot.img", "root.img",
                        "omarchy-volume.icns"], cwd=payload, check=True)
        with self.assertRaisesRegex(check.CheckError, "not Limine"):
            check.check_payload("edge", self.out / NAME, self.out / "installer_data.json")


class SubvolumeTest(unittest.TestCase):
    def test_the_five_subvolumes_and_snappers(self):
        check.check_subvolumes(["@", "@home", "@log", "@pkg", "@factory"])
        check.check_subvolumes(["@", "@home", "@log", "@pkg", "@factory", "@/.snapshots"])

    def test_a_missing_extra_or_other_nested_subvolume(self):
        for listed in (["@", "@home", "@log", "@factory"],
                       ["@", "@home", "@log", "@pkg", "@factory", "@swap"],
                       ["@", "@home", "@log", "@pkg", "@factory", "@/.snapshots", "@/.snapshots/1/snapshot"],
                       ["@", "@home", "@log", "@pkg", "@factory", "@factory/.snapshots"]):
            with self.subTest(listed), self.assertRaisesRegex(check.CheckError, "subvolumes"):
                check.check_subvolumes(listed)


if __name__ == "__main__":
    unittest.main()
