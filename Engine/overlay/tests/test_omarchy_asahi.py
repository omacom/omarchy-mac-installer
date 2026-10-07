# SPDX-License-Identifier: MIT
import io
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import re
import shutil
import time
import subprocess
import unittest
from unittest.mock import patch
import zipfile


class FakeOSInstaller:
    def __init__(self, dutil, data, template):
        self.min_recommended_size = template.get("minimum_size", 64 * GIB)
        self.min_size = template.get("floor_size", self.min_recommended_size)
        self.name = template.get("default_os_name", "Omarchy")
        self.needs_firmware = False
        self.idata_targets = []
        self.efi_part = SimpleNamespace(uuid="ABCDEF")
        self.calls = []
        self.template = template

    def partition_disk(self, name, size):
        self.calls.append(("partition_disk", name, size))
        self.part_info = [
            SimpleNamespace(name="disk0s5", uuid="EFI-UUID", size=500 * 1024**2),
            SimpleNamespace(name="disk0s6", uuid="BOOT-UUID", size=2 * GIB),
            SimpleNamespace(name="disk0s7", uuid="ROOT-UUID", size=32 * GIB),
        ]
        self.efi_part = self.part_info[0]

    def install(self, installer):
        self.calls.append(("install", installer))
        icon = self.template.get("icon")
        if icon:
            Path(installer.icon_path).write_bytes(self.pkg.read(icon))
        for partition, info in zip(self.template["partitions"], self.part_info):
            if partition.get("image"):
                self.install_raw_image(partition["image"], info)


class FakeStubInstaller:
    def __init__(self, sysinfo, dutil, osinfo):
        self.calls = []
        self.recovery = tempfile.TemporaryDirectory()
        self.osi = SimpleNamespace(
            vgid="11111111-2222-3333-4444-555555555555",
            sys_volume="System",
            recovery=self.recovery.name,
            preboot_vgid="66666666-7777-8888-9999-AAAAAAAAAAAA",
        )
        # Where the real stub writes the Recovery setup that Omarchy replaces.
        self.step2_sh = os.path.join(self.recovery.name, "step2.sh")
        self.icon_path = dutil.stub_icon_path

    def load_ipsw(self, ipsw):
        self.calls.append(("load_ipsw", ipsw))

    def prepare_volume(self, part):
        self.calls.append(("prepare_volume", part.name))

    def check_volume(self, part=None):
        if part is not None:
            self.calls.append(("check_volume", part.name))
            return
        self.calls.append(("check_volume",))

    def install_files(self, current_os):
        self.calls.append(("install_files", current_os))

    def prepare_for_bless(self):
        self.calls.append(("prepare_for_bless",))

    def prepare_for_step2(self):
        self.calls.append(("prepare_for_step2",))


sys.modules["asahi_firmware"] = SimpleNamespace(
    core=SimpleNamespace(FWPackage=object),
)
sys.modules["osinstall"] = SimpleNamespace(OSInstaller=FakeOSInstaller, psize=lambda value: int(value[:-2]) * 1024**3 if value.endswith("GB") else int(value[:-1]))
sys.modules["stub"] = SimpleNamespace(StubInstaller=FakeStubInstaller)
sys.modules.setdefault(
    "util",
    SimpleNamespace(align_down=lambda value, align: value - value % align),
)
sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[1] / "src"),
)

from omarchy_asahi import (  # noqa: E402
    STEP2_SCRIPT,
    AsahiAdapterError,
    AsahiInPlaceRepairAdapter,
    AsahiStage1Adapter,
    stub_installer,
)


GIB = 1024**3


