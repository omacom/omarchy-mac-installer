#!/usr/bin/env python3
"""Channel mode takes only the pinned database's archives for an enabled lane, each signed by the Omarchy package key."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fixtures  # noqa: E402

ROOT = fixtures.ROOT
channel_set = fixtures.load("channel_set", "builder/channel_set.py")
candidate_set = fixtures.candidate_set
# What edge has always served for each runtime role.
EDGE_RUNTIME = {"omarchy": "omarchy-dev", "omarchy-settings": "omarchy-settings-dev", "omarchy-mac": "omarchy-mac",
                "omarchy-mac-boot": "omarchy-mac-boot"}


class ChannelSetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.base = Path(cls.tmp.name)
        cls.candidate = fixtures.Signer(cls.base / "candidate")
        cls.channel = fixtures.Signer(cls.base / "channel", "Omarchy fixture <pkgs@example.invalid>")
        cls.other = fixtures.Signer(cls.base / "other", "Someone else <other@example.invalid>")
        cls.trust = fixtures.channel_trust(cls.base, cls.candidate, cls.channel)

    @classmethod
    def tearDownClass(cls):
        for signer in (cls.candidate, cls.channel, cls.other):
            signer.close()
        cls.tmp.cleanup()

    def setUp(self):
        self.work = Path(tempfile.mkdtemp(dir=self.base))

    def snapshot(self, **options) -> tuple[Path, str]:
        return fixtures.make_channel(self.work / "edge", self.channel, **options)

    def freeze(self, database: Path, sha256: str, lane: str = "edge") -> dict:
        return channel_set.snapshot(database, sha256, lane, self.work / "edge/packages", self.work / "set", self.trust)

    def refused(self, message: str, database: Path, sha256: str, lane: str = "edge") -> None:
        with self.assertRaisesRegex(ValueError, message):
            self.freeze(database, sha256, lane)

    def test_a_pinned_database_and_signed_archives_make_a_public_set(self):
        database, sha256 = self.snapshot(extra=[("omarchy-keyring", "20251027-1")])
        summary = self.freeze(database, sha256)
        self.assertEqual(summary["kind"], "channel")
        self.assertIs(summary["candidate_only"], False)
        self.assertEqual(summary["set"], f"omarchy-edge-{sha256[:12]}")
        self.assertEqual(summary["database_sha256"], sha256)
        self.assertEqual(summary["signer"], self.channel.fingerprint.upper())
        packages = {p["name"]: p for p in summary["packages"]}
        expected = {fixtures.CHANNEL_NAMES.get(role, role) for role in fixtures.VERSIONS} | {"aura"}
        self.assertEqual(set(packages), expected,
                         "the set is the policy's runtime and boot packages and the Stage 1 source, nothing else")
        for name, package in packages.items():
            self.assertEqual(package["sha256"], candidate_set.digest(self.work / "edge/packages" / package["filename"]))
            self.assertEqual(package["origin"], "channel")
        self.assertEqual(packages["omarchy-dev"]["role"], "omarchy")
        self.assertEqual(packages["omarchy-settings-dev"]["role"], "omarchy-settings")
        self.assertNotIn("source_commit", packages["omarchy-dev"], "the -dev runtime ships no source revision")
        self.assertEqual(packages["omarchy-mac"]["source_commit"], fixtures.SOURCE)
        self.assertEqual(packages["omarchy-mac-boot"]["source_commit"], fixtures.SOURCE)
        manifest = self.work / "set/manifest.json"
        self.assertEqual(candidate_set.digest(manifest), summary["manifest_sha256"])
        self.assertEqual(json.loads(manifest.read_text())["database_sha256"], sha256)
        self.assertEqual(json.loads((self.work / "set/import.json").read_text()), summary)
        self.assertFalse(any(path.stat().st_mode & 0o222 for path in (self.work / "set").iterdir()),
                         "the frozen set is read-only")
        self.assertEqual(fixtures.test_image_pin.pinned(summary), [], "a public image carries no test image pin")

    def test_the_channel_takes_the_aurora_stack_for_every_mac(self):
        entries = {}
        policy, channel = channel_set.load_policy()
        for role, name in channel["runtime_packages"].items():
            entries[name] = {"NAME": [name], "ARCH": ["aarch64"], "PROVIDES": [role]}
        for name in fixtures.POLICY["boot_packages"] + fixtures.POLICY["support_packages"] + ["aura"]:
            entries[name] = {"NAME": [name], "ARCH": ["aarch64"]}
        chosen = channel_set.resolve(entries, policy, channel)
        self.assertEqual({entry["NAME"][0] for _, _, entry in chosen}, set(entries))
        self.assertEqual({entry["NAME"][0]: group for _, group, entry in chosen if entry["NAME"][0] in ("aura", "m1n1-aurora")},
                         {"aura": "boot", "m1n1-aurora": "stage1"})
        self.assertEqual(sum(group == "support" for _, group, _ in chosen), len(fixtures.POLICY["support_packages"]))
        for name in ("m1n1-aurora", "uboot-aurora", "mesa-aurora", "vulkan-aurora", "aurora-bless", "speakerguardd"):
            self.assertIn(name, entries)

    def test_the_channel_set_is_the_unified_aurora_set_with_the_dev_runtime(self):
        database, sha256 = self.snapshot()
        summary = self.freeze(database, sha256)
        manifest = json.loads((self.work / "set/manifest.json").read_text())
        self.assertNotIn("hardware_profile", summary)
        self.assertNotIn("hardware_profile", manifest)
        names = {p.get("role", p["name"]): p["name"] for p in summary["packages"]}
        self.assertEqual(names["omarchy"], "omarchy-dev")
        self.assertEqual(names["omarchy-settings"], "omarchy-settings-dev")
        for package in ("aura", "m1n1-aurora", "uboot-aurora", "mesa-aurora", "vulkan-aurora", "aurora-bless"):
            self.assertIn(package, names)
        self.assertNotIn("uboot-asahi", names)
        self.assertEqual(set(channel_set.files(database, "edge", self.trust)),
                         {p["filename"] for p in summary["packages"]})
        described = channel_set.describe(database, "edge", self.work / "edge/packages", self.trust)
        self.assertEqual(described["manifest_sha256"], summary["manifest_sha256"])
        inspection = fixtures.load("inspection", "builder/inspection.py")
        imported = inspection.Candidates(self.work / "set")
        report = {}
        inspection.check_supported_models(self.work, imported, report, fixtures.POLICY)
        self.assertEqual(len(report["supported_models"]), 35)
        self.assertIn("t8140-j700", report["supported_models"])
        self.assertIn("t8122-j613", report["supported_models"])

    def test_an_edge_database_still_on_the_aurora_silicon_m1n1_is_refused(self):
        # Until omacom/omarchy-pkgs#1513 publishes, edge carries m1n1-aurora 1.6.1.aurora1.
        database, sha256 = self.snapshot(versions={"m1n1-aurora": "1.6.1.aurora1-3"})
        self.refused("m1n1-aurora 1.6.1.aurora1-3 is below the minimum 1.6.1.aurora20-3", database, sha256)

    def test_edge_installs_aura_and_takes_the_neo_stage1_from_m1n1_aurora(self):
        # omacom/omarchy-pkgs#3025: on edge aura is the boot loader (with the 7.3 kernel). New
        # installs keep m1n1-aurora's raw Stage 1, the one every edge Neo updated to aura runs.
        database, sha256 = self.snapshot(extra=[("m1n1-neo", "1.6.1.aurora16-1")])
        summary = self.freeze(database, sha256)
        groups = {p["name"]: p["group"] for p in summary["packages"]}
        self.assertEqual((groups["aura"], groups["m1n1-aurora"]), ("boot", "stage1"))
        self.assertNotIn("m1n1-neo", groups)
        self.assertEqual(summary["boot_loader"], {"stage2": "aura", "stage1": "m1n1-aurora"})

    def test_rc_and_stable_keep_m1n1_aurora_and_never_take_aura(self):
        for lane, tag in (("rc", fixtures.RC_RELEASE), ("stable", fixtures.STABLE_RELEASE)):
            with self.subTest(lane=lane):
                self.work = Path(tempfile.mkdtemp(dir=self.base))
                database, sha256 = fixtures.make_channel(self.work / "edge", self.channel, lane=lane)
                summary = channel_set.snapshot(database, sha256, lane, self.work / "edge/packages", self.work / "set",
                                               self.trust, tag)
                groups = {p["name"]: p["group"] for p in summary["packages"]}
                self.assertEqual(groups["m1n1-aurora"], "boot")
                self.assertNotIn("aura", groups)
                self.assertNotIn("stage1", groups.values())
                self.assertEqual(summary["boot_loader"], {"stage2": "m1n1-aurora", "stage1": "m1n1-aurora"})

    def test_an_edge_database_with_the_old_aura_is_refused(self):
        # Before #3025 publishes, edge serves aura 0.1.0, which m1n1-aurora replaces on update.
        database, sha256 = self.snapshot(versions={"aura": "0.1.0.r20261023-2"})
        self.refused(r"aura 0.1.0.r20261023-2 is below the minimum 1.0.0b13.r20261118-1", database, sha256)

    def test_an_edge_database_without_m1n1_aurora_is_refused(self):
        # The Neo's Stage 1 comes from m1n1-aurora's archive: an edge without it builds no image.
        policy, channel = channel_set.load_policy()
        entries = {name: {"NAME": [name], "ARCH": ["aarch64"], "PROVIDES": [role]}
                   for role, name in channel["runtime_packages"].items()}
        for name in fixtures.POLICY["boot_packages"] + fixtures.POLICY["support_packages"] + ["aura"]:
            entries[name] = {"NAME": [name], "ARCH": ["aarch64"]}
        del entries["m1n1-aurora"]
        with self.assertRaisesRegex(ValueError, "the channel database does not list m1n1-aurora, the Stage 1 source"):
            channel_set.resolve(entries, policy, channel)

    def test_the_edge_set_refuses_an_aura_without_its_stage2_or_signature(self):
        for defect, message in (("unsigned", "unsigned package: aura-"), ("wrong-payload", "usr/lib/asahi-boot/m1n1.bin is not in aura"),
                                ("stage1-payload", "usr/share/m1n1-aurora/stage1/m1n1-raw.bin is not in m1n1-aurora")):
            with self.subTest(defect=defect):
                self.work = Path(tempfile.mkdtemp(dir=self.base))
                contents = fixtures.channel_contents()
                if defect == "wrong-payload":
                    del contents["aura"]["usr/lib/asahi-boot/m1n1.bin"]
                elif defect == "stage1-payload":
                    del contents["m1n1-aurora"]["usr/share/m1n1-aurora/stage1/m1n1-raw.bin"]
                database, sha256 = self.snapshot(contents=contents, unsigned=("aura",) if defect == "unsigned" else ())
                self.refused(message, database, sha256)
    def test_an_edge_set_on_a_7_3_or_newer_kernel_boots_on_aura(self):
        # The #3025 pairing: the kernel guard never fires, since edge installs aura, not m1n1-aurora.
        for kernel in ("7.3.0.aurora0testedgeC-1", "7.4.1.aurora2-1"):
            with self.subTest(kernel=kernel):
                self.work = Path(tempfile.mkdtemp(dir=self.base))
                database, sha256 = self.snapshot(versions={"linux-aurora": kernel, "linux-aurora-headers": kernel})
                groups = {p["name"]: p["group"] for p in self.freeze(database, sha256)["packages"]}
                self.assertEqual((groups["aura"], groups["m1n1-aurora"]), ("boot", "stage1"))

    def test_rc_and_stable_sets_are_left_alone_by_the_edge_kernel_guard(self):
        # They change only through promotion; the guard is edge's alone.
        for lane, tag in (("rc", fixtures.RC_RELEASE), ("stable", fixtures.STABLE_RELEASE)):
            for kernel in ("7.2.11.aurora16-1", "7.3.0.aurora0testedgeC-1"):
                with self.subTest(lane=lane, kernel=kernel):
                    self.work = Path(tempfile.mkdtemp(dir=self.base))
                    database, sha256 = fixtures.make_channel(self.work / "edge", self.channel, lane=lane,
                                                             versions={"linux-aurora": kernel,
                                                                       "linux-aurora-headers": kernel})
                    summary = channel_set.snapshot(database, sha256, lane, self.work / "edge/packages",
                                                   self.work / "set", self.trust, tag)
                    versions = {p["name"]: p["version"] for p in summary["packages"]}
                    self.assertEqual(versions["linux-aurora"], kernel)
                    self.assertIn("m1n1-aurora", versions)

    def test_the_guard_compares_the_kernel_by_version_not_by_name(self):
        def chosen(kernel, loader="m1n1-aurora"):
            return [("linux-aurora", "boot", {"NAME": ["linux-aurora"], "VERSION": [kernel]}),
                    (loader, "boot", {"NAME": [loader], "VERSION": ["1.6.1.aurora23-4"]})]
        # A release candidate sorts below 7.3 by vercmp, with or without a separator: still 7.3.
        for kernel in ("7.3rc1-1", "7.3-rc1-1", "7.3.aurora1-1", "7.3.0.aurora0testedgeD-1", "7.10.0-1", "8.0.0-1",
                       "aurora-1"):
            with self.subTest(kernel=kernel), self.assertRaisesRegex(
                    ValueError, f"^edge pairs linux-aurora {kernel} with m1n1-aurora; m1n1-aurora is not qualified"):
                channel_set.refuse_untested_boot_pair(chosen(kernel), "edge")
        for kernel in ("7.2.11.aurora16-1", "7.2rc1-1", "7.2.0-1", "6.19.9-1", "6.30-1", "2:7.2.11-1"):
            channel_set.refuse_untested_boot_pair(chosen(kernel), "edge")
        channel_set.refuse_untested_boot_pair(chosen("7.3.0.aurora0testedgeC-1", "aura"), "edge")
        for lane in ("rc", "stable"):
            channel_set.refuse_untested_boot_pair(chosen("7.3.0.aurora0testedgeC-1"), lane)


    def test_the_channel_rejects_missing_unsigned_and_wrong_boot_payloads(self):
        for defect in ("missing", "unsigned", "wrong-key", "wrong-payload", "replaced"):
            with self.subTest(defect=defect):
                self.work = Path(tempfile.mkdtemp(dir=self.base))
                options = {}
                if defect == "missing":
                    options["entry_edit"] = lambda name, text: text.replace("%NAME%\nm1n1-aurora\n", "%NAME%\nother\n")
                elif defect == "unsigned":
                    options["unsigned"] = ("m1n1-aurora",)
                elif defect == "wrong-key":
                    options["signers"] = {"m1n1-aurora": self.candidate}
                elif defect == "wrong-payload":
                    contents = fixtures.channel_contents()
                    del contents["m1n1-aurora"]["usr/share/m1n1-aurora/stage1/m1n1-raw.bin"]
                    options["contents"] = contents
                else:
                    # Only the Asahi boot chain: the Aurora stack is gone from the channel.
                    options["entry_edit"] = lambda name, text: text.replace("%NAME%\nuboot-aurora\n", "%NAME%\nuboot-asahi\n")
                database, sha256 = self.snapshot(**options)
                with self.assertRaises(ValueError):
                    self.freeze(database, sha256)

    def test_files_lists_the_archives_the_set_needs(self):
        database, sha256 = self.snapshot()
        result = subprocess.run([sys.executable, str(ROOT / "builder/channel_set.py"), "files", "--db", str(database),
                                 "--lane", "edge"], capture_output=True, text=True)
        # The real policy pins the Omarchy key; listing reads only the database and the policy's names.
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sorted(result.stdout.split()), sorted(p.name for p in (self.work / "edge/packages").glob("*.xz")))

    def test_an_unsigned_package_is_refused(self):
        database, sha256 = self.snapshot(unsigned=("omarchy-mac",))
        self.refused("unsigned package: omarchy-mac-", database, sha256)

    def test_a_package_signed_by_another_key_is_refused(self):
        database, sha256 = self.snapshot(signers={"linux-aurora": self.other})
        self.refused("invalid signature: linux-aurora-", database, sha256)

    def test_a_package_signed_by_the_candidate_test_key_is_refused(self):
        database, sha256 = self.snapshot(signers={"omarchy-dev": self.candidate})
        self.refused("invalid signature: omarchy-dev-", database, sha256)

    def test_a_tampered_package_is_refused(self):
        database, sha256 = self.snapshot()
        archive = next((self.work / "edge/packages").glob("uboot-aurora-*.xz"))
        archive.write_bytes(archive.read_bytes() + b"\0")
        self.refused("is not the archive the channel database lists", database, sha256)

    def test_a_tampered_package_signed_again_is_refused(self):
        database, sha256 = self.snapshot()
        archive = next((self.work / "edge/packages").glob("m1n1-aurora-*.xz"))
        fixtures.write_archive(archive, "m1n1-aurora", fixtures.VERSIONS["m1n1-aurora"],
                               {"usr/lib/asahi-boot/m1n1.bin": b"another m1n1"}, [])
        self.channel.sign(archive)
        self.refused("is not the archive the channel database lists", database, sha256)

    def test_a_database_changed_after_pinning_is_refused(self):
        database, pinned = self.snapshot()
        shutil.rmtree(self.work / "edge")
        database, _ = self.snapshot(versions={"omarchy-mac": "0.1.5-1"})
        self.refused("the channel database changed since it was pinned", database, pinned)

    def test_a_runtime_that_does_not_provide_its_role_is_refused(self):
        database, sha256 = self.snapshot(entry_edit=lambda name, text: text.replace("%PROVIDES%\nomarchy\n\n", "")
                                         if name == "omarchy-dev" else text)
        self.refused("omarchy-dev does not provide omarchy", database, sha256)

    def test_a_database_without_a_set_package_is_refused(self):
        database, sha256 = self.snapshot(entry_edit=lambda name, text: text.replace("%NAME%\nuboot-aurora", "%NAME%\nuboot")
                                         if name == "uboot-aurora" else text)
        self.refused("uboot-aurora", database, sha256)

    def test_archive_metadata_must_match_the_database(self):
        database, sha256 = self.snapshot(entry_edit=lambda name, text: text.replace("%ARCH%\naarch64", "%ARCH%\nany")
                                         if name == "linux-aurora" else text)
        self.refused("linux-aurora is not an aarch64 package", database, sha256)

    def test_only_the_policys_lanes_are_built(self):
        database, sha256 = self.snapshot()
        self.refused("unknown lane beta", database, sha256, "beta")
        self.assertFalse((self.work / "set").exists(), "a refused lane freezes nothing")
        with self.assertRaisesRegex(ValueError, "unknown lane beta"):
            channel_set.files(database, "beta", self.trust)

    def test_the_stable_lane_takes_the_clean_runtime_of_its_final_tag(self):
        database, sha256 = fixtures.make_channel(self.work / "stable", self.channel, lane="stable")
        summary = channel_set.snapshot(database, sha256, "stable", self.work / "stable/packages", self.work / "set",
                                       self.trust, release_tag=fixtures.STABLE_RELEASE)
        self.assertEqual(summary["set"], f"omarchy-stable-{sha256[:12]}")
        runtime = {p["role"]: (p["name"], p["version"]) for p in summary["packages"] if p["group"] == "runtime"}
        self.assertEqual(runtime["omarchy"], ("omarchy", "4.5.0-1"))
        self.assertEqual(runtime["omarchy-settings"], ("omarchy-settings", "4.5.0-1"))

    def test_the_stable_lane_refuses_a_runtime_that_is_not_its_tag(self):
        database, sha256 = fixtures.make_channel(self.work / "stable", self.channel, lane="stable",
                                                 versions={"omarchy": "4.5.0rc3-1", "omarchy-settings": "4.5.0rc3-1"})
        with self.assertRaisesRegex(ValueError, "is not the release v4.5.0"):
            channel_set.snapshot(database, sha256, "stable", self.work / "stable/packages", self.work / "set",
                                 self.trust, release_tag=fixtures.STABLE_RELEASE)

    def rc_snapshot(self, **options) -> tuple[Path, str]:
        return fixtures.make_channel(self.work / "rc", self.channel, lane="rc", **options)

    def freeze_rc(self, database: Path, sha256: str, tag: str | None = fixtures.RC_RELEASE) -> dict:
        return channel_set.snapshot(database, sha256, "rc", self.work / "rc/packages", self.work / "set", self.trust,
                                    release_tag=tag)

    def test_the_rc_lane_takes_the_clean_runtime_of_its_release(self):
        # The rc database also lists an old -dev pair, as the real one does; it is never chosen.
        database, sha256 = self.rc_snapshot(extra=[("omarchy-dev", "4.0.0.r7195.g3ac4365-1"),
                                                   ("omarchy-settings-dev", "4.0.0.r7195.g3ac4365-1")])
        summary = self.freeze_rc(database, sha256)
        self.assertEqual(summary["set"], f"omarchy-rc-{sha256[:12]}")
        self.assertEqual(summary["channel"], "rc")
        runtime = {p["role"]: (p["name"], p["version"]) for p in summary["packages"] if p["group"] == "runtime"}
        self.assertEqual(runtime["omarchy"], ("omarchy", "4.5.0rc2-1"))
        self.assertEqual(runtime["omarchy-settings"], ("omarchy-settings", "4.5.0rc2-1"))
        self.assertFalse({p["name"] for p in summary["packages"]} & {"omarchy-dev", "omarchy-settings-dev"})
        self.assertEqual(set(channel_set.files(database, "rc", self.trust)), {p["filename"] for p in summary["packages"]})

    def test_the_rc_lane_refuses_edges_runtime(self):
        database, sha256 = self.snapshot()
        with self.assertRaisesRegex(ValueError, "the channel database does not list omarchy$"):
            channel_set.snapshot(database, sha256, "rc", self.work / "edge/packages", self.work / "set", self.trust,
                                 release_tag=fixtures.RC_RELEASE)

    def test_the_rc_lane_takes_a_final_tag_only_with_the_final_runtime(self):
        database, sha256 = fixtures.make_channel(self.work / "rc", self.channel, lane="rc",
                                                 versions={"omarchy": "4.5.0-1", "omarchy-settings": "4.5.0-1"})
        summary = channel_set.snapshot(database, sha256, "rc", self.work / "rc/packages", self.work / "set",
                                       self.trust, release_tag="v4.5.0")
        self.assertEqual(summary["set"], f"omarchy-rc-{sha256[:12]}")
        self.assertEqual(channel_set.lane_inputs("rc", "v4.5.0")["OMARCHY_MIRROR"], "rc")
        self.assertEqual(channel_set.lane_inputs("rc", "v4.5.0")["OMARCHY_IMAGE_REF"], "v4.5.0")
        rc2, rc2_sha256 = fixtures.make_channel(self.work / "rc2", self.channel, lane="rc")
        with self.assertRaisesRegex(ValueError, "is not the release v4.5.0"):
            channel_set.snapshot(rc2, rc2_sha256, "rc", self.work / "rc2/packages", self.work / "set2",
                                 self.trust, release_tag="v4.5.0")

    def test_only_a_final_tag_on_the_rc_lane_takes_a_bare_rc_label(self):
        self.assertEqual(channel_set.release_image_label("rc", "v4.5.0"), "4.5.0-rc")
        self.assertEqual(channel_set.release_image_label("rc", "v4.5.0-rc2"), "4.5.0-rc2")
        self.assertEqual(channel_set.release_image_label("stable", "v4.5.0"), "4.5.0")
        with self.assertRaisesRegex(ValueError, "v4.5.0-rc2 is not a stable release tag"):
            channel_set.release_version("stable", "v4.5.0-rc2")

    def test_the_rc_runtime_must_be_its_tags_release(self):
        database, sha256 = self.rc_snapshot()
        with self.assertRaisesRegex(ValueError, "omarchy 4.5.0rc2-1 is not the release v4.5.0-rc3"):
            self.freeze_rc(database, sha256, "v4.5.0-rc3")
        self.assertFalse((self.work / "set/import.json").exists(), "nothing is frozen for another release")

    def test_a_release_tag_goes_with_its_lane_alone(self):
        database, sha256 = self.rc_snapshot()
        with self.assertRaisesRegex(ValueError, "the rc lane needs a release tag"):
            self.freeze_rc(database, sha256, None)
        edge, edge_sha256 = self.snapshot()
        with self.assertRaisesRegex(ValueError, "the edge lane takes no release tag"):
            channel_set.snapshot(edge, edge_sha256, "edge", self.work / "edge/packages", self.work / "set",
                                 self.trust, release_tag=fixtures.RC_RELEASE)

    def test_a_lane_outside_edge_rc_and_stable_is_refused(self):
        database, sha256 = self.snapshot()
        self.refused("unknown lane beta", database, sha256, "beta")

    def test_each_lane_takes_its_runtime_from_its_own_policy_entry(self):
        # The edge database also lists the clean pair: edge still takes only its -dev one.
        database, sha256 = self.snapshot(extra=[("omarchy", "4.5.0rc2-1"), ("omarchy-settings", "4.5.0rc2-1")])
        names = {p["role"]: p["name"] for p in self.freeze(database, sha256)["packages"] if "role" in p}
        self.assertEqual(names["omarchy"], "omarchy-dev")
        self.assertEqual(names["omarchy-settings"], "omarchy-settings-dev")

    def test_the_edge_set_records_exactly_what_it_did_before_lanes(self):
        database, sha256 = self.snapshot()
        summary = self.freeze(database, sha256)
        manifest = json.loads((self.work / "set/manifest.json").read_text())
        self.assertEqual(set(manifest), {"schema", "kind", "channel", "repository", "database_sha256", "signer",
                                         "packages"})
        self.assertEqual((manifest["schema"], manifest["kind"], manifest["channel"], manifest["repository"]),
                         (1, "channel", "edge", "omarchy"))
        # Plus the lane's boot loader (omacom/omarchy-pkgs#3025 made aura edge's).
        self.assertEqual(set(summary), {"schema", "kind", "set", "candidate_only", "channel", "source_commit",
                                        "signer", "database_sha256", "manifest_sha256", "set_sha256", "packages",
                                        "boot_loader"})
        self.assertEqual(summary["set"], f"omarchy-edge-{sha256[:12]}")
        roles = {p["name"]: p.get("role") for p in summary["packages"] if p["group"] == "runtime"}
        self.assertEqual(roles, {"omarchy-dev": "omarchy", "omarchy-settings-dev": "omarchy-settings",
                                 "omarchy-mac": "omarchy-mac", "omarchy-mac-boot": "omarchy-mac-boot"})

    def test_the_candidate_import_never_accepts_the_channel_key(self):
        receipt = fixtures.make_set(self.work / "set-dir", self.candidate, key_signer=self.channel)
        with self.assertRaisesRegex(ValueError, "invalid signature: signing.json"):
            candidate_set.snapshot(self.work / "set-dir", self.work / "imported", receipt, fixtures.SOURCE,
                                   trust=self.trust)

    def test_installed_channel_packages_are_verified_against_the_pinned_database(self):
        database, sha256 = self.snapshot(extra=[("omarchy-keyring", "20251027-1")])
        cache = self.work / "edge/packages"
        keyring = next(cache.glob("omarchy-keyring-*"))
        self.channel.sign(keyring)
        lines = [f"omarchy-keyring|20251027-1|omarchy|{keyring.name}|{candidate_set.digest(keyring)}",
                 f"glibc|2.43-1|core|glibc-2.43-1-aarch64.pkg.tar.xz|{'5' * 64}"]
        records = self.work / "packages"
        records.write_text("\n".join(lines) + "\n")
        self.assertEqual(channel_set.verify_installed(database, sha256, records, cache, self.trust), 1)
        Path(str(keyring) + ".sig").unlink()
        with self.assertRaisesRegex(ValueError, "unsigned package"):
            channel_set.verify_installed(database, sha256, records, cache, self.trust)
        self.other.sign(keyring)
        with self.assertRaisesRegex(ValueError, "invalid signature"):
            channel_set.verify_installed(database, sha256, records, cache, self.trust)
        with self.assertRaisesRegex(ValueError, "changed since it was pinned"):
            channel_set.verify_installed(database, "0" * 64, records, cache, self.trust)
        records.write_text(lines[0].replace(candidate_set.digest(keyring), "6" * 64) + "\n")
        with self.assertRaisesRegex(ValueError, "is not the pinned channel database's"):
            channel_set.verify_installed(database, sha256, records, cache, self.trust)


class ChannelTrustTest(unittest.TestCase):
    def test_the_policy_pins_the_omarchy_package_key(self):
        policy, channel = channel_set.load_policy()
        listing = subprocess.check_output(["gpg", "--batch", "--show-keys", "--with-colons",
                                           str(ROOT / "builder/keys/omarchy-repository.gpg")], text=True,
                                          stderr=subprocess.DEVNULL)
        fingerprint = next(line.split(":")[9] for line in listing.splitlines() if line.startswith("fpr:"))
        self.assertEqual(channel["primary_fingerprint"], fingerprint, "the channel key is the image's repository key")
        self.assertEqual(channel["signing_fingerprint"], fingerprint)
        self.assertNotEqual(policy["primary_fingerprint"], fingerprint, "the candidate test key is another key")
        self.assertEqual(channel["runtime_packages"], EDGE_RUNTIME)
        self.assertEqual(channel["lane"], "edge", "edge is the default lane")

    def test_the_policy_enables_edge_with_the_dev_runtime_and_rc_and_stable_with_the_clean_one(self):
        lanes = json.loads((ROOT / "builder/candidate-trust/policy.json").read_text())["channel"]["lanes"]
        self.assertEqual(lanes, {"edge": {"runtime_packages": EDGE_RUNTIME,
                                          "boot_loader": {"stage2": "aura", "stage1": "m1n1-aurora"}},
                                 "rc": {"runtime_packages": {role: role for role in EDGE_RUNTIME}},
                                 "stable": {"runtime_packages": {role: role for role in EDGE_RUNTIME}}})

    def test_a_lane_names_its_install_contract_inputs(self):
        self.assertEqual(channel_set.lane_inputs("edge"), {
            "OMARCHY_MIRROR": "edge",
            "OMARCHY_RUNTIME_PACKAGE": "omarchy-dev",
            "OMARCHY_SETTINGS_PACKAGE": "omarchy-settings-dev",
            "OMARCHY_IMAGE_REF": "edge",
        })
        result = self.lane("edge")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "OMARCHY_MIRROR=edge\nOMARCHY_RUNTIME_PACKAGE=omarchy-dev\n"
                                        "OMARCHY_SETTINGS_PACKAGE=omarchy-settings-dev\nOMARCHY_IMAGE_REF=edge\n")

    def test_the_stable_lane_bakes_its_final_tag_as_the_image_ref(self):
        self.assertEqual(channel_set.lane_inputs("stable", "v4.5.0"), {
            "OMARCHY_MIRROR": "stable",
            "OMARCHY_RUNTIME_PACKAGE": "omarchy",
            "OMARCHY_SETTINGS_PACKAGE": "omarchy-settings",
            "OMARCHY_IMAGE_REF": "v4.5.0",
        })
        self.assertEqual(self.lane("stable").stderr, "channel_set: the stable lane needs a release tag\n")
        self.assertIn("unknown lane beta", self.lane("beta").stderr)

    def test_the_rc_lane_bakes_its_release_tag_as_the_image_ref(self):
        self.assertEqual(channel_set.lane_inputs("rc", "v4.5.0-rc2"), {
            "OMARCHY_MIRROR": "rc",
            "OMARCHY_RUNTIME_PACKAGE": "omarchy",
            "OMARCHY_SETTINGS_PACKAGE": "omarchy-settings",
            "OMARCHY_IMAGE_REF": "v4.5.0-rc2",
        })
        result = self.lane("rc", "--release-tag", "v4.5.0-rc2")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "OMARCHY_MIRROR=rc\nOMARCHY_RUNTIME_PACKAGE=omarchy\n"
                                        "OMARCHY_SETTINGS_PACKAGE=omarchy-settings\nOMARCHY_IMAGE_REF=v4.5.0-rc2\n")

    def test_release_tags_are_checked_against_their_lane(self):
        for lane, tag, message in (("rc", None, "the rc lane needs a release tag"),
                                   ("edge", "v4.5.0-rc2", "the edge lane takes no release tag"),
                                   ("rc", "4.5.0-rc2", "is not an rc release tag"),
                                   ("rc", "v4.5-rc2", "is not an rc release tag"),
                                   ("rc", "v4.5.0-rc0", "is not an rc release tag"),
                                   ("rc", "v04.5.0-rc1", "is not an rc release tag"),
                                   ("rc", "v4.5.0-rc2\n", "is not an rc release tag"),
                                   ("stable", None, "the stable lane needs a release tag"),
                                   ("stable", "v4.5.0-rc2", "is not a stable release tag"),
                                   ("stable", "4.5.0", "is not a stable release tag"),
                                   ("stable", "v04.5.0", "is not a stable release tag"),
                                   ("stable", "v4.5.0\n", "is not a stable release tag")):
            with self.subTest(lane=lane, tag=tag):
                with self.assertRaisesRegex(ValueError, message):
                    channel_set.lane_inputs(lane, tag)
        self.assertEqual(self.lane("rc").stderr, "channel_set: the rc lane needs a release tag\n")

    def test_a_malformed_lane_table_is_refused(self):
        policy = json.loads((ROOT / "builder/candidate-trust/policy.json").read_text())
        for defect, lanes in (("the list of lane names lanes were", ["edge"]),
                              ("no lane", {}),
                              ("an unknown lane", {"beta": {"runtime_packages": EDGE_RUNTIME}}),
                              ("a lane without its runtime", {"edge": {}}),
                              ("a runtime missing a role", {"edge": {"runtime_packages": {"omarchy": "omarchy-dev"}}}),
                              ("a lane with an extra input", {"edge": {"runtime_packages": EDGE_RUNTIME,
                                                                       "mirror": "rc"}})):
            with self.subTest(defect=defect), tempfile.TemporaryDirectory() as directory:
                trust = Path(directory) / "trust"
                shutil.copytree(ROOT / "builder/candidate-trust", trust)
                (trust / "policy.json").write_text(json.dumps(dict(policy, channel=dict(policy["channel"], lanes=lanes))))
                with self.assertRaises(ValueError):
                    channel_set.load_policy(trust)

    def lane(self, lane: str, *extra: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(ROOT / "builder/channel_set.py"), "lane", "--lane", lane, *extra],
                              capture_output=True, text=True)


if __name__ == "__main__":
    unittest.main()
