#!/usr/bin/env python3
"""A build directory's payload, PROVENANCE and IMAGE must describe the same image and candidate set."""
import hashlib
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fixtures  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("mac_image_check", str(ROOT / "bin/mac-image-check"))
spec = importlib.util.spec_from_loader("mac_image_check", loader)
check = importlib.util.module_from_spec(spec)
loader.exec_module(check)

NAME = "omarchy-2026.09.25-aarch64-apple-silicon-mac-edge-os-package.zip"
LAB = ["profile=lab", "lab_access_sha256=" + "d" * 64]
LAB_NAME = "omarchy-2026.09.25-aarch64-apple-silicon-mac-edge-lab-os-package.zip"
BUILT = "2026-09-25T03:04:05Z"
INPUTS = {
    "candidate_set": "apple-test-fixture",
    "candidate_source_commit": "a" * 40,
    "candidate_signer": "E" * 40,
    "candidate_receipt_sha256": "1" * 64,
    "candidate_manifest_sha256": "2" * 64,
    "omarchy_channel": "edge",
}
# A channel build's inputs (mac-image-inputs resolve --channel-set): the set is named for its database.
CHANNEL_DB = "7" * 64
CHANNEL_INPUTS = {
    "channel_set": f"omarchy-edge-{CHANNEL_DB[:12]}",
    "channel_signer": "40DFB630FF42BCFFB047046CF0134EE680CAC571",
    "channel_manifest_sha256": "8" * 64,
    "omarchy_channel": "edge",
    "omarchy_db_sha256": CHANNEL_DB,
}
CHANNEL_PACKAGES = [
    ("omarchy-dev", "4.0.0.r6971.g492b883-1", "omarchy-candidates", "omarchy-dev-4.0.0.r6971.g492b883-1-aarch64.pkg.tar.zst",
     "3" * 64),
    ("omarchy-mac", "0.1.4-1", "omarchy-candidates", "omarchy-mac-0.1.4-1-aarch64.pkg.tar.zst", "6" * 64),
    ("omarchy-keyring", "20251027-1", "omarchy", "omarchy-keyring-20251027-1-any.pkg.tar.zst", "9" * 64),
    ("glibc", "2.43-1", "core", "glibc-2.43-1-aarch64.pkg.tar.xz", "5" * 64),
]
PACKAGES = [
    ("omarchy", "4.0.0-1", "omarchy-candidates", "omarchy-4.0.0-1-aarch64.pkg.tar.xz", "3" * 64),
    ("uboot-aurora", "2026.07-1", "omarchy-candidates", "uboot-aurora-2026.07-1-aarch64.pkg.tar.zst", "4" * 64),
    ("glibc", "2.43-1", "core", "glibc-2.43-1-aarch64.pkg.tar.xz", "5" * 64),
]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def package_set(packages) -> str:
    lines = sorted((f"{n}|{v}|{s}\n" for n, v, _, _, s in packages), key=str.encode)
    return hashlib.sha256("".join(lines).encode()).hexdigest()


