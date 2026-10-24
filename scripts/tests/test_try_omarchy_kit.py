import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("try_omarchy_kit", ROOT / "scripts/try_omarchy_kit.py")
kit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kit)


class TryOmarchyKitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.directory)
        (self.directory / "Packaging").mkdir()
        shutil.copytree(ROOT / "Vendor/TryOmarchyKitClient", self.directory / "Vendor/TryOmarchyKitClient")
        shutil.copyfile(ROOT / "Packaging/try-omarchy-kit.json", self.directory / "Packaging/try-omarchy-kit.json")

    def pin(self) -> dict:
        return json.loads((self.directory / "Packaging/try-omarchy-kit.json").read_text())

    def write_pin(self, pin: dict) -> None:
        (self.directory / "Packaging/try-omarchy-kit.json").write_text(json.dumps(pin))

    def pinned_kit(self, **changes) -> dict:
        """The checkout's pin with a consistent kit, then CHANGES to that kit."""
        pin = self.pin()
        pin["kit"] = {"release": "v1.2.3", "revision": pin["client"]["revision"], "url": kit.kit_url("v1.2.3"),
                      "bytes": 1, "sha256": "0" * 64, "version": "v1.2.3",
                      "runProtocolVersion": kit.protocol_version(kit.VENDOR), **changes}
        return pin

    def test_the_checkout_pin_is_consistent(self) -> None:
        pin = kit.check(ROOT)
        if pin["kit"] is not None:
            self.assertEqual(pin["kit"]["revision"], pin["client"]["revision"])
            self.assertEqual(pin["kit"]["version"], pin["kit"]["release"])
            self.assertEqual(pin["kit"]["runProtocolVersion"], kit.protocol_version(kit.VENDOR))

    def test_the_vendored_client_matches_its_pin(self) -> None:
        kit.check(ROOT)
        kit.check(self.directory)

    def test_a_changed_client_is_refused(self) -> None:
        source = self.directory / "Vendor/TryOmarchyKitClient/VMRunProtocol.swift"
        source.write_text(source.read_text() + "\n")
        with self.assertRaisesRegex(ValueError, "differs from the pinned client"):
            kit.check(self.directory)

    def test_the_pin_is_validated(self) -> None:
        good = self.pin()
        for change in (
            lambda pin: pin.update(format=2),
            lambda pin: pin["client"].update(repository="someone/fork"),
            lambda pin: pin["client"].update(revision="main"),
            lambda pin: pin.update(kit=self.pinned_kit(url="https://example.com/TryOmarchyKit.zip")["kit"]),
            lambda pin: pin.update(kit=self.pinned_kit(bytes=True)["kit"]),
            lambda pin: pin.update(kit=self.pinned_kit(revision="main")["kit"]),
            lambda pin: pin.update(kit=self.pinned_kit(runProtocolVersion=0)["kit"]),
            lambda pin: pin.update(kit=self.pinned_kit(runProtocolVersion=True)["kit"]),
            lambda pin: pin.update(kit={key: value for key, value in self.pinned_kit()["kit"].items()
                                        if key != "version"}),
        ):
            pin = copy.deepcopy(good)
            change(pin)
            self.write_pin(pin)
            with self.assertRaises(ValueError):
                kit.check(self.directory)
        self.write_pin(self.pinned_kit())
        self.assertEqual(kit.check(self.directory)["kit"]["release"], "v1.2.3")

    def test_a_development_kit_is_never_pinned(self) -> None:
        for version in ("development", "v1.2.4"):
            self.write_pin(self.pinned_kit(version=version))
            with self.assertRaisesRegex(ValueError, "is not its release"):
                kit.check(self.directory)

    def test_the_kit_and_the_client_come_from_one_revision(self) -> None:
        self.write_pin(self.pinned_kit(revision="1" * 40))
        with self.assertRaisesRegex(ValueError, "different revisions"):
            kit.check(self.directory)

    def test_the_kit_speaks_the_vendored_clients_protocol(self) -> None:
        self.write_pin(self.pinned_kit(runProtocolVersion=kit.protocol_version(kit.VENDOR) + 1))
        with self.assertRaisesRegex(ValueError, "run-mode protocol"):
            kit.check(self.directory)

    def test_the_protocol_version_is_read_from_the_client(self) -> None:
        source = self.directory / "Vendor/TryOmarchyKitClient/VMRunProtocol.swift"
        self.assertGreaterEqual(kit.protocol_version(source.parent), 1)
        source.write_text(source.read_text().replace("public static let version =", "public static let v ="))
        with self.assertRaisesRegex(ValueError, "cannot find VMRunProtocol.version"):
            kit.protocol_version(source.parent)

    def test_a_release_manifest_must_name_its_release(self) -> None:
        valid = {"format": 1, "version": "v1.2.3", "runProtocolVersion": 1, "buildDate": "2026-10-23T09:30:05Z"}
        self.assertEqual(kit.kit_manifest(valid, "v1.2.3"), {"version": "v1.2.3", "runProtocolVersion": 1})
        for change in ({"format": 2}, {"format": True}, {"runProtocolVersion": 0},
                       {"runProtocolVersion": "1"}, {"version": "development"}, {"version": "v1.2.4"}):
            with self.assertRaises(ValueError):
                kit.kit_manifest({**valid, **change}, "v1.2.3")
        with self.assertRaises(ValueError):
            kit.kit_manifest([valid], "v1.2.3")

    def test_the_online_check_compares_the_release_with_the_pin(self) -> None:
        pin = self.pinned_kit()
        self.write_pin(pin)
        published = {"format": 1, "version": "v1.2.3", "runProtocolVersion": pin["kit"]["runProtocolVersion"],
                     "buildDate": "2026-10-23T09:30:05Z"}
        tag = pin["kit"]["revision"]
        original = (kit.tag_revision, kit.download)
        self.addCleanup(lambda: (setattr(kit, "tag_revision", original[0]), setattr(kit, "download", original[1])))
        kit.tag_revision = lambda release: tag
        kit.download = lambda url, destination: destination.write_text(json.dumps(published))
        self.assertEqual(kit.check_online(self.directory)["kit"]["release"], "v1.2.3")
        published["runProtocolVersion"] += 1
        with self.assertRaisesRegex(ValueError, "differs from the pin"):
            kit.check_online(self.directory)
        published["runProtocolVersion"] -= 1
        tag = "1" * 40
        with self.assertRaisesRegex(ValueError, "no longer names"):
            kit.check_online(self.directory)

    def test_without_a_pinned_kit_there_is_nothing_to_embed(self) -> None:
        pin = self.pin()
        pin["kit"] = None
        self.write_pin(pin)
        self.assertIsNone(kit.fetch(self.directory / "cache", self.directory, {}))

    def test_a_local_kit_must_match_the_digest_beside_it(self) -> None:
        archive = self.directory / "TryOmarchyKit.zip"
        archive.write_bytes(b"kit")
        sidecar = Path(f"{archive}.sha256")
        sidecar.write_text(f"{hashlib.sha256(b'kit').hexdigest()}  TryOmarchyKit.zip\n")
        environment = {"OMARCHY_TRY_KIT_ZIP": str(archive)}
        self.assertEqual(kit.fetch(self.directory / "cache", self.directory, environment), archive.resolve())
        sidecar.write_text("0" * 64 + "  TryOmarchyKit.zip\n")
        with self.assertRaisesRegex(ValueError, "does not match"):
            kit.fetch(self.directory / "cache", self.directory, environment)

    def test_a_cached_kit_is_used_when_it_matches_the_pin(self) -> None:
        cache = self.directory / "cache"
        cache.mkdir()
        digest = hashlib.sha256(b"kit").hexdigest()
        (cache / f"{digest}.zip").write_bytes(b"kit")
        self.write_pin(self.pinned_kit(bytes=3, sha256=digest))
        self.assertEqual(kit.fetch(cache, self.directory, {}), cache / f"{digest}.zip")



