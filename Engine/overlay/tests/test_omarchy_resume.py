# SPDX-License-Identifier: MIT
import hashlib
import contextlib
import os
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from omarchy_contract import Journal
from omarchy_resume import (
    BOUND_CHECKPOINT, REBOUND_CHECKPOINT, REBOUND_CHECKPOINT_V2, PREVIOUS_GUARD_ENGINE,
    PREVIOUS_RESUME_ENGINES, BOOTABILITY_FRAMEWORK_LINKS,
    ORIGINAL_ENGINES, ResumeError, original_plan, resume_stage1, verify_input, protected_stub_trees,
)
from omarchy_stage1 import AmbiguousMutationState, run_stage1


class InterruptedResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / ("a" * 64 + ".jsonl")
        self.journal = Journal(str(self.path))
        self.journal.inspection("apple,j414s", "supported")
        layout = self.journal.inventory("disk0", [{
            "kind": "free", "source_identifier": "disk0s3", "offset_bytes": 400_000_000_000,
            "length_bytes": 100_000_000_000, "minimum_install_bytes": 40_000_000_000,
            "minimum_container_bytes": 0,
        }])
        version, digest = next(iter(ORIGINAL_ENGINES.items()))
        self.journal.plan(device_identifier="apple,j414s", layout_digest=layout,
            candidate_kind="free", source_identifier="disk0s3", requested_length_bytes=100_000_000_000,
            engine_version=version, engine_digest=digest,
            metadata_digest="sha256:" + "e" * 64, payload_digest="sha256:" + "f" * 64,
            required_human_steps=["enterOneTrueRecovery", "authenticateMachineOwner"])
        self.journal.event("apfs_preparation_started")
        self.journal.checkpoint("apfs-target-prepared", "apfs_preparation", b"original target")
        self.journal.event("stub_and_esp_started")

    def test_normal_retry_still_refuses_ambiguous_stage(self):
        adapter = ResumeAdapter(self.journal)
        with self.assertRaises(AmbiguousMutationState):
            run_stage1(original_plan(self.journal), self.journal, adapter)
        self.assertEqual(adapter.calls, [])

    def test_resume_binds_before_writing_and_never_replays_partition_or_image_writes(self):
        adapter = ResumeAdapter(self.journal)
        plan = original_plan(self.journal)
        self.assertEqual(resume_stage1(plan, self.journal, adapter), "awaiting_recovery")
        self.assertEqual(adapter.calls, ["validate", "finish", "handoff"])
        self.assertEqual(Journal(str(self.path)).completion_outcome, "awaiting_recovery")

    def test_restart_during_staging_uses_durable_binding(self):
        first = ResumeAdapter(self.journal, fail="finish")
        with self.assertRaisesRegex(RuntimeError, "interrupted"):
            resume_stage1(original_plan(self.journal), self.journal, first)
        resumed = Journal(str(self.path))
        self.assertNotIn("stub-and-esp-installed", resumed.checkpoints)
        self.assertIn(BOUND_CHECKPOINT, resumed.checkpoints)
        second = ResumeAdapter(resumed)
        resume_stage1(original_plan(resumed), resumed, second)
        self.assertEqual(second.previous, b"verified original partitions and images")
        self.assertEqual(second.calls, ["validate", "finish", "handoff"])

    def test_handoff_failure_and_lost_success_reply_do_not_rebuild_staging(self):
        first = ResumeAdapter(self.journal, fail="handoff")
        with self.assertRaises(RuntimeError):
            resume_stage1(original_plan(self.journal), self.journal, first)
        for completed in (False, True):
            resumed = Journal(str(self.path))
            adapter = ResumeAdapter(resumed)
            resume_stage1(original_plan(resumed), resumed, adapter)
            self.assertEqual(adapter.calls, ["validate_installed"] + ([] if completed else ["handoff"]))

    def test_rebinding_preserves_original_evidence_and_is_replayable(self):
        old = b"original guard engine target binding"
        self.journal.checkpoint(BOUND_CHECKPOINT, "stub_and_esp", old)
        earlier = b"framework guard engine target binding"
        self.journal.checkpoint(REBOUND_CHECKPOINT, "stub_and_esp", earlier)
        adapter = ResumeAdapter(self.journal, fail="finish")
        with self.assertRaisesRegex(RuntimeError, "interrupted"):
            resume_stage1(original_plan(self.journal), self.journal, adapter)
        resumed = Journal(str(self.path))
        self.assertEqual(resumed.checkpoint_evidence(BOUND_CHECKPOINT), old)
        self.assertEqual(resumed.checkpoint_evidence(REBOUND_CHECKPOINT), earlier)
        latest = REBOUND_CHECKPOINT + "-" + hashlib.sha256(
            b"verified original partitions and images").hexdigest()
        self.assertEqual(resumed.checkpoint_evidence(latest),
                         b"verified original partitions and images")
        next_adapter = ResumeAdapter(resumed)
        resume_stage1(original_plan(resumed), resumed, next_adapter)
        self.assertEqual(next_adapter.previous, resumed.checkpoint_evidence(latest))

    def test_changed_partition_or_image_blocks_before_mutation(self):
        adapter = ResumeAdapter(self.journal, fail="validate")
        with self.assertRaises(RuntimeError):
            resume_stage1(original_plan(self.journal), self.journal, adapter)
        self.assertEqual(adapter.calls, ["validate"])
        self.assertNotIn(BOUND_CHECKPOINT, self.journal.checkpoints)

    def test_unrelated_mutation_or_unbound_completion_is_not_resumable(self):
        self.journal.event("unrelated_mutation_started")
        with self.assertRaises(ResumeError):
            original_plan(self.journal)

    def test_inputs_require_original_hash_and_private_snapshot(self):
        source = Path(self.temp.name) / "payload"
        source.write_bytes(b"original payload")
        source.chmod(0o600)
        expected = "sha256:" + hashlib.sha256(source.read_bytes()).hexdigest()
        verify_input(source, expected)
        source.write_bytes(b"different payload")
        with self.assertRaisesRegex(ResumeError, "differs"):
            verify_input(source, expected)
        source.chmod(0o644)
        with self.assertRaisesRegex(ResumeError, "private"):
            verify_input(source, expected)