def build(out: Path, packages=PACKAGES, candidates=PACKAGES[:2], inspection="passed", image_edit=None,
          profile=(), provenance_profile=None, name=None, inspection_digest=None, target_edit=None,
          sources=(("omarchy", "a" * 40),), runtime_sources=None, provenance_edit=None, legacy=False,
          inputs=INPUTS, pin=None, report_edit=None, recovery_app=None, builder_commit="c" * 40, grow=None) -> None:
    """A build directory as build-mac-image writes it; LEGACY, as a build before builds recorded their identity.
    With CHANNEL_INPUTS, a channel build: the image target names the channel set and database, INSPECTION the
    set it inspected, and PIN (the test image pin, empty for a public image) is recorded. BUILDER_COMMIT names
    the builder, and GROW (fixtures.write_payload) changes the payload's bytes, as a builder fix would."""
    lab = "profile=lab" in profile
    channel = "channel_set" in inputs
    name = name or (LAB_NAME if lab else NAME)
    zip_path = out / name
    fixtures.write_payload(zip_path, check.BOOT_UUID, check.ROOT_UUID, recovery_app=recovery_app, grow=grow)
    (out / "installer_data.json").write_text(json.dumps(check.expected_metadata(name, recovery_app is not None)))
    if channel:
        target = {"channel_set": inputs["channel_set"], "channel_db_sha256": inputs["omarchy_db_sha256"]}
    else:
        target = {"candidate_set": inputs["candidate_set"], "candidate_source_commit": inputs["candidate_source_commit"]}
    target.update({"builder_commit": builder_commit, "builder_tree_clean": "true",
                   "image_profile": "lab" if lab else "release" if channel else "test",
                   "package_set_sha256": package_set(packages), "built": BUILT})
    if legacy:
        del target["package_set_sha256"], target["built"]
    if target_edit:
        target = target_edit(target)
    report = {"result": inspection, "runtime_sources": dict(sources) if runtime_sources is None else runtime_sources}
    if target is not None:
        report["image_target"] = target
    digest = inspection_digest or next((l.split("=", 1)[1] for l in profile if l.startswith("lab_access_sha256=")), None)
    if digest:
        report.update(profile="lab", lab_access_sha256=digest)
    if channel:
        report.update(set_kind="channel", set=inputs["channel_set"], database_sha256=inputs["omarchy_db_sha256"])
    if report_edit:
        report_edit(report)
    (out / "INSPECTION").write_text(json.dumps(report))
    (out / "inputs").write_text("format=2\n" + "".join(f"{k}={v}\n" for k, v in inputs.items()))
    lines = ["format=2", "kind=mac-image", "lane=edge", "kernel=linux-aurora", "platform=apple-silicon",
             "builder_commit=" + builder_commit, "builder_tree_clean=true", f"inputs_sha256={sha(out / 'inputs')}"]
    lines += [f"input.{k}={v}" for k, v in inputs.items()]
    lines += [f"candidate={n}|{v}|{f}|{s}" for n, v, _, f, s in candidates]
    lines += [f"candidate_source={n}|{c}" for n, c in sources]
    lines += ["hardware_setup=build", *([] if pin is None else [f"test_image_pin={pin}"]),
              *(profile if provenance_profile is None else provenance_profile),
              f"package_set_sha256={package_set(packages)}", *([] if legacy else [f"built={BUILT}"]),
              f"package_count={len(packages)}"]
    lines += [f"package={i}|{'|'.join(p)}" for i, p in enumerate(packages, 1)]
    lines += [f"installer_data_sha256={sha(out / 'installer_data.json')}", f"inspection_sha256={sha(out / 'INSPECTION')}",
              f"payload={name}|{zip_path.stat().st_size}|{sha(zip_path)}"]
    if provenance_edit:
        lines = provenance_edit(lines)
    (out / "PROVENANCE").write_text("\n".join(lines) + "\n")
    digests = check.check_payload("edge", zip_path, out / "installer_data.json", "lab" if name == LAB_NAME else "release")
    image = ["format=2", "lane=edge", "platform=apple-silicon", "builder_commit=" + builder_commit, "builder_tree_clean=true",
             *[f"{k}={v}" for k, v in inputs.items()],
             "hardware_setup=build", *([] if pin is None else [f"test_image_pin={pin}"]), *profile,
             f"package_set_sha256={package_set(packages)}",
             *([] if legacy else [f"built={BUILT}"]),
             *[f"image_sha256={m}|{d}" for m, d in sorted(digests.items())], f"input_digest={sha(out / 'inputs')}"]
    if image_edit:
        image = image_edit(image)
    (out / "IMAGE").write_text("\n".join(image) + "\n")