@unittest.skipUnless(Path("/usr/bin/ditto").exists(), "kit archives are made and unpacked with macOS's ditto")
class KitArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.directory)
        self.source = self.directory / "source"

    def kit_folder(self, name: str = kit.KIT_BUNDLE, **info: object) -> Path:
        bundle = self.source / name
        helper = bundle / kit.KIT_HELPER
        helper.parent.mkdir(parents=True)
        helper.write_text("#!/bin/sh\n")
        helper.chmod(0o755)
        (bundle / kit.KIT_MANIFEST).parent.mkdir(parents=True)
        (bundle / kit.KIT_MANIFEST).write_text('{"format": 1}')
        (bundle / "Contents/Info.plist").write_bytes(plistlib.dumps(
            {"CFBundleExecutable": "omarchy-vm-helper", "CFBundlePackageType": "BNDL", **info}))
        daemon = bundle / kit.KIT_NETWORK_DAEMON
        daemon.write_text("#!/bin/sh\n")
        daemon.chmod(0o755)
        self.write_job(bundle)
        return bundle

    def write_job(self, bundle: Path, **changes: object) -> None:
        job = bundle / kit.KIT_NETWORK_JOB
        job.parent.mkdir(parents=True, exist_ok=True)
        job.write_bytes(plistlib.dumps({
            "Label": "dev.tryomarchy.kit.network", "MachServices": {"dev.tryomarchy.kit.network": True},
            "BundleProgram": kit.KIT_NETWORK_DAEMON, "RunAtLoad": False, **changes}))

    def archive(self) -> Path:
        archive = self.directory / kit.KIT_NAME
        archive.unlink(missing_ok=True)
        subprocess.run(["/usr/bin/ditto", "-c", "-k", str(self.source), str(archive)], check=True)
        return archive

    def test_a_kit_unpacks_into_its_bundle_folder(self) -> None:
        self.kit_folder()
        helpers = self.directory / "app/Contents/Helpers"
        unpacked = kit.unpack(self.archive(), helpers)
        self.assertEqual(unpacked, helpers / "TryOmarchyKit.bundle")
        self.assertEqual(kit.kit_bundle(helpers), unpacked)
        self.assertEqual(sorted(entry.name for entry in helpers.iterdir()), ["TryOmarchyKit.bundle"])
        self.assertTrue((unpacked / "Contents/MacOS/omarchy-vm-helper").stat().st_mode & 0o111)
        with self.assertRaisesRegex(ValueError, "already exists"):
            kit.unpack(self.archive(), helpers)

    def test_embedding_registers_the_kits_daemon_as_the_app(self) -> None:
        self.kit_folder()
        app = self.directory / "Installer.app"
        bundle = kit.embed(self.archive(), app)
        self.assertEqual(bundle, app / kit.EMBEDDED_KIT)
        job = plistlib.loads((app / "Contents/Library/LaunchDaemons/dev.tryomarchy.kit.network.plist").read_bytes())
        self.assertEqual(job["BundleProgram"],
                         "Contents/Helpers/TryOmarchyKit.bundle/Contents/MacOS/omarchy-network-daemon")
        self.assertEqual((job["Label"], job["MachServices"], job["RunAtLoad"]),
                         ("dev.tryomarchy.kit.network", {"dev.tryomarchy.kit.network": True}, False))
        self.assertTrue((app / job["BundleProgram"]).is_file())

    def test_a_networking_job_that_names_another_service_or_program_is_refused(self) -> None:
        for changes in ({"Label": "../escape"}, {"MachServices": {"dev.tryomarchy.network": True}},
                        {"BundleProgram": "Contents/MacOS/omarchy-vm-helper"}):
            with self.subTest(changes=changes):
                shutil.rmtree(self.source, ignore_errors=True)
                self.write_job(self.kit_folder(), **changes)
                app = self.directory / "app.app"
                shutil.rmtree(app, ignore_errors=True)
                with self.assertRaisesRegex(ValueError, "does not name its service and daemon"):
                    kit.embed(self.archive(), app)
        shutil.rmtree(self.source)
        (self.kit_folder() / kit.KIT_NETWORK_DAEMON).unlink()
        shutil.rmtree(self.directory / "app.app")
        with self.assertRaisesRegex(ValueError, "no networking daemon"):
            kit.embed(self.archive(), self.directory / "app.app")

    def test_a_networking_job_that_sets_other_keys_is_refused(self) -> None:
        for changes in ({"Program": "/bin/sh"}, {"ProgramArguments": ["/bin/sh", "-c", "id"]},
                        {"UserName": "root"}, {"KeepAlive": True}):
            with self.subTest(changes=changes):
                shutil.rmtree(self.source, ignore_errors=True)
                self.write_job(self.kit_folder(), **changes)
                app = self.directory / "app.app"
                shutil.rmtree(app, ignore_errors=True)
                with self.assertRaisesRegex(ValueError, "sets keys it may not"):
                    kit.embed(self.archive(), app)
                self.assertFalse((app / "Contents/Library/LaunchDaemons").exists())

    def test_an_archive_with_anything_beside_the_kit_is_refused(self) -> None:
        self.kit_folder()
        (self.source / "extra").write_text("extra")
        with self.assertRaisesRegex(ValueError, "must hold only TryOmarchyKit.bundle"):
            kit.unpack(self.archive(), self.directory / "out")
        self.assertEqual(list((self.directory / "out").iterdir()), [])

    def test_the_old_kit_app_is_refused(self) -> None:
        self.kit_folder("Try Omarchy Kit.app")
        with self.assertRaisesRegex(ValueError, "must hold only TryOmarchyKit.bundle"):
            kit.unpack(self.archive(), self.directory / "out")

    def test_a_kit_with_a_bundle_identity_is_refused(self) -> None:
        for key in ("CFBundleIdentifier", "CFBundleName", "CFBundleDisplayName"):
            with self.subTest(key=key):
                shutil.rmtree(self.source, ignore_errors=True)
                self.kit_folder(**{key: "dev.tryomarchy.kit"})
                with self.assertRaisesRegex(ValueError, f"bundle identity of its own \\({key}\\)"):
                    kit.unpack(self.archive(), self.directory / "out")
        self.assertEqual(list((self.directory / "out").iterdir()), [])

    def test_a_kit_without_its_helper_or_manifest_is_refused(self) -> None:
        for missing, message in ((kit.KIT_HELPER, "no helper"), (kit.KIT_MANIFEST, "no manifest"),
                                 ("Contents/Info.plist", "does not name its helper")):
            with self.subTest(missing=missing):
                shutil.rmtree(self.source, ignore_errors=True)
                (self.kit_folder() / missing).unlink()
                with self.assertRaisesRegex(ValueError, message):
                    kit.unpack(self.archive(), self.directory / "out")


if __name__ == "__main__":
    unittest.main()