class AsahiStage1AdapterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.metadata = self.root / "installer_data.json"
        self.payload = self.root / "payload.zip"
        self._write_metadata()
        with zipfile.ZipFile(self.payload, "w") as archive:
            archive.writestr("omarchy-volume.icns", b"omarchy-icon")
            archive.writestr("esp/m1n1/boot.bin", "m1n1")
            archive.writestr("esp/EFI/BOOT/BOOTAA64.EFI", "grub")
            archive.writestr("boot.img", b"b" * 4096)
            archive.writestr("root.img", b"r" * 4096)
        self.installed_esp = self.root / "installed-esp"
        self.installed_stub_icon = self.root / ".VolumeIcon.icns"
        (self.installed_esp / "m1n1").mkdir(parents=True)
        (self.installed_esp / "m1n1/boot.bin").write_bytes(b"m1n1")
        (self.installed_esp / "EFI/BOOT").mkdir(parents=True)
        (self.installed_esp / "EFI/BOOT/BOOTAA64.EFI").write_bytes(b"grub")
        self.raw_images = {
            "disk0s6": b"b" * 4096,
            "disk0s7": b"r" * 4096,
        }
        self.free = FakePart(
            "disk0s3",
            offset=400 * GIB,
            size=100 * GIB,
            free=True,
            part_type="Free space",
        )
        self.plan = SimpleNamespace(
            plan_digest="a" * 64,
            candidate_kind="free",
            source_identifier="disk0s3",
            offset_bytes=self.free.offset,
            length_bytes=80 * GIB,
            minimum_container_bytes=0,
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_preflight_accepts_the_partition_floor(self):
        metadata = self._target_metadata()
        metadata["floor_size"] = 40 * GIB
        metadata["minimum_size"] = 64 * GIB
        self._write_metadata([metadata])
        self.plan.length_bytes = 50 * GIB
        adapter = self._adapter(FakeInstaller(FakeDiskUtil([[self.free]])))

        adapter.preflight(self.plan)

        self.assertTrue(adapter.preflight_complete)

    def test_preflight_rejects_an_extent_below_the_partition_floor(self):
        metadata = self._target_metadata()
        metadata["floor_size"] = 40 * GIB
        self._write_metadata([metadata])
        self.plan.length_bytes = 30 * GIB
        adapter = self._adapter(FakeInstaller(FakeDiskUtil([[self.free]])))

        with self.assertRaisesRegex(
            AsahiAdapterError,
            "smaller than Asahi minimum",
        ):
            adapter.preflight(self.plan)

    def test_free_extent_runs_exact_upstream_stage_one_primitives(self):
        dutil = FakeDiskUtil([[self.free]])
        installer = FakeInstaller(dutil)
        adapter = self._adapter(installer)

        adapter.preflight(self.plan)
        target_evidence = json.loads(adapter.prepare_target(self.plan))
        installed_evidence = json.loads(
            adapter.install_stub_and_esp(self.plan)
        )
        with patch.dict(
            os.environ,
            {"OMARCHY_MACHINE_OWNER": "mina"},
        ), patch(
            "omarchy_asahi.sys.stdin",
            SimpleNamespace(buffer=io.BytesIO(b"owner-password\n")),
        ), patch(
            "omarchy_asahi.subprocess.run",
        ) as run:
            recovery_evidence = json.loads(
                adapter.prepare_recovery_handoff(self.plan)
            )

        self.assertEqual(installer.chosen_firmware, None)
        self.assertEqual(
            installer.ins.calls[0],
            ("load_ipsw", "ipsw-image"),
        )
        self.assertEqual(
            dutil.add_calls,
            [("disk0s3", "apfs", "Omarchy", 2 * GIB)],
        )
        self.assertEqual(
            target_evidence["partition_identifier"],
            "disk0s4",
        )
        self.assertEqual(installed_evidence["apfs_vgid"], "11111111-2222-3333-4444-555555555555")
        self.assertEqual(installed_evidence["efi_partition"], "efi-uuid")
        self.assertEqual(
            installed_evidence["startup_volume_icon"],
            {
                "content_sha256": hashlib.sha256(b"omarchy-icon").hexdigest(),
                "installed_bytes": 12,
                "member": "omarchy-volume.icns",
                "verification": "stub-file-sha256",
            },
        )
        self.assertEqual(
            [item["partition_identifier"] for item in installed_evidence["populated_partitions"]],
            ["disk0s5", "disk0s6", "disk0s7"],
        )
        self.assertEqual(
            [item["installed_bytes"] for item in installed_evidence["populated_partitions"]],
            [8, 4096, 4096],
        )
        self.assertEqual(
            [item["verification"] for item in installed_evidence["populated_partitions"]],
            ["copied-tree-sha256", "raw-prefix-sha256", "source-sha256-write-flushed-v1"],
        )
        self.assertEqual(
            installed_evidence["populated_partitions"][1]["content_sha256"],
            hashlib.sha256(b"b" * 4096).hexdigest(),
        )
        self.assertEqual(
            recovery_evidence["outcome"],
            "awaiting_recovery",
        )
        self.assertLess(
            installer.ins.calls.index(("prepare_for_bless",)),
            installer.ins.calls.index(("prepare_for_step2",)),
        )
        run.assert_called_once_with(
            [
                "/usr/sbin/bless",
                "--setBoot",
                "--device",
                "/dev/System",
                "--user",
                "mina",
                "--stdinpass",
            ],
            input=b"owner-password\n",
            check=True,
            stdout=-3,
            stderr=-3,
        )

    def test_recovery_handoff_rejects_missing_owner_before_bless(self):
        adapter = self._adapter(FakeInstaller(FakeDiskUtil([[self.free]])))
        adapter.preflight(self.plan)

        with patch.dict(os.environ, {}, clear=True), patch(
            "omarchy_asahi.subprocess.run",
        ) as run:
            with self.assertRaisesRegex(
                AsahiAdapterError,
                "machine owner is unavailable",
            ):
                adapter.prepare_recovery_handoff(self.plan)

        run.assert_not_called()

    def test_resize_uses_only_approved_container_and_minimum(self):
        source = FakePart(
            "disk0s2",
            offset=1 * GIB,
            size=500 * GIB,
            free=False,
        )
        resized_free = FakePart(
            "disk0s3",
            offset=421 * GIB,
            size=80 * GIB,
            free=True,
            part_type="Free space",
        )
        plan = SimpleNamespace(
            **{
                **self.plan.__dict__,
                "candidate_kind": "resize",
                "source_identifier": "disk0s2",
                "offset_bytes": resized_free.offset,
                "minimum_container_bytes": 320 * GIB,
            }
        )
        dutil = FakeDiskUtil([[source], [resized_free]])
        adapter = self._adapter(FakeInstaller(dutil))

        adapter.preflight(plan)
        adapter.prepare_target(plan)

        self.assertEqual(
            dutil.resize_calls,
            [("disk0s2", 420 * GIB)],
        )
        self.assertEqual(dutil.add_calls[0][0], "disk0s3")

    def test_changed_free_extent_is_rejected_before_partition_creation(self):
        changed = FakePart(
            "disk0s3",
            offset=self.free.offset + GIB,
            size=self.free.size,
            free=True,
            part_type="Free space",
        )
        dutil = FakeDiskUtil([[changed]])
        adapter = self._adapter(FakeInstaller(dutil))
        adapter.preflight(self.plan)

        with self.assertRaisesRegex(
            AsahiAdapterError,
            "approved free extent changed",
        ):
            adapter.prepare_target(self.plan)

        self.assertEqual(dutil.add_calls, [])

    def test_resize_geometry_drift_is_rejected_before_mutation(self):
        source = FakePart("disk0s2", offset=0, size=500 * GIB, free=False)
        plan = SimpleNamespace(**{
            **self.plan.__dict__, "candidate_kind": "resize",
            "source_identifier": source.name, "minimum_container_bytes": 320 * GIB,
        })
        dutil = FakeDiskUtil([[source]])
        adapter = self._adapter(FakeInstaller(dutil))
        adapter.preflight(plan)
        with self.assertRaisesRegex(AsahiAdapterError, "approved source partition changed"):
            adapter.prepare_target(plan)
        self.assertEqual(dutil.resize_calls, [])
        self.assertEqual(dutil.add_calls, [])

    def test_prepared_checkpoint_rejects_each_changed_identity_field(self):
        initial = self._adapter(FakeInstaller(FakeDiskUtil([[self.free]])))
        initial.preflight(self.plan)
        evidence = initial.prepare_target(self.plan)
        for field, value in (
            ("uuid", "DIFFERENT-UUID"), ("name", "disk0s9"),
            ("size", 2 * GIB + 4096), ("offset", self.plan.offset_bytes + 4096),
            ("type", "Linux Filesystem"),
        ):
            with self.subTest(field=field):
                part = FakePart("disk0s4", offset=self.plan.offset_bytes,
                                size=2 * GIB, free=False)
                setattr(part, field, value)
                installer = FakeInstaller(FakeDiskUtil([[part]]))
                adapter = self._adapter(installer)
                adapter.preflight(self.plan)
                with self.assertRaisesRegex(
                    AsahiAdapterError, "prepared resume target does not match checkpoint"
                ):
                    adapter.validate_prepared_checkpoint(self.plan, evidence)
                self.assertIsNone(adapter.target_part)
                self.assertFalse(any(call[0] == "prepare_volume" for call in installer.ins.calls))

    def test_retry_reconciles_the_exact_prepared_apfs_partition(self):
        prepared = FakePart(
            "disk0s4",
            offset=self.plan.offset_bytes,
            size=2 * GIB,
            free=False,
        )
        adapter = self._adapter(FakeInstaller(FakeDiskUtil([[prepared]])))
        adapter.preflight(self.plan)

        evidence = json.loads(adapter.install_stub_and_esp(self.plan))

        self.assertEqual(evidence["plan_digest"], self.plan.plan_digest)
        self.assertIn(("prepare_volume", "disk0s4"), adapter.installer.ins.calls)

    def test_installed_image_read_back_rejects_changed_partition_bytes(self):
        dutil = FakeDiskUtil([[self.free]])
        adapter = self._adapter(FakeInstaller(dutil))
        adapter.preflight(self.plan)
        adapter.prepare_target(self.plan)
        adapter.install_stub_and_esp(self.plan)
        self.raw_images["disk0s6"] = b"x" * 4096

        with self.assertRaisesRegex(
            AsahiAdapterError,
            "installed content does not match payload",
        ):
            adapter._installed_evidence(self.plan)

    def test_fast_root_is_not_reread_but_retry_detects_corruption(self):
        adapter = self._adapter(FakeInstaller(FakeDiskUtil([[self.free]])))
        adapter.preflight(self.plan)
        target = adapter.prepare_target(self.plan)
        read = adapter.raw_partition_opener
        def boot_only(name):
            self.assertNotEqual(name, "disk0s7")
            return read(name)
        adapter.raw_partition_opener = boot_only
        evidence = adapter.install_stub_and_esp(self.plan)
        self.raw_images["disk0s7"] = b"x" * 4096
        retry = self._retry_adapter(target, evidence)
        retry.preflight(self.plan)
        with self.assertRaisesRegex(AsahiAdapterError, "does not match payload"):
            retry.validate_installed_checkpoint(self.plan, target, evidence)

    def test_thorough_mode_reads_root(self):
        adapter = self._adapter(FakeInstaller(FakeDiskUtil([[self.free]])))
        adapter.full_readback = True
        adapter.preflight(self.plan)
        adapter.prepare_target(self.plan)
        evidence = json.loads(adapter.install_stub_and_esp(self.plan))
        self.assertEqual(evidence["populated_partitions"][-1]["verification"],
                         "raw-prefix-sha256")
        self.raw_images["disk0s7"] = b"x" * 4096
        with self.assertRaisesRegex(AsahiAdapterError, "does not match payload"):
            adapter._installed_evidence(self.plan)

    def test_oversized_image_fails_before_disk_mutation(self):
        metadata = json.loads(self.metadata.read_text())
        metadata["os_list"][0]["partitions"][1]["size"] = "1024B"
        self.metadata.chmod(0o600)
        self.metadata.write_text(json.dumps(metadata))
        self.metadata.chmod(0o400)
        installer = FakeInstaller(FakeDiskUtil([[self.free]]))
        adapter = self._adapter(installer)
        with self.assertRaisesRegex(AsahiAdapterError, "exceeds partition capacity"):
            adapter.preflight(self.plan)
        self.assertEqual(installer.dutil.resize_calls, [])
        self.assertFalse(adapter.preflight_complete)

    def test_preflight_does_not_expand_the_package(self):
        adapter = self._adapter(FakeInstaller(FakeDiskUtil([[self.free]])))
        with patch.object(zipfile.ZipFile, "testzip", side_effect=AssertionError("expanded")):
            adapter.preflight(self.plan)

    def test_recovery_retry_revalidates_exact_installed_checkpoint(self):
        initial = self._adapter(FakeInstaller(FakeDiskUtil([[self.free]])))
        initial.preflight(self.plan)
        target_evidence = initial.prepare_target(self.plan)
        installed_evidence = initial.install_stub_and_esp(self.plan)
        retry = self._retry_adapter(target_evidence, installed_evidence)

        retry.preflight(self.plan)
        retry.validate_installed_checkpoint(
            self.plan,
            target_evidence,
            installed_evidence,
        )

        self.assertEqual(retry.installer.dutil.resize_calls, [])
        self.assertEqual(retry.installer.dutil.add_calls, [])
        self.assertIn(
            ("check_volume", "disk0s4"),
            retry.installer.ins.calls,
        )

    def test_recovery_retry_rejects_changed_partition_identity(self):
        initial = self._adapter(FakeInstaller(FakeDiskUtil([[self.free]])))
        initial.preflight(self.plan)
        target_evidence = initial.prepare_target(self.plan)
        installed_evidence = initial.install_stub_and_esp(self.plan)
        retry = self._retry_adapter(
            target_evidence,
            installed_evidence,
            changed_partition="disk0s7",
        )
        retry.preflight(self.plan)

        with self.assertRaisesRegex(
            AsahiAdapterError,
            "installed partition identity changed",
        ):
            retry.validate_installed_checkpoint(
                self.plan,
                target_evidence,
                installed_evidence,
            )

        self.assertEqual(retry.installer.dutil.resize_calls, [])
        self.assertEqual(retry.installer.dutil.add_calls, [])

    def test_duplicate_target_metadata_is_rejected(self):
        target = self._target_metadata()
        self._write_metadata([target, dict(target)])
        adapter = self._adapter(FakeInstaller(FakeDiskUtil([[self.free]])))

        with self.assertRaisesRegex(
            AsahiAdapterError,
            "exactly one Omarchy full-OS target",
        ):
            adapter.preflight(self.plan)

    def test_validation_only_uefi_target_is_not_an_install_target(self):
        target = self._target_metadata()
        target["omarchy_target"] = "apple-silicon-uefi"
        self._write_metadata([target])
        adapter = self._adapter(FakeInstaller(FakeDiskUtil([[self.free]])))

        with self.assertRaisesRegex(
            AsahiAdapterError,
            "exactly one Omarchy full-OS target",
        ):
            adapter.preflight(self.plan)

    def test_missing_startup_volume_icon_is_rejected_before_mutation(self):
        target = self._target_metadata()
        target.pop("icon")
        self._write_metadata([target])
        adapter = self._adapter(FakeInstaller(FakeDiskUtil([[self.free]])))

        with self.assertRaisesRegex(
            AsahiAdapterError,
            "invalid Omarchy Startup Options icon metadata",
        ):
            adapter.preflight(self.plan)

    def test_metadata_symlink_and_unprepared_adapter_are_rejected(self):
        link = self.root / "metadata-link.json"
        link.symlink_to(self.metadata)
        adapter = AsahiStage1Adapter(
            installer=FakeInstaller(FakeDiskUtil([[self.free]])),
            metadata_path=str(link),
            payload_path=str(self.payload),
            stub_size=2 * GIB,
        )
        with self.assertRaisesRegex(
            AsahiAdapterError,
            "metadata is unavailable",
        ):
            adapter.preflight(self.plan)

        fresh = self._adapter(FakeInstaller(FakeDiskUtil([[self.free]])))
        with self.assertRaisesRegex(
            AsahiAdapterError,
            "preflight is required",
        ):
            fresh.prepare_target(self.plan)

    def _adapter(self, installer):
        installer.dutil.stub_icon_path = str(self.installed_stub_icon)
        installer.dutil.mount_points = {
            "disk0s5": str(self.installed_esp),
        }
        @contextmanager
        def writer(name):
            stream = io.BytesIO()
            yield stream
            self.raw_images[name] = stream.getvalue()
            stream.close()

        return AsahiStage1Adapter(
            installer=installer,
            metadata_path=str(self.metadata),
            payload_path=str(self.payload),
            stub_size=2 * GIB,
            raw_partition_writer=writer,
            image_flush=lambda stream: None,
            raw_partition_opener=lambda name: io.BytesIO(
                self.raw_images[name]
            ),
        )

    def _retry_adapter(
        self,
        target_evidence,
        installed_evidence,
        changed_partition=None,
    ):
        target = json.loads(target_evidence)
        installed = json.loads(installed_evidence)
        parts = [
            FakePart(
                target["partition_identifier"],
                offset=target["offset_bytes"],
                size=target["size_bytes"],
                free=False,
                uuid=target["uuid"],
            )
        ]
        next_offset = target["offset_bytes"] + target["size_bytes"]
        for item in installed["populated_partitions"]:
            identity = item["partition_uuid"]
            if item["partition_identifier"] == changed_partition:
                identity = "changed-uuid"
            parts.append(
                FakePart(
                    item["partition_identifier"],
                    offset=next_offset,
                    size=item["partition_size_bytes"],
                    free=False,
                    uuid=identity,
                )
            )
            next_offset += item["partition_size_bytes"]
        return self._adapter(FakeInstaller(FakeDiskUtil([parts])))

    def _target_metadata(self):
        return {
            "omarchy_target": "apple-silicon-full-os",
            "minimum_size": 64 * GIB,
            "name": "Omarchy MX Mac",
            "default_os_name": "Omarchy",
            "boot_object": "m1n1.bin",
            "next_object": "m1n1/boot.bin",
            "package": self.payload.name,
            "icon": "omarchy-volume.icns",
            "supported_fw": None,
            "partitions": [
                {
                    "name": "EFI",
                    "type": "EFI",
                    "size": "500MB",
                    "format": "fat",
                    "copy_firmware": True,
                    "copy_installer_data": True,
                    "source": "esp",
                },
                {
                    "name": "Boot",
                    "type": "Linux",
                    "size": "2GB",
                    "image": "boot.img",
                },
                {
                    "name": "Root",
                    "type": "Linux",
                    "size": "32GB",
                    "expand": True,
                    "image": "root.img",
                },
            ],
        }

    def _write_metadata(self, targets=None):
        if targets is None:
            targets = [self._target_metadata()]
        if self.metadata.exists():
            self.metadata.chmod(0o600)
        self.metadata.write_text(
            json.dumps({"os_list": targets}),
            encoding="utf-8",
        )
        self.metadata.chmod(0o400)


class AsahiInPlaceRepairAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.payload = self.root / "payload.zip"
        self.content = {
            "disk0s3": bytearray(b"stub-old"),
            "disk0s4": bytearray(b"efi-old!"),
            "disk0s5": bytearray(b"boot-old"),
            "disk0s6": bytearray(b"root-old"),
        }
        replacements = {
            "boot": b"boot-new",
            "root": b"root-new",
        }
        with zipfile.ZipFile(self.payload, "w") as archive:
            for role, content in replacements.items():
                archive.writestr(f"repair/{role}.img", content)
        self.manifest = {
            "partitions": [
                {"role": "stub", "identifier": "disk0s3"},
                {"role": "efi", "identifier": "disk0s4"},
                {"role": "boot", "identifier": "disk0s5"},
                {"role": "root", "identifier": "disk0s6"},
            ],
            "existing_content": {
                role: self._identity(self.content[identifier])
                for role, identifier in (
                    ("stub", "disk0s3"),
                    ("efi", "disk0s4"),
                    ("boot", "disk0s5"),
                    ("root", "disk0s6"),
                )
            },
            "replacement_content": {
                "stub": {
                    **self._identity(self.content["disk0s3"]),
                    "payload_member": None,
                },
                "efi": {
                    **self._identity(self.content["disk0s4"]),
                    "payload_member": None,
                },
                "boot": {
                    **self._identity(replacements["boot"]),
                    "payload_member": "repair/boot.img",
                },
                "root": {
                    **self._identity(replacements["root"]),
                    "payload_member": "repair/root.img",
                },
            },
        }
        self.disk_utility = FakeDiskUtil([[]])
        self.installer = SimpleNamespace(dutil=self.disk_utility)
        self.opened_partitions = []
        self.adapter = AsahiInPlaceRepairAdapter(
            installer=self.installer,
            manifest=self.manifest,
            metadata_path=self.root / "metadata.json",
            payload_path=self.payload,
            raw_partition_opener=self._open_partition,
            boot_policy_authorizer=lambda *_: b"authorized",
        )
        self.plan = SimpleNamespace(plan_digest="a" * 64)

    def tearDown(self):
        self.temporary.cleanup()

    def test_repair_rewrites_only_declared_content_without_partitioning(self):
        existing = json.loads(
            self.adapter.validate_existing_install(self.plan)
        )
        written = json.loads(self.adapter.rewrite_existing_content(self.plan))
        repaired = json.loads(
            self.adapter.validate_repaired_content(self.plan)
        )

        self.assertEqual(set(existing["content"]), {"boot", "root"})
        self.assertEqual(existing["preserved_roles"], ["stub", "efi"])
        self.assertEqual(written["rewritten_roles"], ["boot", "root"])
        self.assertEqual(set(repaired["content"]), {"boot", "root"})
        self.assertEqual(repaired["preserved_roles"], ["stub", "efi"])
        self.assertEqual(self.content["disk0s3"], b"stub-old")
        self.assertEqual(self.content["disk0s4"], b"efi-old!")
        self.assertEqual(self.content["disk0s5"], b"boot-new")
        self.assertEqual(self.content["disk0s6"], b"root-new")
        self.assertEqual(self.disk_utility.resize_calls, [])
        self.assertEqual(self.disk_utility.add_calls, [])

    def test_readback_is_exhaustive_for_declared_writes_and_skips_preserved_content(self):
        self.adapter.validate_existing_install(self.plan)
        self.adapter.rewrite_existing_content(self.plan)
        self.content["disk0s4"][0] ^= 1

        self.assertEqual(
            json.loads(self.adapter.validate_repaired_content(self.plan))["preserved_roles"],
            ["stub", "efi"],
        )
        self.content["disk0s5"][0] ^= 1

        with self.assertRaisesRegex(
            AsahiAdapterError,
            "repair content changed",
        ):
            self.adapter.validate_repaired_content(self.plan)

    def test_invalid_later_repair_member_does_not_write_earlier_partition(self):
        for field, value in (("payload_member", "repair/missing.img"), ("size_bytes", 4096)):
            with self.subTest(field=field):
                expected = self.manifest["replacement_content"]["root"]
                original = expected[field]
                expected[field] = value
                try:
                    with self.assertRaises(AsahiAdapterError):
                        self.adapter.rewrite_existing_content(self.plan)
                    self.assertEqual(self.content["disk0s5"], b"boot-old")
                    self.assertEqual(self.opened_partitions, [])
                finally:
                    expected[field] = original

    @staticmethod
    def _identity(content):
        return {
            "size_bytes": len(content),
            "sha256": "sha256:" + hashlib.sha256(content).hexdigest(),
        }

    def _open_partition(self, identifier, mode):
        self.opened_partitions.append((identifier, mode))
        return PartitionAccess(self.content[identifier], "w" in mode or "+" in mode)


class FakePart:
    def __init__(
        self,
        name,
        *,
        offset,
        size,
        free,
        part_type="Apple_APFS",
        uuid="PART-UUID",
    ):
        self.name = name
        self.offset = offset
        self.size = size
        self.free = free
        self.type = part_type
        self.uuid = uuid


class FakeDiskUtil:
    def __init__(self, partition_rounds):
        self.partition_rounds = list(partition_rounds)
        self.round = 0
        self.resize_calls = []
        self.add_calls = []
        self.mount_points = {}

    def get_info(self):
        return None

    def get_partitions(self, disk):
        index = min(self.round, len(self.partition_rounds) - 1)
        self.round += 1
        return self.partition_rounds[index]

    def resizeContainer(self, name, new_size):
        self.resize_calls.append((name, new_size))

    def addPartition(self, name, part_type, label, size):
        self.add_calls.append((name, part_type, label, size))
        source = self.partition_rounds[-1][0]
        return FakePart(
            "disk0s4",
            offset=source.offset,
            size=size,
            free=False,
        )

    def mount(self, name):
        return self.mount_points[name]


class PartitionAccess:
    def __init__(self, content, writable):
        self.content = content
        self.writable = writable
        self.stream = io.BytesIO(bytes(content))

    def __enter__(self):
        return self.stream

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is None and self.writable:
            self.content[:] = self.stream.getvalue()
        self.stream.close()


class FakeInstaller:
    def __init__(self, dutil):
        self.dutil = dutil
        self.sys_disk = "disk0"
        self.sysinfo = object()
        self.osinfo = object()
        self.cur_os = "current-os"
        self.chosen_firmware = "unset"
        self.check_cur_os_calls = 0

    def choose_ipsw(self, supported_firmware):
        self.chosen_firmware = supported_firmware
        return "ipsw-image"

    def check_cur_os(self):
        self.check_cur_os_calls += 1


# A kmutil that prints each prompt before reading its answer, and echoes what
# a terminal would show: the confirmation and the user name, but never the
# password, which a real terminal reads with echo off.
PROMPTING_KMUTIL = """printf 'Are you sure you want to do this? (enter y or n) '; IFS= read -r a; echo "$a"
echo 'updating local machine policy...'; printf 'Username: '; IFS= read -r u; echo "$u"
printf 'Password: '; IFS= read -r p; echo
printf '%s|%s|%s' "$a" "$u" "$p" >"$PIPED"
[ "$a|$u|$p" = "y|scott|$EXPECTED_PASSWORD" ]"""

# Files step2.sh may leave in /tmp while it runs; none may outlive it.
STEP2_LOGS = ("bp.txt", "bless.log", "bputil.log", "bputil.status", "kmutil.log",
              "kmutil.status", "kmutil.pid", "kmutil.done")


class Step2ScriptTests(unittest.TestCase):
    """The Recovery setup: the installer's own name and one password prompt."""

    title = "Probe Installer"
    # How macOS and its recoveryOS run step2.sh's #!/bin/sh: bash in POSIX
    # mode. (Linux's /bin/sh is often dash, which the class below covers.)
    shell = [shutil.which("bash") or "/bin/bash", "--posix"]
    # The fake recoveryOS tools' interpreter.
    fake_shell = "/bin/sh"

    def make(self, owner):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        step2 = Path(root.name) / "step2.sh"
        step2.write_text("asahi step2")

        class Stub:
            def __init__(self, *args):
                self.osi = SimpleNamespace(vgid="0B1C2D3E-4F50-6172-8394-A5B6C7D8E9F0", preboot_vgid="1A2B3C4D-5E6F-7081-92A3-B4C5D6E7F809", recovery=root.name)
                self.step2_sh = str(step2)

            def load_identity(self):
                pass

            def collect_firmware(self, pkg):
                pass

            def install_files(self, cur_os):
                pass

        with patch("omarchy_asahi.stub.StubInstaller", Stub), patch.dict(
            os.environ, {"OMARCHY_MACHINE_OWNER": owner, "OMARCHY_INSTALLER_NAME": self.title}
        ):
            installer = stub_installer("sysinfo", "dutil", "osinfo")
            installer.install_files("cur-os")
        return step2

    def test_the_setup_is_branded_and_asks_for_the_password_once(self):
        step2 = self.make("scott")
        text = step2.read_text()
        self.assertTrue(os.access(step2, os.X_OK))
        self.assertIn("${BOLD}Probe Installer${RST}", text)
        self.assertNotIn("MX Mac", text)
        self.assertEqual(re.findall(r"##[A-Z]+##", text), [])
        self.assertIn('VGID="0B1C2D3E-4F50-6172-8394-A5B6C7D8E9F0"', text)
        self.assertIn('PREBOOT="1A2B3C4D-5E6F-7081-92A3-B4C5D6E7F809"', text)
        self.assertIn('OWNER="scott"', text)
        # One prompt, kept whole: leading and trailing spaces are part of it.
        self.assertEqual(text.count("read -r PASSWORD"), 1)
        self.assertIn("IFS= read -r PASSWORD", text)
        self.assertIn('bputil -nc -v "$VGID" -u "$OWNER" -p "$PASSWORD"', text)
        # kmutil runs on a hidden terminal, recording its own process ID.
        self.assertIn('script -q -t 0 "$kmutil_log"', text)
        self.assertIn("echo $$ >/tmp/kmutil.pid; exec kmutil configure-boot -c boot.bin --raw --entry-point 2048", text)
        self.assertIn('run_with_spinner "$kmutil_status" 60 /tmp/kmutil.pid hidden_kmutil', text)
        self.assertEqual(text.count("Are you sure you want to do this? (y or n)"), 1)
        # The recoveryOS checks stay.
        self.assertIn("': Paired'", text)
        self.assertIn("'one true recoveryOS'", text)
        result = subprocess.run([*self.shell, "-n", str(step2)])
        self.assertEqual(result.returncode, 0)

    def step2_with_fakes(self, kmutil_body, paired=True, bputil_mode="ok", password="secret",
                         startup="macos"):
        """step2.sh in place, with fake recoveryOS tools first on PATH."""
        step2 = self.make("scott")
        root = Path(step2).parent
        resources = root / "Omarchy" / "Finish Installation.app" / "Contents" / "Resources"
        resources.mkdir(parents=True)
        script = resources / "step2.sh"
        script.write_text(step2.read_text())
        script.chmod(0o755)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        sleep = shutil.which("sleep")
        fakes = {
            "bputil": "\n".join((
                f"[ \"$1\" = -d ] && echo 'OS Pairing Status: {'Paired' if paired else 'Not Paired'}'"
                " && echo 'OS Type: one true recoveryOS' && exit 0",
                'echo "$*" >>"$CALLS.bputil"',
                # Without -u, bputil asks the owner itself: the fallback.
                '[ "$4" = -u ] || exit 0',
                "echo 'It should only be used to understand how the security works.'",
                "echo 'Use at your own risk!'",
                '[ "$7" = "$EXPECTED_PASSWORD" ] || exit 1',
                f'[ "$BPUTIL_MODE" = hang ] && echo $$ >"$CALLS.bputil-pid" && exec {sleep} 30',
                '[ "$BPUTIL_MODE" = fail ] && exit 1',
                "exit 0",
            )),
            "stty": 'echo "$*" >>"$CALLS.stty"',
            "mount": "exit 0",
            "reboot": "exit 0",
            "shutdown": "exit 0",
            # script -q -t 0 log command...: the command's output goes to the
            # log, which is kept for the test because step2.sh deletes it. Like
            # the real script, which gives kmutil its own session on a hidden
            # terminal, Ctrl-C ends script but never reaches kmutil: here
            # kmutil runs in the background, where SIGINT is ignored. Its
            # answers come through descriptor 3, since dash replaces a
            # background job's stdin with /dev/null even after <&0.
            "script": "\n".join((
                'log=$4; shift 4',
                "trap 'exit 130' INT",
                'exec 3<&0',
                '"$@" <&3 3<&- >"$log" 2>&1 &',
                'wait $!; s=$?',
                'cp "$log" "$CALLS.kmutil-log"; exit $s',
            )),
            # Every sleep lasts 50 ms, so the one-minute deadline is twelve seconds.
            "sleep": f"exec {sleep} 0.05",
            # Like macOS's bless, it exits 0 whatever the password, and only
            # a right one changes the startup disk.
            "bless": "\n".join((
                '[ "$1" = --getBoot ] && { cat "$CALLS.boot"; exit 0; }',
                'echo "$*" >>"$CALLS.bless"',
                'case "$*" in',
                '*--stdinpass) IFS= read -r pw; echo "$pw" >>"$CALLS.bless"',
                '    [ "$pw" = "$EXPECTED_PASSWORD" ] && echo /dev/omarchy >"$CALLS.boot" ;;',
                # Without --stdinpass, bless asks the owner itself.
                '*) echo /dev/omarchy >"$CALLS.boot" ;;',
                'esac',
                "exit 0",
            )),
            "diskutil": "\n".join((
                '[ "$1" = info ] || exit 0',
                'case "$2" in',
                "/dev/omarchy|*/Omarchy) printf '   Device Node:  /dev/omarchy\\n   APFS Volume Group:  0B1C2D3E-4F50-6172-8394-A5B6C7D8E9F0\\n' ;;",
                "*) printf '   Device Node:  /dev/disk0s2\\n   APFS Volume Group:  MACOS-VG\\n' ;;",
                'esac',
            )),
            "kmutil": 'n=$(($(cat "$CALLS" 2>/dev/null || echo 0) + 1)); echo "$n" >"$CALLS"\n' + kmutil_body,
        }
        for name, body in fakes.items():
            (bin_dir / name).write_text(f"#!{self.fake_shell}\n" + body + "\n")
            (bin_dir / name).chmod(0o755)
        calls = root / "kmutil-calls"
        Path(f"{calls}.boot").write_text("/dev/omarchy\n" if startup == "omarchy" else "/dev/disk0s2\n")
        env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", CALLS=str(calls),
                   PIPED=str(root / "piped"), EXPECTED_PASSWORD=password, BPUTIL_MODE=bputil_mode)
        return script, env, calls

    def run_step2(self, kmutil_body, stdin="y\nsecret\n\n", **options):
        script, env, calls = self.step2_with_fakes(kmutil_body, **options)
        result = subprocess.run([*self.shell, str(script)], input=stdin, env=env,
                                capture_output=True, text=True, timeout=60)
        kmutil_calls = int(calls.read_text()) if calls.exists() else 0
        return result, kmutil_calls, calls

    def assert_nothing_left_in_tmp(self):
        for name in STEP2_LOGS:
            self.assertFalse(os.path.exists(f"/tmp/{name}"), name)

    def test_kmutil_is_answered_out_of_sight(self):
        result, kmutil_calls, calls = self.run_step2(PROMPTING_KMUTIL)
        out = result.stdout
        self.assertEqual(result.returncode, 0, out + result.stderr)
        self.assertEqual(kmutil_calls, 1)
        self.assertEqual(Path(calls.parent, "piped").read_text(), "y|scott|secret")
        self.assertNotIn("Username", out)
        self.assertNotIn("type y", out)
        self.assertEqual(out.count("Password for scott:"), 1)
        # Nothing changes before the owner agrees.
        self.assertLess(out.index("Are you sure you want to do this? (y or n)"), out.index("Password for scott:"))
        # The terminal shows what is typed, so the password goes only after
        # kmutil's own Password: prompt has turned echo off.
        log = Path(f"{calls}.kmutil-log").read_text()
        self.assertIn("scott", log)
        self.assertNotIn("secret", log)
        self.assert_nothing_left_in_tmp()

    def test_a_password_with_spaces_reaches_every_tool_intact(self):
        password = "  two words  "
        result, kmutil_calls, calls = self.run_step2(
            PROMPTING_KMUTIL, stdin=f"y\n{password}\n\n", password=password)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(kmutil_calls, 1)
        self.assertEqual(Path(calls.parent, "piped").read_text(), f"y|scott|{password}")

    def test_the_owner_can_decline_before_anything_changes(self):
        result, kmutil_calls, calls = self.run_step2("exit 0", stdin="n\n")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Nothing was changed", result.stdout)
        self.assertNotIn("Password for", result.stdout)
        self.assertFalse(Path(f"{calls}.bputil").exists())
        self.assertEqual(kmutil_calls, 0)
        self.assert_nothing_left_in_tmp()

    def test_a_wrong_password_is_named_without_bputils_disclaimer(self):
        result, kmutil_calls, _ = self.run_step2(PROMPTING_KMUTIL, stdin="y\nwrong\nsecret\n\n")
        out = result.stdout
        self.assertEqual(result.returncode, 0, out + result.stderr)
        self.assertIn("That password didn't work for scott. Try again.", out)
        self.assertNotIn("Use at your own risk", out)
        # Each attempt says what it is doing while bputil works, and a
        # rejected one replaces that line with the reason.
        self.assertEqual(out.count("Updating Omarchy's security settings..."), 2)
        # (Text-mode capture turns the carriage return into a newline.)
        self.assertIn("\x1b[KThat password didn't work for scott.", out)
        self.assertEqual(kmutil_calls, 1)

    def test_bputil_lets_macos_ask_after_three_failures(self):
        result, _, calls = self.run_step2("exit 0", stdin="y\nw1\nw2\nw3\n\n", bputil_mode="fail")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("macOS asks itself now", result.stdout)
        attempts = Path(f"{calls}.bputil").read_text().splitlines()
        self.assertEqual(len(attempts), 4)
        self.assertEqual(attempts[-1], "-nc -v 0B1C2D3E-4F50-6172-8394-A5B6C7D8E9F0")

    def test_a_kmutil_that_fails_is_handed_to_the_owner(self):
        started = time.monotonic()
        result, kmutil_calls, _ = self.run_step2('[ "$n" -gt 1 ] && exit 0\necho "Username: Password:"; exit 1')
        out = result.stdout
        self.assertEqual(result.returncode, 0, out + result.stderr)
        self.assertEqual(kmutil_calls, 2)
        self.assertIn("Type y, then your user name and password", out)
        self.assertNotIn("Username:", out)
        # A failure is handed over at once, not after the one-minute deadline.
        self.assertLess(time.monotonic() - started, 8)

    def test_a_kmutil_that_stalls_is_stopped_before_the_owner_answers(self):
        # It keeps its own command line, as kmutil does, so it can be recognized.
        result, kmutil_calls, calls = self.run_step2(
            '[ "$n" -gt 1 ] && exit 0\necho $$ >"$CALLS.stalled"\nwhile :; do sleep 1; done')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(kmutil_calls, 2)
        self.assertIn("Type y, then your user name and password", result.stdout)
        # The stalled kmutil itself, not only script, is gone before the
        # second one starts.
        with self.assertRaises(ProcessLookupError):
            os.kill(int(Path(f"{calls}.stalled").read_text()), 0)

    def test_ctrl_c_stops_everything_it_started(self):
        import signal
        script, env, calls = self.step2_with_fakes("exit 0", bputil_mode="hang")
        process = subprocess.Popen([*self.shell, str(script)], env=env, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                   start_new_session=True)
        process.stdin.write("y\nsecret\n")
        process.stdin.flush()
        hanging = Path(f"{calls}.bputil-pid")
        deadline = time.monotonic() + 20
        while not hanging.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(hanging.exists(), "bputil never started")
        # Ctrl-C reaches the terminal's whole foreground process group.
        os.killpg(process.pid, signal.SIGINT)
        out, _ = process.communicate(timeout=20)
        self.assertEqual(process.returncode, 130)
        self.assertIn("Stopped.", out)
        time.sleep(0.5)
        with self.assertRaises(ProcessLookupError):
            os.killpg(process.pid, 0)
        self.assert_nothing_left_in_tmp()

    def test_a_rejected_password_never_reaches_kmutil(self):
        # After three rejected passwords bputil asks macOS itself; the third
        # rejected one must not then be typed into kmutil.
        started = time.monotonic()
        result, kmutil_calls, calls = self.run_step2("exit 0", stdin="y\nw1\nw2\nw3\n\n", bputil_mode="fail")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(kmutil_calls, 1)
        self.assertFalse(Path(f"{calls}.kmutil-log").exists(), "the hidden kmutil ran")
        self.assertIn("Type y, then your user name and password", result.stdout)
        self.assertLess(time.monotonic() - started, 8)

    def interrupt(self, kmutil_body, started_file, stdin, **options):
        """Run step2.sh, wait for `started_file`, then press Ctrl-C."""
        import signal
        script, env, calls = self.step2_with_fakes(kmutil_body, **options)
        process = subprocess.Popen([*self.shell, str(script)], env=env, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                   start_new_session=True)
        process.stdin.write(stdin)
        process.stdin.flush()
        started = Path(f"{calls}.{started_file}")
        deadline = time.monotonic() + 20
        while not started.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(started.exists(), f"{started_file} never appeared")
        os.killpg(process.pid, signal.SIGINT)
        out, _ = process.communicate(timeout=20)
        self.assertEqual(process.returncode, 130)
        self.assertIn("Stopped.", out)
        return calls

    def test_ctrl_c_at_the_password_prompt_restores_echo(self):
        # The fake stty's log appears when echo is turned off for the prompt.
        calls = self.interrupt("exit 0", "stty", stdin="y\n")
        self.assertEqual(Path(f"{calls}.stty").read_text().splitlines(), ["-echo", "echo"])
        self.assertFalse(Path(f"{calls}.bputil").exists())
        self.assert_nothing_left_in_tmp()

    def test_ctrl_c_during_the_hidden_kmutil_stops_it(self):
        # A kmutil that ignores Ctrl-C, as one on its own hidden terminal would.
        calls = self.interrupt(
            "trap '' INT\necho $$ >\"$CALLS.kmutil-pid\"\nwhile :; do sleep 1; done",
            "kmutil-pid", stdin="y\nsecret\n")
        with self.assertRaises(ProcessLookupError):
            os.kill(int(Path(f"{calls}.kmutil-pid").read_text()), 0)
        self.assert_nothing_left_in_tmp()

    def test_a_volume_group_that_is_not_a_uuid_is_refused(self):
        for vgid in ("", "VG-1", '0B1C2D3E-4F50-6172-8394-A5B6C7D8E9F0"; reboot; "'):
            root = tempfile.TemporaryDirectory()
            self.addCleanup(root.cleanup)
            step2 = Path(root.name) / "step2.sh"

            class Stub:
                def __init__(self, *args, vgid=vgid):
                    self.osi = SimpleNamespace(vgid=vgid, preboot_vgid="1A2B3C4D-5E6F-7081-92A3-B4C5D6E7F809",
                                               recovery=root.name)
                    self.step2_sh = str(step2)

                def install_files(self, cur_os):
                    pass

            with self.subTest(vgid=vgid), patch("omarchy_asahi.stub.StubInstaller", Stub), patch.dict(
                os.environ, {"OMARCHY_MACHINE_OWNER": "scott", "OMARCHY_INSTALLER_NAME": self.title}
            ), self.assertRaisesRegex(AsahiAdapterError, "volume group is invalid"):
                stub_installer("sysinfo", "dutil", "osinfo").install_files("cur-os")

    def test_an_unpaired_recovery_blesses_with_the_known_owner(self):
        result, kmutil_calls, calls = self.run_step2(PROMPTING_KMUTIL, stdin="secret\n\n", paired=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Password for scott:", result.stdout)
        root = calls.parent
        self.assertEqual(
            Path(f"{calls}.bless").read_text().splitlines(),
            [f"--setBoot --mount {root / 'Omarchy'} --user scott --stdinpass", "secret"],
        )
        self.assertEqual(Path(f"{calls}.boot").read_text(), "/dev/omarchy\n")
        self.assertIn("choose Omarchy, and log in.", result.stdout)
        self.assertEqual(kmutil_calls, 0)

    def test_an_unpaired_recovery_checks_the_startup_disk_not_bless(self):
        # bless exits 0 after a wrong password, so only the startup disk tells.
        result, _, calls = self.run_step2("exit 0", stdin="wrong\nsecret\n\n", paired=False)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout.count("That password didn't work for scott. Try again."), 1)
        self.assertEqual(Path(f"{calls}.boot").read_text(), "/dev/omarchy\n")

    def test_an_unpaired_recovery_lets_bless_ask_after_three_failures(self):
        result, _, calls = self.run_step2("exit 0", stdin="a\nb\nc\n\n", paired=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("macOS asks itself now", result.stdout)
        self.assertEqual(Path(f"{calls}.bless").read_text().splitlines()[-1],
                         f"--setBoot --mount {calls.parent / 'Omarchy'}")
        self.assertEqual(Path(f"{calls}.boot").read_text(), "/dev/omarchy\n")

    def test_an_unpaired_recovery_already_starting_omarchy_asks_nothing(self):
        result, _, calls = self.run_step2("exit 0", stdin="\n", paired=False, startup="omarchy")
        self.assertEqual(result.returncode, 1)
        self.assertIn("is already the", result.stdout)
        self.assertNotIn("Password for", result.stdout)
        self.assertFalse(Path(f"{calls}.bless").exists())

    def test_every_line_fits_an_80_column_terminal(self):
        text = self.make("scott").read_text()
        for line in text.splitlines():
            match = re.match(r'\s*(?:echo|printf)\s+"(.*)"', line)
            if not match:
                continue
            shown = re.sub(r"\$\{(BOLD|RST)\}", "", match.group(1))
            shown = shown.replace("$os_name", "Omarchy").replace("\\n", "")
            self.assertLessEqual(len(shown), 76, shown)

    def test_an_unknown_owner_is_asked_for(self):
        text = self.make("").read_text()
        self.assertIn('OWNER=""', text)
        self.assertIn('if [ -z "$OWNER" ]', text)

    def test_a_title_that_could_break_the_script_is_refused(self):
        self.title = 'Probe"; reboot; "'
        with self.assertRaises(AsahiAdapterError):
            self.make("scott")

    def test_an_owner_that_could_break_the_script_is_refused(self):
        with self.assertRaises(AsahiAdapterError):
            self.make('scott"; rm -rf /; "')

    def test_the_template_has_only_its_four_placeholders(self):
        import re as _re
        self.assertEqual(
            set(_re.findall(r"##[A-Z]+##", STEP2_SCRIPT)),
            {"##TITLE##", "##VGID##", "##PREBOOT##", "##OWNER##"},
        )



@unittest.skipUnless(shutil.which("dash"), "dash is not installed")
class Step2ScriptDashTests(Step2ScriptTests):
    """The same under dash: step2.sh is #!/bin/sh, so it must stay POSIX."""

    shell = [shutil.which("dash") or "dash"]
    # Linux's /bin/sh is often dash, so the fakes run under it too.
    fake_shell = shutil.which("dash") or "dash"

if __name__ == "__main__":
    unittest.main()