class ChannelBuildDirectoryTest(unittest.TestCase):
    """A channel build's records name the channel set and database, and describe a public image: one that
    carries the native Recovery app, which the installer finishes every public install with."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "out"
        self.recovery = Path(self.tmp.name) / "recovery"
        self.inputs = dict(CHANNEL_INPUTS, recovery_app_manifest_sha256=fixtures.make_recovery_app(self.recovery))

    def tearDown(self):
        self.tmp.cleanup()

    def build(self, **options):
        if self.out.exists():
            shutil.rmtree(self.out)
        self.out.mkdir()
        settings = dict(packages=CHANNEL_PACKAGES, candidates=CHANNEL_PACKAGES[:2], inputs=self.inputs, pin="",
                        sources=(("omarchy-mac", "b" * 40),), recovery_app=self.recovery)
        build(self.out, **{**settings, **options})

    def test_a_channel_build_is_a_public_image_with_its_database_and_digests(self):
        self.build()
        check.check_provenance("edge", self.out)
        check.check_descriptor("edge", self.out)
        image = check.parse_fields(self.out / "IMAGE")
        self.assertEqual(image["omarchy_db_sha256"], [CHANNEL_DB])
        self.assertEqual(image["channel_set"], [CHANNEL_INPUTS["channel_set"]])
        self.assertNotIn("candidate_set", image)
        self.assertNotIn("profile", image)
        self.assertEqual(image["test_image_pin"], [""])
        provenance = check.parse_fields(self.out / "PROVENANCE")
        self.assertEqual(provenance["input.omarchy_db_sha256"], [CHANNEL_DB])
        self.assertEqual({p.split("|")[1]: p.split("|")[5] for p in provenance["package"]},
                         {n: s for n, _, _, _, s in CHANNEL_PACKAGES}, "PROVENANCE records every package's digest")

    def test_a_record_naming_a_retired_hardware_profile_is_refused(self):
        for target, edit in (("PROVENANCE", lambda lines: [*lines, "hardware_profile=neo"]),
                             ("IMAGE", lambda lines: [*lines, "hardware_profile=neo"])):
            with self.subTest(target):
                self.build(**{"provenance_edit" if target == "PROVENANCE" else "image_edit": edit})
                with self.assertRaisesRegex(check.CheckError, "retired hardware profile"):
                    getattr(check, "check_provenance" if target == "PROVENANCE" else "check_descriptor")("edge", self.out)

    def test_a_channel_build_carries_the_native_recovery_app(self):
        self.build()
        metadata = json.loads((self.out / "installer_data.json").read_text())
        self.assertEqual(metadata["os_list"][0]["recovery_gui"], "omarchy-native-v1")
        image = check.parse_fields(self.out / "IMAGE")
        self.assertEqual(image["recovery_app_manifest_sha256"], [self.inputs["recovery_app_manifest_sha256"]])
        members = {value.split("|", 1)[0] for value in image["image_sha256"]}
        for member in (check.recovery_app.PREFIX + check.recovery_app.MANIFEST,
                       check.recovery_app.PREFIX + check.recovery_app.APP + "/Contents/MacOS/OmarchyRecovery"):
            self.assertIn(member, members)

    def test_a_channel_build_without_the_native_recovery_app_is_refused(self):
        # The regression this guards: an edge image whose installs the app cannot hand to Recovery.
        self.build(inputs=CHANNEL_INPUTS, recovery_app=None)
        for mode, where in (("provenance", "PROVENANCE"), ("descriptor", "the inputs record")):
            with self.subTest(mode=mode), self.assertRaisesRegex(
                    check.CheckError, f"{where} pins no native Recovery app: a channel image must carry it"):
                getattr(check, f"check_{mode}")("edge", self.out)
        with self.assertRaisesRegex(check.CheckError, "pins no native Recovery app"):
            check.image_name("edge", self.out)
        result = subprocess.run([sys.executable, str(ROOT / "bin/mac-image-check"), "descriptor", "edge", str(self.out)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("pins no native Recovery app", result.stderr)

    def test_a_channel_build_must_carry_the_recovery_app_its_inputs_pin(self):
        # Pinned in the inputs, but the payload neither declares nor carries it.
        self.build(recovery_app=None)
        for mode in ("provenance", "descriptor"):
            with self.subTest(mode=mode), self.assertRaisesRegex(check.CheckError, "Recovery metadata does not match"):
                getattr(check, f"check_{mode}")("edge", self.out)

    def test_native_recovery_inventory_is_bound_to_image_inputs(self):
        recovery = Path(self.tmp.name) / "recovery"
        digest = fixtures.make_recovery_app(recovery)
        inputs = dict(CHANNEL_INPUTS, recovery_app_manifest_sha256=digest)
        self.build(inputs=inputs, recovery_app=recovery)
        check.check_provenance("edge", self.out)
        check.check_descriptor("edge", self.out)
        # A coherent output cannot silently substitute another recorded input.
        self.build(inputs=dict(inputs, recovery_app_manifest_sha256="0" * 64), recovery_app=recovery)
        for mode in ("provenance", "descriptor"):
            with self.subTest(mode=mode), self.assertRaisesRegex(check.CheckError, "pinned input"):
                getattr(check, f"check_{mode}")("edge", self.out)

    def test_descriptor_binds_the_recovery_input_to_actual_inputs_not_only_the_copy(self):
        recovery = Path(self.tmp.name) / "recovery"
        digest = fixtures.make_recovery_app(recovery)
        self.build(inputs=dict(CHANNEL_INPUTS, recovery_app_manifest_sha256=digest), recovery_app=recovery)
        inputs = self.out / "inputs"
        inputs.write_text(inputs.read_text().replace(f"recovery_app_manifest_sha256={digest}\n", ""))
        for filename, key in (("IMAGE", "input_digest"), ("PROVENANCE", "inputs_sha256")):
            path = self.out / filename
            path.write_text("\n".join(f"{key}={sha(inputs)}" if line.startswith(key + "=") else line
                                      for line in path.read_text().splitlines()) + "\n")
        with self.assertRaisesRegex(check.CheckError, "pinned inputs"):
            check.check_descriptor("edge", self.out)

    def test_a_channel_build_carries_no_test_image_pin(self):
        self.build(pin="omarchy omarchy-mac omarchy-settings")
        for mode in ("provenance", "descriptor"):
            with self.subTest(mode=mode), self.assertRaisesRegex(check.CheckError, "test image pin for a channel image"):
                getattr(check, f"check_{mode}")("edge", self.out)

    def test_a_channel_build_is_never_a_lab_image(self):
        self.build(profile=LAB)
        for mode in ("provenance", "descriptor"):
            with self.subTest(mode=mode), self.assertRaisesRegex(check.CheckError, "lab profile for a channel image"):
                getattr(check, f"check_{mode}")("edge", self.out)

    def test_the_image_target_and_inspection_must_name_the_channel_build(self):
        cases = {
            "the image target's channel_db_sha256 does not match": dict(
                target_edit=lambda t: dict(t, channel_db_sha256="0" * 64)),
            "the image target's profile does not match": dict(target_edit=lambda t: dict(t, image_profile="test")),
            "INSPECTION did not inspect the channel set": dict(
                report_edit=lambda r: r.update(database_sha256="0" * 64)),
        }
        for message, options in cases.items():
            with self.subTest(message=message):
                self.build(**options)
                with self.assertRaisesRegex(check.CheckError, message):
                    check.check_provenance("edge", self.out)

    def test_records_name_one_kind_of_set(self):
        mixed = dict(self.inputs, candidate_set="apple-test-fixture")
        self.build(inputs=mixed)
        for mode in ("provenance", "descriptor"):
            with self.subTest(mode=mode), self.assertRaisesRegex(check.CheckError, "names no single set"):
                getattr(check, f"check_{mode}")("edge", self.out)

    def test_the_payload_admits_every_board_and_carries_the_j700_boot_payloads(self):
        self.build()
        metadata = json.loads((self.out / "installer_data.json").read_text())
        target = metadata["os_list"][0]
        self.assertNotIn("allowed_device_classes", target)
        self.assertNotIn("excluded_device_classes", target)
        self.assertEqual(target["supported_fw"], ["13.5", "14.8.3", "26.6.2"])
        for member in ("esp/aurora/stage1-j700.bin", "esp/m1n1/boot-j700.bin"):
            with self.subTest(member):
                fixtures.write_payload(self.out / NAME, check.BOOT_UUID, check.ROOT_UUID, omit=(member,),
                                       recovery_app=self.recovery)
                with self.assertRaisesRegex(check.CheckError, "missing members"):
                    check.check_payload("edge", self.out / NAME, self.out / "installer_data.json")

    def test_the_payload_passes_the_engine_contract(self):
        self.build()
        metadata = json.loads((self.out / "installer_data.json").read_text())
        engine = json.loads((ROOT.parent / "Engine/installer_data.json").read_text())
        # A public image adds only the native Recovery declaration to the engine's template.
        engine["os_list"][0].update(package=NAME, supported_fw=["13.5", "14.8.3", "26.6.2"],
                                    recovery_gui="omarchy-native-v1")
        self.assertEqual(metadata, engine)

    def test_a_public_image_is_named_for_its_lane_date_package_set_and_bytes(self):
        self.build()
        set_id, image_id = package_set(CHANNEL_PACKAGES)[:12], sha(self.out / NAME)[:12]
        expected = f"omarchy-mac-edge-2026.09.25-{set_id}-{image_id}"
        self.assertEqual(check.image_name("edge", self.out), expected)
        self.assertRegex(expected, check.IMAGE_NAME)
        result = subprocess.run([sys.executable, str(ROOT / "bin/mac-image-check"), "name", "edge", str(self.out)],
                                capture_output=True, text=True)
        self.assertEqual((result.returncode, result.stdout), (0, f"{expected}\n"))

    def test_a_builder_fix_on_the_same_packages_and_day_gets_its_own_name(self):
        # Run 39328508742: the same edge package set on the same UTC day as a published
        # release, rebuilt by a newer builder, was named like it and its upload refused.
        self.build()
        first = check.image_name("edge", self.out)
        self.build(builder_commit="d" * 40, grow={"esp/limine.conf": 1})
        second = check.image_name("edge", self.out)
        self.assertNotEqual(second, first)
        self.assertEqual(second.rsplit("-", 1)[0], first.rsplit("-", 1)[0], "the same lane, date and package set id")
        self.assertEqual(second.rsplit("-", 1)[1], sha(self.out / NAME)[:12])

    def test_the_same_bytes_keep_their_name(self):
        self.build()
        first = check.image_name("edge", self.out)
        copy = Path(self.tmp.name) / "copy"
        shutil.copytree(self.out, copy)
        self.assertEqual(check.image_name("edge", copy), first)
        self.assertEqual(check.image_name("edge", self.out), first)

    def test_the_name_pattern(self):
        for name, matches in (("omarchy-mac-edge-2026.10.19-71656b684cbe-0123456789ab", True),
                              ("omarchy-mac-stable-2026.10.19-71656b684cbe-0123456789ab", True),
                              ("omarchy-mac-edge-2026.10.19-71656b684cbe", False),
                              ("omarchy-mac-edge-2026.10.19-71656b684cbe-0123456789AB", False),
                              ("omarchy-mac-edge-2026.10.19-71656b684cbe-0123456789a", False),
                              ("omarchy-mac-beta-2026.10.19-71656b684cbe-0123456789ab", False)):
            with self.subTest(name):
                self.assertEqual(check.IMAGE_NAME.fullmatch(name) is not None, matches)

    def test_any_changed_package_changes_the_name(self):
        self.build()
        first = check.image_name("edge", self.out)
        changed = [*CHANNEL_PACKAGES[:3], ("glibc", "2.43-2", "core", "glibc-2.43-2-aarch64.pkg.tar.xz", "a" * 64)]
        self.build(packages=changed)
        self.assertNotEqual(check.image_name("edge", self.out), first)

    def test_only_a_public_image_is_named(self):
        cases = {
            "lab profile for a channel image": dict(profile=LAB),
            "test image pin for a channel image": dict(pin="omarchy omarchy-mac omarchy-settings"),
        }
        for message, options in cases.items():
            with self.subTest(message=message):
                self.build(**options)
                with self.assertRaisesRegex(check.CheckError, message):
                    check.image_name("edge", self.out)


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

    def test_a_candidate_set_image_is_not_named(self):
        build(self.out)
        with self.assertRaisesRegex(check.CheckError, "only a public image"):
            check.image_name("edge", self.out)

    def test_a_candidate_installed_from_another_repository(self):
        packages = [PACKAGES[0], ("uboot-aurora", "2026.07-1", "asahi-alarm", PACKAGES[1][3], PACKAGES[1][4]), PACKAGES[2]]
        build(self.out, packages=packages)
        with self.assertRaisesRegex(check.CheckError, "uboot-aurora from the candidate set"):
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

    def test_image_and_provenance_record_one_build_time(self):
        def replace_built(value):
            return lambda lines: [f"built={value}" if l.startswith("built=") else l for l in lines]
        cases = (
            ("descriptor", dict(image_edit=replace_built("2026-09-25T03:04:06Z")), "IMAGE built does not match"),
            ("descriptor", dict(image_edit=lambda lines: [l for l in lines if not l.startswith("built=")]),
             "IMAGE built does not match PROVENANCE"),
            ("descriptor", dict(image_edit=lambda lines: lines + [f"built={BUILT}"]), "does not name built once"),
            ("descriptor", dict(image_edit=replace_built("2026-09-25 03:04:05")), "IMAGE names no UTC build time"),
            ("provenance", dict(provenance_edit=lambda lines: [l for l in lines if not l.startswith("built=")]),
             "records a build identity that PROVENANCE does not"),
            ("provenance", dict(provenance_edit=lambda lines: lines + [f"built={BUILT}"]), "does not name built once"),
            ("provenance", dict(provenance_edit=replace_built("2026-02-30T03:04:05Z")),
             "PROVENANCE names no UTC build time"),
        )
        for mode, options, message in cases:
            with self.subTest(message=message, mode=mode):
                shutil.rmtree(self.out)
                self.out.mkdir()
                build(self.out, **options)
                with self.assertRaisesRegex(check.CheckError, message):
                    getattr(check, f"check_{mode}")("edge", self.out)

    def test_a_build_from_before_builds_recorded_their_identity_passes(self):
        # Published images (preview-3-f22c43fb7903, the m1 and m2 lab kits) carry no
        # built= in IMAGE or PROVENANCE, and no identity in INSPECTION's image target.
        for profile in ((), LAB):
            with self.subTest(lab=bool(profile)):
                shutil.rmtree(self.out)
                self.out.mkdir()
                build(self.out, legacy=True, profile=profile)
                check.check_provenance("edge", self.out)
                check.check_descriptor("edge", self.out)

    def test_a_partial_build_identity_is_refused(self):
        without_built = lambda lines: [l for l in lines if not l.startswith("built=")]
        cases = (
            ("provenance", dict(legacy=True, target_edit=lambda t: {**t, "package_set_sha256": package_set(PACKAGES)}),
             "records a build identity that PROVENANCE does not"),
            ("provenance", dict(legacy=True, target_edit=lambda t: {**t, "built": BUILT}),
             "records a build identity that PROVENANCE does not"),
            ("provenance", dict(target_edit=lambda t: {k: v for k, v in t.items() if k != "package_set_sha256"}),
             "image target's package_set_sha256 does not match"),
            ("provenance", dict(legacy=True, provenance_edit=lambda lines: lines + [f"built={BUILT}"]),
             "image target's package_set_sha256 does not match"),
            ("descriptor", dict(legacy=True, image_edit=lambda lines: lines + [f"built={BUILT}"]),
             "IMAGE built does not match PROVENANCE"),
            ("descriptor", dict(image_edit=without_built), "IMAGE built does not match PROVENANCE"),
            ("descriptor", dict(provenance_edit=without_built), "IMAGE built does not match PROVENANCE"),
        )
        for mode, options, message in cases:
            with self.subTest(message=message, mode=mode, options=sorted(options)):
                shutil.rmtree(self.out)
                self.out.mkdir()
                build(self.out, **options)
                with self.assertRaisesRegex(check.CheckError, message):
                    getattr(check, f"check_{mode}")("edge", self.out)

    def test_inspection_must_read_this_builds_provenance_in_the_image(self):
        cases = {
            "does not record the image target": lambda t: None,
            "candidate_set does not match": lambda t: {**t, "candidate_set": "apple-test-other"},
            "candidate_source_commit does not match": lambda t: {**t, "candidate_source_commit": "b" * 40},
            "builder_commit does not match": lambda t: {**t, "builder_commit": "d" * 40},
            "builder_tree_clean does not match": lambda t: {**t, "builder_tree_clean": "false"},
            "profile does not match": lambda t: {**t, "image_profile": "lab"},
            "package_set_sha256 does not match": lambda t: {**t, "package_set_sha256": "0" * 64},
            "built does not match": lambda t: {**t, "built": "2026-09-25T03:04:06Z"},
            "image target's built does not match": lambda t: {k: v for k, v in t.items() if k != "built"},
        }
        for message, edit in cases.items():
            with self.subTest(message):
                shutil.rmtree(self.out)
                self.out.mkdir()
                build(self.out, target_edit=edit)
                with self.assertRaisesRegex(check.CheckError, message):
                    check.check_provenance("edge", self.out)

    def test_runtime_sources_must_be_inspections_and_the_sets(self):
        cases = {
            "no source": dict(sources=(), runtime_sources={}),
            "twice": dict(sources=(("omarchy", "a" * 40), ("omarchy", "a" * 40)), runtime_sources={"omarchy": "a" * 40}),
            "not a set package": dict(sources=(("omarchy", "a" * 40), ("glibc", "a" * 40))),
            "not a commit": dict(sources=(("omarchy", "a" * 39),)),
            "not inspection's": dict(runtime_sources={"omarchy": "a" * 40, "uboot-aurora": "b" * 40}),
            "another runtime commit": dict(sources=(("omarchy", "b" * 40),)),
        }
        for label, change in cases.items():
            with self.subTest(label):
                shutil.rmtree(self.out)
                self.out.mkdir()
                build(self.out, **change)
                with self.assertRaisesRegex(check.CheckError, "candidate source|runtime"):
                    check.check_provenance("edge", self.out)
        shutil.rmtree(self.out)
        self.out.mkdir()
        build(self.out, sources=(("omarchy", "a" * 40), ("uboot-aurora", "b" * 40)))
        check.check_provenance("edge", self.out)

    def test_a_changed_payload(self):
        build(self.out)
        with (self.out / NAME).open("ab") as stream:
            stream.write(b"\0")
        with self.assertRaises(check.CheckError):
            check.check_provenance("edge", self.out)

    def test_a_lab_image_on_edge_passes(self):
        build(self.out, profile=LAB)
        check.check_provenance("edge", self.out)
        check.check_descriptor("edge", self.out)

    def test_lab_and_release_payloads_are_told_apart_by_name(self):
        build(self.out, profile=LAB)
        with self.assertRaisesRegex(check.CheckError, "names a lab image"):
            check.check_payload("edge", self.out / LAB_NAME, self.out / "installer_data.json", "release")
        shutil.rmtree(self.out)
        self.out.mkdir()
        build(self.out)
        with self.assertRaisesRegex(check.CheckError, "does not name a lab image"):
            check.check_payload("edge", self.out / NAME, self.out / "installer_data.json", "lab")
        for mode in ("provenance", "descriptor"):
            with self.subTest(mode=mode):
                shutil.rmtree(self.out)
                self.out.mkdir()
                build(self.out, name=LAB_NAME)
                with self.assertRaisesRegex(check.CheckError, "names a lab image"):
                    getattr(check, f"check_{mode}")("edge", self.out)

    def test_inspection_must_carry_the_recorded_lab_digest(self):
        build(self.out, profile=LAB, inspection_digest="e" * 64)
        with self.assertRaisesRegex(check.CheckError, "INSPECTION's lab access digest"):
            check.check_provenance("edge", self.out)

    def test_a_lab_record_must_be_whole_and_agree(self):
        cases = {
            "names no lab access digest": dict(profile=["profile=lab"]),
            "not one profile=lab": dict(profile=["profile=release"]),
            "lab access on a release image": dict(profile=[LAB[1]]),
            "profile does not match PROVENANCE": dict(profile=LAB, provenance_profile=[]),
            "lab_access_sha256 does not match PROVENANCE": dict(
                profile=LAB, provenance_profile=["profile=lab", "lab_access_sha256=" + "e" * 64]),
        }
        for message, options in cases.items():
            with self.subTest(message=message):
                shutil.rmtree(self.out)
                self.out.mkdir()
                build(self.out, **options)
                with self.assertRaisesRegex(check.CheckError, message):
                    check.check_descriptor("edge", self.out)

    def test_a_lab_image_is_refused_on_release_lanes(self):
        for lane in ("rc", "stable"):
            with self.subTest(lane=lane):
                with self.assertRaisesRegex(check.CheckError, f"refused on the {lane} lane"):
                    check.check_recorded_profile(lane, {"profile": ["lab"], "lab_access_sha256": ["f" * 64]}, "IMAGE")
                with self.assertRaisesRegex(check.CheckError, f"refused on the {lane} lane"):
                    check.check_profile(lane, "lab")
                self.assertEqual(check.main(["mac-image-check", "tree", lane, str(self.out), "--candidates",
                                             str(self.out), "--profile", "lab"]), 1)
        self.assertEqual(check.main(["mac-image-check", "provenance", "edge", str(self.out), "--profile", "lab"]), 64)

    def test_payload_without_limine(self):
        build(self.out)
        payload = self.out / "unpacked"
        payload.mkdir()
        subprocess.run(["bsdtar", "-xf", str(self.out / NAME), "-C", str(payload)], check=True)
        (payload / "esp/EFI/BOOT/BOOTAA64.EFI").write_bytes(fixtures.payload_pe(b"GRUB 2.12\n"))
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



class BootTreeTest(unittest.TestCase):
    """The installed /boot and ESP of a fresh image: Limine only, no GRUB."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        boot = self.root / "boot"
        for name in (f"vmlinuz-{check.KERNEL}", f"initramfs-{check.KERNEL}.img", "efi/EFI/BOOT/BOOTAA64.EFI",
                     "efi/m1n1/boot.bin", "efi/limine.conf", f"efi/EFI/Linux/omarchy_{check.KERNEL}.efi"):
            (boot / name).parent.mkdir(parents=True, exist_ok=True)
            (boot / name).write_bytes(b"x")
        (boot / "efi/omarchy").mkdir()
        (boot / "omarchy").mkdir()
        (self.root / "etc/default").mkdir(parents=True)
        (self.root / "etc/default/limine").write_text(
            f'ESP_PATH="/boot/efi"\nKERNEL_CMDLINE[default]="root=UUID={check.ROOT_UUID} rw '
            'rootflags=subvol=@,x-systemd.device-timeout=0 zswap.enabled=0 rootfstype=btrfs quiet splash"\n')

    def test_limine_only(self):
        check.check_boot(self.root)

    def test_grub_leftovers_fail(self):
        for name in ("boot/grub", "etc/default/update-grub"):
            with self.subTest(name=name):
                path = self.root / name
                path.mkdir() if name == "boot/grub" else path.write_text('TARGET="/boot/grub/grub-aa64.efi"\n')
                with self.assertRaisesRegex(check.CheckError, "without GRUB"):
                    check.check_boot(self.root)
                path.rmdir() if path.is_dir() else path.unlink()

    def test_command_line_without_the_root_fails(self):
        (self.root / "etc/default/limine").write_text('KERNEL_CMDLINE[default]="quiet splash"\n')
        with self.assertRaisesRegex(check.CheckError, "lacks root=UUID="):
            check.check_boot(self.root)

    def test_command_line_set_or_extended(self):
        line = (f"root=UUID={check.ROOT_UUID} rw rootflags=subvol=@,x-systemd.device-timeout=0 "
                "zswap.enabled=0 rootfstype=btrfs quiet splash")
        for setting in (f'KERNEL_CMDLINE[default]="{line}"', f'KERNEL_CMDLINE[default]+="{line}"',
                        f'KERNEL_CMDLINE[default] += "{line}"', f'  KERNEL_CMDLINE["default"]+="{line}"'):
            with self.subTest(setting=setting):
                (self.root / "etc/default/limine").write_text(f'ESP_PATH="/boot/efi"\n{setting}\n')
                check.check_boot(self.root)

    def test_command_line_refusals(self):
        line = f"root=UUID={check.ROOT_UUID} rw rootflags=subvol=@ rootfstype=btrfs quiet"
        cases = {
            "two lines": (f'KERNEL_CMDLINE[default]+="{line}"\nKERNEL_CMDLINE[default]+="splash"\n', "one generated"),
            "a set and an extended line": (f'KERNEL_CMDLINE[default]="{line}"\nKERNEL_CMDLINE[default]+="splash"\n',
                                           "one generated"),
            "an unquoted line": (f'KERNEL_CMDLINE[default]+={line.split()[0]}\n', "unreadable"),
            "the kernel's own key": (f'KERNEL_CMDLINE[default]+="{line}"\nKERNEL_CMDLINE[{check.KERNEL}]+="quiet"\n',
                                     f"replaces the {check.KERNEL}"),
            "an extended line without the root": ('KERNEL_CMDLINE[default]+="rw quiet splash"\n', "lacks root=UUID="),
        }
        for name, (text, pattern) in cases.items():
            with self.subTest(name):
                (self.root / "etc/default/limine").write_text(text)
                with self.assertRaisesRegex(check.CheckError, pattern):
                    check.check_boot(self.root)

    def test_command_line_with_drop_ins(self):
        """check_boot reads the line limine-entry-tool composes, drop-ins included."""
        line = f"root=UUID={check.ROOT_UUID} rw rootflags=subvol=@ rootfstype=btrfs quiet"
        (self.root / "etc/default/limine").write_text(f'KERNEL_CMDLINE[default]+="{line}"\n')
        drop_ins = self.root / "etc/limine-entry-tool.d"
        drop_ins.mkdir(parents=True)
        drop_in = drop_ins / "omarchy-defaults.conf"
        drop_in.write_text('KERNEL_CMDLINE[default]+=" quiet splash"\nKERNEL_CMDLINE[default]+=" initramfs_async=0"\n')
        check.check_boot(self.root)
        evil = "root=UUID=00000000-0000-0000-0000-000000000000"
        cases = {
            "a second root": (f'KERNEL_CMDLINE[default]+=" {evil}"\n', "unsafe"),
            "encryption": ('KERNEL_CMDLINE[default]+=" rd.luks.name=x=root"\n', "unsafe"),
            "ro": ('KERNEL_CMDLINE[default]+=" ro"\n', "unsafe"),
            "the kernel's own key": (f'KERNEL_CMDLINE[{check.KERNEL}]+="quiet"\n', f"replaces the {check.KERNEL}"),
        }
        for name, (text, pattern) in cases.items():
            with self.subTest(name):
                (drop_ins / "50-extra.conf").write_text(text)
                with self.assertRaisesRegex(check.CheckError, pattern):
                    check.check_boot(self.root)
        with self.subTest("the defaults resetting it"):
            (drop_ins / "50-extra.conf").write_text(f'KERNEL_CMDLINE[default]+=" {evil}"\n')
            (self.root / "etc/default/limine").write_text(f'KERNEL_CMDLINE[default]="{line}"\n')
            check.check_boot(self.root)