class ResumeAdapter:
    def __init__(self, journal, fail=None):
        self.journal = journal
        self.fail = fail
        self.calls = []
        self.previous = None

    def validate_interrupted_staging(self, plan, target, previous):
        self.calls.append("validate")
        self.previous = previous
        if self.fail == "validate":
            raise RuntimeError("changed image")
        return b"verified original partitions and images"

    @contextlib.contextmanager
    def protect_resume(self, plan):
        self.protected = True
        try:
            yield
        finally:
            self.protected = False

    def resume_interrupted_staging(self, plan, binding):
        assert self.protected
        self.calls.append("finish")
        saved = Journal(self.journal.path)
        latest = [name for name in saved.checkpoints
                  if name == BOUND_CHECKPOINT or name.startswith(REBOUND_CHECKPOINT)][-1]
        assert saved.checkpoint_evidence(latest) == binding
        if self.fail == "finish":
            raise RuntimeError("interrupted staging")
        return b"completed staging evidence"

    def validate_installed_checkpoint(self, *args):
        self.calls.append("validate_installed")

    def prepare_recovery_handoff(self, plan):
        assert self.protected
        self.calls.append("handoff")
        if self.fail == "handoff":
            raise RuntimeError("interrupted handoff")
        return b"handoff evidence"

    def prepare_target(self, plan):
        raise AssertionError("resume repeated APFS preparation")

    def install_stub_and_esp(self, plan):
        raise AssertionError("resume repeated partitioning or image installation")


