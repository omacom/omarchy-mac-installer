"""check-try-kit accepts only a kit signed as the installer, with its entitlements and a release manifest."""
from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "check-try-kit"


def load_check():
    loader = importlib.machinery.SourceFileLoader("check_try_kit", str(SCRIPT))
    spec = importlib.util.spec_from_loader("check_try_kit", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


check = load_check()
IDENTIFIER = check.identity()["INSTALLER_APP_IDENTIFIER"] + ".vm-kit"
PROTOCOL = check.protocol_version()
MACHO = b"\xcf\xfa\xed\xfe" + b"\0" * 28


def entitlements(name: str) -> dict:
    return plistlib.loads((check.ENTITLEMENTS / name).read_bytes())


class FakeCodesign:
    """codesign's answers for the paths it has signatures for."""

    def __init__(self) -> None:
        self.signatures: dict[str, dict] = {}
        self.invalid: set[str] = set()

    def sign(self, path: Path, identifier: str = IDENTIFIER, runtime: bool = True,
             entitlements: dict | None = None, adhoc: bool = False) -> None:
        self.signatures[str(path)] = {"identifier": identifier, "runtime": runtime,
                                      "entitlements": entitlements or {}, "adhoc": adhoc}

    def __call__(self, *arguments: str) -> subprocess.CompletedProcess:
        path = arguments[-1]
        signature = self.signatures.get(path)
        if arguments[0] == "--verify":
            return subprocess.CompletedProcess(arguments, 1 if path in self.invalid else 0, b"", b"")
        if signature is None:
            return subprocess.CompletedProcess(arguments, 1, b"", b"code object is not signed at all\n")
        if "--entitlements" in arguments:
            data = plistlib.dumps(signature["entitlements"]) if signature["entitlements"] else b""
            return subprocess.CompletedProcess(arguments, 0, data, b"")
        flags = "0x10000(runtime)" if signature["runtime"] else "0x2(adhoc)"
        details = f"Executable={path}\nIdentifier={signature['identifier']}\nCodeDirectory v=20500 flags={flags}\n"
        details += "Signature=adhoc\n" if signature["adhoc"] else "Authority=Developer ID Application: Test (TEAM123456)\n"
        return subprocess.CompletedProcess(arguments, 0, b"", details.encode())


class CheckTryKitTest(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.app = self.root / f"{check.identity()['INSTALLER_APP_NAME']}.app"
        self.kit = self.app / check.KIT
        self.codesign = FakeCodesign()
        self.pin_path = self.root / "try-omarchy-kit.json"
        self.pin(None)
        self.daemon_answer = check.NETWORK_SERVICE
        self.qemu_starts = True
        for patch in (mock.patch.object(check, "codesign", self.codesign),
                      mock.patch.object(check, "network_daemon_check", lambda daemon: self.daemon_answer),
                      mock.patch.object(check, "qemu_starts", lambda qemu: self.qemu_starts),
                      mock.patch.object(check, "PIN", self.pin_path),
                      mock.patch.dict(os.environ, {}, clear=False)):
            patch.start()
            self.addCleanup(patch.stop)
        os.environ.pop("OMARCHY_TRY_KIT_ZIP", None)
        os.environ.pop("GITHUB_ACTIONS", None)
        (self.app / "Contents").mkdir(parents=True)
        (self.app / "Contents/Info.plist").write_bytes(plistlib.dumps({
            "NSCameraUsageDescription": "Omarchy uses the camera.",
            "NSMicrophoneUsageDescription": "Omarchy uses the microphone.",
        }))
        self.codesign.sign(self.app, "org.example.app", entitlements=entitlements("installer-app.entitlements"))

    def pin(self, release: str | None) -> None:
        kit = None if release is None else {"release": release}
        self.pin_path.write_text(json.dumps({"format": 1, "client": {}, "kit": kit}))

    def make_kit(self, version: str = "v1.2.3", protocol: int = PROTOCOL) -> None:
        resources = self.kit / "Contents/Resources"
        for relative, entitlement in ((check.QEMU, "vm-qemu.entitlements"), (check.HELPER, "vm-helper.entitlements"),
                                      (check.NETWORK_DAEMON, None),
                                      ("Contents/Resources/runtime/lib/libglib-2.0.0.dylib", None),
                                      ("Contents/Resources/runtime/bin/zstd", None)):
            path = self.kit / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(MACHO)
            self.codesign.sign(path, entitlements=entitlements(entitlement) if entitlement else None)
        for relative in sorted(check.NETWORK_PINNED):
            path = self.kit / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(MACHO)
            self.codesign.sign(path, identifier="dev.tryomarchy.kit", runtime=False)
        job = self.app / f"Contents/Library/LaunchDaemons/{check.NETWORK_SERVICE}.plist"
        job.parent.mkdir(parents=True, exist_ok=True)
        job.write_bytes(plistlib.dumps({
            "Label": check.NETWORK_SERVICE, "MachServices": {check.NETWORK_SERVICE: True},
            "BundleProgram": f"{check.KIT}/{check.NETWORK_DAEMON}"}))
        (resources / "scripts").mkdir(exist_ok=True)
        (resources / "scripts/run-qemu-gpu.sh").write_text("#!/bin/bash\n")
        (resources / "kit.json").write_text(json.dumps({
            "format": 1, "version": version, "runProtocolVersion": protocol, "buildDate": "2026-10-23T09:30:05Z"}))
        (self.kit / "Contents/Info.plist").write_bytes(plistlib.dumps({
            "CFBundleExecutable": "omarchy-vm-helper", "CFBundlePackageType": "BNDL", "LSUIElement": True}))
        self.codesign.sign(self.kit)

    def run_check(self) -> tuple[int, str, str]:
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            status = check.Check(self.app).run()
        return status, output.getvalue(), errors.getvalue()

    def refused(self, finding: str) -> None:
        status, _, errors = self.run_check()
        self.assertEqual(status, 1, errors)
        self.assertIn(finding, errors)

    def test_without_a_pinned_kit_the_app_has_none_and_says_so(self) -> None:
        status, output, errors = self.run_check()
        self.assertEqual((status, errors), (0, ""))
        self.assertIn("No Try Omarchy kit is pinned", output)
        os.environ["GITHUB_ACTIONS"] = "true"
        self.assertTrue(self.run_check()[1].startswith("::notice::"))

    def test_a_kit_nobody_pinned_is_refused(self) -> None:
        self.make_kit()
        self.refused("pins no kit")

    def test_a_pinned_kit_signed_as_the_installer_passes(self) -> None:
        self.pin("v1.2.3")
        self.make_kit()
        status, output, errors = self.run_check()
        self.assertEqual((status, errors), (0, ""))
        self.assertIn(f"kit v1.2.3, run-mode protocol {PROTOCOL}, 5 Mach-O files signed as {IDENTIFIER}", output)

    def test_a_pinned_kit_must_be_in_the_app(self) -> None:
        self.pin("v1.2.3")
        self.refused("carries no kit")

    def test_a_local_kit_is_checked_like_a_pinned_one(self) -> None:
        os.environ["OMARCHY_TRY_KIT_ZIP"] = str(self.root / "TryOmarchyKit.zip")
        self.refused("carries no kit")
        self.make_kit()
        self.assertEqual(self.run_check()[0], 0)

    def test_every_macho_is_signed_as_the_installer_with_the_runtime(self) -> None:
        self.pin("v1.2.3")
        self.make_kit()
        library = self.kit / "Contents/Resources/runtime/lib/libglib-2.0.0.dylib"
        self.codesign.sign(library, identifier="dev.tryomarchy.kit")
        self.refused("libglib-2.0.0.dylib is not signed as")
        self.codesign.sign(library, runtime=False)
        self.refused("libglib-2.0.0.dylib is not signed with the hardened runtime")
        del self.codesign.signatures[str(library)]
        self.refused("libglib-2.0.0.dylib is not signed")
        self.codesign.sign(library)
        self.codesign.invalid.add(str(self.kit))
        self.refused("the kit does not verify")

    def test_the_kit_has_no_bundle_identity_of_its_own(self) -> None:
        self.pin("v1.2.3")
        self.make_kit()
        info = self.kit / "Contents/Info.plist"
        for key in ("CFBundleIdentifier", "CFBundleName", "CFBundleDisplayName"):
            with self.subTest(key=key):
                info.write_bytes(plistlib.dumps({"CFBundleExecutable": "omarchy-vm-helper", key: "dev.tryomarchy.kit"}))
                self.refused(f"the kit has a bundle identity of its own ({key})")
        self.make_kit()
        self.codesign.sign(self.kit, identifier="dev.tryomarchy.kit")
        self.refused(f"the kit's bundle is not signed as {IDENTIFIER}")
        info.unlink()
        self.refused("the kit's Info.plist is missing")

    def test_an_ad_hoc_kit_needs_no_hardened_runtime_but_qemu_must_start(self) -> None:
        self.pin("v1.2.3")
        self.make_kit()
        for path, signature in list(self.codesign.signatures.items()):
            if signature["identifier"] == IDENTIFIER:
                signature.update(runtime=False, adhoc=True)
        self.assertEqual(self.run_check()[0], 0)
        self.qemu_starts = False
        self.refused("runtime/bin/Try Omarchy does not start from the signed app with its libraries")

    def test_the_networking_runtime_keeps_the_kits_signatures_and_the_app_registers_its_daemon(self) -> None:
        self.pin("v1.2.3")
        self.make_kit()
        client = self.kit / "Contents/Resources/network/omarchy-network-client"
        self.codesign.sign(client)
        self.refused("omarchy-network-client was re-signed")
        client.unlink()
        self.refused("the kit has no Contents/Resources/network/omarchy-network-client")
        self.make_kit()
        self.codesign.sign(self.kit / check.NETWORK_DAEMON, identifier="dev.tryomarchy.kit")
        self.refused("omarchy-network-daemon is not signed as")
        self.make_kit()
        self.daemon_answer = None
        self.refused("networking daemon does not accept its runtime")
        self.daemon_answer = check.NETWORK_SERVICE
        self.qemu_starts = True
        job = self.app / f"Contents/Library/LaunchDaemons/{check.NETWORK_SERVICE}.plist"
        job.write_bytes(plistlib.dumps({
            "Label": check.NETWORK_SERVICE, "MachServices": {check.NETWORK_SERVICE: True},
            "BundleProgram": "Contents/MacOS/omarchy-network-daemon"}))
        self.refused("job does not name the kit's service and daemon")
        job.write_bytes(plistlib.dumps({
            "Label": check.NETWORK_SERVICE, "MachServices": {check.NETWORK_SERVICE: True},
            "BundleProgram": f"{check.KIT}/{check.NETWORK_DAEMON}", "ProgramArguments": ["/bin/sh"]}))
        self.refused("job sets keys it may not: ProgramArguments")
        job.unlink()
        self.refused(f"the app has no Contents/Library/LaunchDaemons/{check.NETWORK_SERVICE}.plist")

    def test_qemu_and_the_helper_carry_their_entitlements_and_nothing_else_does(self) -> None:
        self.pin("v1.2.3")
        self.make_kit()
        self.codesign.sign(self.kit / check.QEMU, entitlements=entitlements("vm-helper.entitlements"))
        self.refused("runtime/bin/Try Omarchy carries other entitlements")
        self.codesign.sign(self.kit / check.QEMU, entitlements=entitlements("vm-qemu.entitlements"))
        self.codesign.sign(self.kit / check.HELPER)
        self.refused("omarchy-vm-helper carries other entitlements")
        self.codesign.sign(self.kit / check.HELPER, entitlements=entitlements("vm-helper.entitlements"))
        self.codesign.sign(self.kit / "Contents/Resources/runtime/bin/zstd",
                           entitlements={"com.apple.security.get-task-allow": True})
        self.refused("zstd carries other entitlements")
        (self.kit / check.QEMU).unlink()
        self.refused("the kit has no Contents/Resources/runtime/bin/Try Omarchy")

    def test_the_kit_manifest_names_a_release_the_client_can_run(self) -> None:
        self.pin("v1.2.3")
        self.make_kit(version="development")
        self.refused("'development', not a release")
        self.make_kit(version="v1.2.4")
        self.refused("not the pinned v1.2.3")
        self.make_kit(protocol=PROTOCOL + 1)
        self.refused(f"run-mode protocol {PROTOCOL + 1}")
        (self.kit / "Contents/Resources/kit.json").write_text(json.dumps({"format": 2}))
        self.refused("not a format 1 manifest")
        (self.kit / "Contents/Resources/kit.json").unlink()
        self.refused("kit.json is missing")

    def test_the_app_describes_the_camera_and_microphone_and_is_signed_for_them(self) -> None:
        (self.app / "Contents/Info.plist").write_bytes(plistlib.dumps({"NSCameraUsageDescription": "Camera."}))
        self.refused("no NSMicrophoneUsageDescription")
        (self.app / "Contents/Info.plist").write_bytes(plistlib.dumps({"NSMicrophoneUsageDescription": "Mic."}))
        self.refused("no NSCameraUsageDescription")
        self.codesign.sign(self.app, "org.example.app")
        self.refused("not signed with installer-app.entitlements")


if __name__ == "__main__":
    unittest.main()