class SyncDatabasesTest(unittest.TestCase):
    """First boot resolves packages offline from the sync databases the image keeps."""

    REPOSITORIES = ("omarchy", "asahi-alarm", "core", "extra", "alarm", "aur")

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        (self.root / "etc").mkdir()
        (self.root / "etc/pacman.conf").write_text(
            "[options]\nSigLevel = Required DatabaseOptional\n"
            + "".join(f"\n[{name}]\nInclude = /etc/pacman.d/mirrorlist\n" for name in self.REPOSITORIES)
            + "\n# [core-debug]\n")
        self.sync = self.root / "var/lib/pacman/sync"
        self.sync.mkdir(parents=True)
        for name in self.REPOSITORIES:
            (self.sync / f"{name}.db").write_bytes(b"db")

    def test_one_database_per_configured_repository(self):
        check.check_sync_databases(self.root)

    def test_refusals(self):
        cases = (
            ("no sync databases", lambda: shutil.rmtree(self.sync), "keeps no pacman sync databases"),
            ("a missing one", lambda: (self.sync / "aur.db").unlink(), "are not its pacman.conf's"),
            ("the build's candidates", lambda: (self.sync / "omarchy-candidates.db").write_bytes(b"db"),
             "are not its pacman.conf's"),
            ("the empty fork placeholder", lambda: (self.sync / "omarchy-aarch64.db").write_bytes(b"db"),
             "are not its pacman.conf's"),
            ("a leftover lock", lambda: (self.sync / "db.lck").write_bytes(b""), "are not its pacman.conf's"),
            ("an empty database", lambda: (self.sync / "core.db").write_bytes(b""), "not a regular, non-empty file"),
            ("a linked database", lambda: ((self.sync / "core.db").unlink(), (self.sync / "core.db").symlink_to("extra.db")),
             "not a regular, non-empty file"),
        )
        for label, edit, message in cases:
            with self.subTest(label):
                self.setUp()
                edit()
                with self.assertRaisesRegex(check.CheckError, message):
                    check.check_sync_databases(self.root)

if __name__ == "__main__":
    unittest.main()