class InterruptedTargetValidationTests(unittest.TestCase):
    def setUp(self):
        # Reuse the existing adapter's disk/ZIP fixture, with no live devices.
        from test_omarchy_asahi import AsahiStage1AdapterTests, FakeDiskUtil, FakeInstaller, FakePart, GIB
        self.fixture = AsahiStage1AdapterTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.plan = self.fixture.plan
        sizes = [2 * GIB, 500 * 1024**2, 2 * GIB,
                 self.plan.length_bytes - 4 * GIB - 500 * 1024**2]
        offset = self.plan.offset_bytes
        self.parts = []
        for index, (size, kind) in enumerate(zip(sizes, (
                "Apple_APFS", "EFI", "Linux Filesystem", "Linux Filesystem"))):
            part = FakePart(f"disk0s{4 + index}", offset=offset, size=size, free=False,
                            part_type=kind, uuid=f"{index + 1:08x}-1111-4111-8111-111111111111")
            self.parts.append(part)
            offset += size
        self.parts[0].container = {"Volumes": [{"Roles": [role]} for role in (
            "System", "Data", "Preboot", "Recovery")]}
        self.installer = FakeInstaller(FakeDiskUtil([self.parts]))
        self.installer.cur_os = SimpleNamespace(vgid="ffffffff-1111-4111-8111-111111111111")
        self.adapter = self.fixture._adapter(self.installer)
        self.adapter.preflight(self.plan)
        self.adapter.native_recovery_gui = True
        self.adapter.authorized_owner = "alice"
        self.adapter.resume_engine_digest = "sha256:" + "1" * 64
        self.adapter.osins.min_size = 34 * GIB + 500 * 1024**2
        self.adapter.osins.PART_ALIGNMENT = 1024**2
        self.adapter.osins.align = lambda n: n
        self.installer.ins.get_paths = lambda: None
        self.installer.ins.osi.system = str(self.fixture.root / "system")
        self.addCleanup(self.installer.ins.recovery.cleanup)
        self.adapter._resume_boot_object = lambda uuid: b"original EFI-bound boot object"
        Path(self.installer.ins.boot_obj_path).write_bytes(b"original EFI-bound boot object")
        self.fixture.installed_stub_icon.write_bytes(b"omarchy-icon")
        self.adapter.target_part = self.parts[0]
        self.target = self.adapter._target_evidence(self.plan)
        values = {"500MB": 500 * 1024**2, "2GB": 2 * GIB, "32GB": 32 * GIB}
        self.sizes = patch("omarchy_asahi.osinstall.psize", values.__getitem__)
        self.sizes.start()
        self.addCleanup(self.sizes.stop)
        self.signature = patch("omarchy_asahi.omarchy_recovery.validate_source")
        self.signature.start()
        self.addCleanup(self.signature.stop)

    def test_exact_target_full_images_and_boot_binding_are_required(self):
        bound = self.adapter.validate_interrupted_staging(self.plan, self.target)
        record = json.loads(bound)
        self.assertEqual(record["partitions"][0]["uuid"], self.parts[1].uuid)
        self.assertEqual(set(record["image_receipts"]), {"boot.img", "root.img"})
        self.assertEqual(self.installer.dutil.resize_calls, [])
        self.assertEqual(self.installer.dutil.add_calls, [])
        self.assertEqual(self.adapter.osins.calls, [])

    def test_only_known_guard_engine_binding_can_upgrade_with_identical_target(self):
        from omarchy_asahi import AsahiAdapterError
        self.adapter.resume_engine_digest = PREVIOUS_GUARD_ENGINE
        old = self.adapter.validate_interrupted_staging(self.plan, self.target)
        self.adapter.resume_engine_digest = "sha256:" + "2" * 64
        new = self.adapter.validate_interrupted_staging(self.plan, self.target, old)
        expected = json.loads(old)
        expected["replacement_engine_digest"] = self.adapter.resume_engine_digest
        self.assertEqual(json.loads(new), expected)
        self.adapter.authorized_owner = "another-owner"
        with self.assertRaisesRegex(AsahiAdapterError, "bound resume target changed"):
            self.adapter.validate_interrupted_staging(self.plan, self.target, old)
        self.adapter.authorized_owner = "alice"
        for digest in PREVIOUS_RESUME_ENGINES:
            prior = dict(json.loads(old), replacement_engine_digest=digest)
            rebound = self.adapter.validate_interrupted_staging(
                self.plan, self.target,
                json.dumps(prior, sort_keys=True, separators=(",", ":")).encode())
            self.assertEqual(rebound, new)
        self.adapter.resume_engine_digest = "sha256:" + "3" * 64
        with self.assertRaisesRegex(AsahiAdapterError, "bound resume target changed"):
            self.adapter.validate_interrupted_staging(self.plan, self.target, new)

    def test_changed_image_or_unbound_efi_is_rejected_before_any_staging(self):
        from omarchy_asahi import AsahiAdapterError
        self.fixture.raw_images["disk0s7"] = b"different image"
        with self.assertRaisesRegex(AsahiAdapterError, "differs|truncated"):
            self.adapter.validate_interrupted_staging(self.plan, self.target)
        self.fixture.raw_images["disk0s7"] = b"r" * 4096
        Path(self.installer.ins.boot_obj_path).write_bytes(b"another EFI's boot object")
        with self.assertRaisesRegex(AsahiAdapterError, "bind this EFI"):
            self.adapter.validate_interrupted_staging(self.plan, self.target)
        self.assertEqual(self.adapter.osins.calls, [])

    def test_reconciled_uuid_and_owner_cannot_change_after_interruption(self):
        from omarchy_asahi import AsahiAdapterError
        bound = self.adapter.validate_interrupted_staging(self.plan, self.target)
        self.parts[2].uuid = "99999999-1111-4111-8111-111111111111"
        with self.assertRaisesRegex(AsahiAdapterError, "bound resume target changed"):
            self.adapter.validate_interrupted_staging(self.plan, self.target, bound)

    def test_bound_restart_can_rebuild_interrupted_boot_object_and_icon(self):
        bound = self.adapter.validate_interrupted_staging(self.plan, self.target)
        Path(self.installer.ins.boot_obj_path).unlink()
        self.fixture.installed_stub_icon.write_bytes(b"upstream stub rebuild's temporary icon")
        self.assertEqual(self.adapter.validate_interrupted_staging(self.plan, self.target, bound), bound)

    def test_wrong_apfs_identity_is_rejected(self):
        from omarchy_asahi import AsahiAdapterError
        self.parts[0].uuid = "99999999-1111-4111-8111-111111111111"
        with self.assertRaisesRegex(AsahiAdapterError, "prepared target identity"):
            self.adapter.validate_interrupted_staging(self.plan, self.target)

    def test_rebuild_restores_icon_and_uses_private_firmware_and_recovery_inputs(self):
        import omarchy_asahi
        self.installer.ins.osi.system = str(self.fixture.root / "system")
        self.adapter.osins.part_info = self.parts[1:]
        self.adapter.osins.efi_part = self.parts[1]
        system = Path(self.installer.ins.osi.system)
        (system / "Finish Installation.app").mkdir(parents=True)
        optional = self.fixture.installed_esp / "vendorfw/u-boot/asmedia/asm2214a-apple.bin"
        optional.parent.mkdir(parents=True)
        optional.write_bytes(b"injected optional firmware")
        # Add the small pinned app input used by the staging code. The
        # signature and its hashes are covered by Recovery's own suite.
        with omarchy_asahi.zipfile.ZipFile(self.fixture.payload, "a") as z:
            # A different board's file was removed during the original install.
            # Resume must consume the already filtered, verified EFI tree.
            z.writestr("esp/m1n1/boot-j700.bin", b"other board stage2")
            prefix = "esp/aurora/recovery/"
            name = "Contents/MacOS/OmarchyRecovery"
            z.writestr(prefix + "omarchy-recovery-app.json", json.dumps({"files": {name: {}}}))
            z.writestr(prefix + omarchy_asahi.omarchy_recovery.APP_NAME + "/" + name,
                       b"pinned recovery")
        self.adapter.osins.pkg.close()
        self.adapter.osins.pkg = omarchy_asahi.zipfile.ZipFile(self.fixture.payload)
        recovery_image = (Path(self.installer.ins.osi.recovery) / self.installer.ins.osi.vgid
                          / "usr/standalone/firmware/arm64eBaseSystem.dmg")
        recovery_image.parent.mkdir(parents=True)
        recovery_image.write_bytes(b"existing compressed recovery image")

        def install_files(current):
            self.assertFalse(recovery_image.exists())
            recovery_image.write_bytes(b"regenerated compressed recovery image")
            self.fixture.installed_stub_icon.write_bytes(b"upstream icon")

        self.installer.ins.install_files = install_files
        self.installer.ins.collect_firmware = lambda package: None
        self.installer.ins.collect_installer_data = lambda directory: None
        self.adapter.osins.extract_file = lambda name, path: Path(path).write_bytes(self.adapter.osins.pkg.read(name))

        class Firmware:
            def __init__(self, directory):
                self.directory = Path(directory)

            def close(self):
                for name in ("firmware.tar", "firmware.cpio", "manifest.txt"):
                    (self.directory / name).write_bytes(("fresh " + name).encode())

        def stage(*args, **kwargs):
            root = kwargs["payload_root"]
            self.assertEqual((root / "m1n1/boot.bin").read_bytes(), b"m1n1")
            self.assertEqual((root / "vendorfw/firmware.tar").read_bytes(), b"fresh firmware.tar")
            self.assertFalse((root / "vendorfw/u-boot/asmedia/asm2214a-apple.bin").exists())
            self.assertEqual((kwargs["app_source"] / "Contents/MacOS/OmarchyRecovery").read_bytes(),
                             b"pinned recovery")
            self.assertNotEqual(kwargs["app_source"].parent, self.fixture.installed_esp)

        def installed(plan):
            self.assertEqual(self.fixture.installed_stub_icon.read_bytes(), b"omarchy-icon")
            return b"complete evidence"

        before_images = dict(self.fixture.raw_images)
        self.adapter._installed_evidence = installed
        with patch("omarchy_asahi.asahi_firmware.core.FWPackage", Firmware), patch(
                "omarchy_asahi.omarchy_recovery.stage", side_effect=stage), patch(
                "omarchy_asahi.shutil.copy"):
            self.assertEqual(self.adapter._rebuild_interrupted_staging(self.plan), b"complete evidence")
        self.assertFalse(optional.exists())
        self.assertEqual(self.fixture.raw_images, before_images)
        self.assertEqual(self.installer.dutil.add_calls, [])
        self.assertEqual(self.installer.dutil.resize_calls, [])


class ProtectedStubWriteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "stub"
        self.root.mkdir(mode=0o775)
        self.root.chmod(0o775)
        self.outside = Path(self.temp.name) / "outside"
        self.outside.mkdir()
        self.critical = self.outside / "critical"
        self.critical.write_bytes(b"must remain unchanged")

    def test_stale_subtree_symlink_cannot_redirect_privileged_writer(self):
        (self.root / "usr").symlink_to(self.outside, target_is_directory=True)
        with self.assertRaisesRegex(ResumeError, "symbolic link"):
            with protected_stub_trees([(self.root, ["usr"], {})]):
                (self.root / "usr/critical").write_bytes(b"overwritten")
        self.assertEqual(self.critical.read_bytes(), b"must remain unchanged")
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o775)

    def test_hard_link_to_external_file_is_rejected(self):
        os.link(self.critical, self.root / "config")
        with self.assertRaises(ResumeError):
            with protected_stub_trees([(self.root, ["config"], {})]):
                (self.root / "config").write_bytes(b"overwritten")
        self.assertEqual(self.critical.read_bytes(), b"must remain unchanged")

    def test_volume_root_is_sealed_and_writable_descendants_are_refused(self):
        child = self.root / "usr"
        child.mkdir(mode=0o755)
        with protected_stub_trees([(self.root, ["usr"], {})]):
            self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
        child.chmod(0o775)
        with self.assertRaises(ResumeError):
            with protected_stub_trees([(self.root, ["usr"], {})]):
                self.fail("writable subtree admitted")

    def test_only_exact_restore_link_is_allowed(self):
        (self.root / "Restore").symlink_to(str(self.outside) + "/./Restore")
        with protected_stub_trees([(self.root, ["Restore"], {
                "Restore": str(self.outside / "Restore")})]):
            pass
        with self.assertRaises(ResumeError):
            with protected_stub_trees([(self.root, ["Restore"], {"Restore": "/other/Restore"})]):
                self.fail("foreign restore link admitted")

    def test_framework_links_are_exact_and_cannot_redirect_outside(self):
        framework = self.root / "group/Restore/Bootability/BootabilityBrain.framework"
        (framework / "Versions/A/Resources").mkdir(parents=True)
        (framework / "Versions/A/BootabilityBrain").write_bytes(b"framework")
        prefix = str(framework.relative_to(self.root))
        allowed = {prefix + "/" + name: target
                   for name, target in BOOTABILITY_FRAMEWORK_LINKS.items()}
        for name, target in BOOTABILITY_FRAMEWORK_LINKS.items():
            (framework / name).symlink_to(target)
        with protected_stub_trees([(self.root, ["group"], allowed)]):
            pass
        (framework / "Versions/Current").unlink()
        (framework / "Versions/Current").symlink_to(self.outside)
        with self.assertRaisesRegex(ResumeError, "symbolic link"):
            with protected_stub_trees([(self.root, ["group"], allowed)]):
                self.fail("external framework link admitted")

    @unittest.skipUnless(sys.platform == "darwin", "requires macOS ACLs")
    def test_acl_grants_are_rejected(self):
        import subprocess
        target = self.root / "config"
        target.write_bytes(b"untouched")
        subprocess.run(["/bin/chmod", "+a", "everyone allow read", str(target)], check=True)
        with self.assertRaisesRegex(ResumeError, "access controls"):
            with protected_stub_trees([(self.root, ["config"], {})]):
                self.fail("ACL-bearing file admitted")
