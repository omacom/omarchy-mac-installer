import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("installed", ROOT / "image-builder/builder/verify_installed_system.py")
installed = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installed)


class AuroraPackageRolesTests(unittest.TestCase):
    def inspect(self, replacements):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in installed.REQUIRED_PACKAGES:
                renamed, provides = replacements.get(name, (name, []))
                path = root / "var/lib/pacman/local" / renamed / "desc"
                path.parent.mkdir(parents=True)
                path.write_text("%NAME%\n" + renamed + "\n\n%VERSION%\n0.4.4.aurora2-1\n\n%PROVIDES%\n"
                                + "\n".join(provides) + "\n\n")
            verification = installed.Verification()
            installed.check_packages(verification, root)
            return verification

    def test_exact_audio_and_bless_replacements_fill_legacy_roles(self):
        result = self.inspect({
            "asahi-audio": ("aurora-audio", ["asahi-audio=4.1.aurora2"]),
            "alsa-ucm-conf-asahi": ("alsa-ucm-conf-aurora", ["alsa-ucm-conf-asahi=10.aurora2"]),
            "asahi-bless": ("aurora-bless", ["asahi-bless=0.4.4.aurora2"]),
        })
        self.assertEqual(result.failed, [], result.checks)

    def test_other_package_cannot_claim_audio_role(self):
        result = self.inspect({"asahi-audio": ("impostor", ["asahi-audio=100"])} )
        self.assertIn("packages-required-present", result.failed)

    def test_every_image_takes_the_aurora_stack_as_mandatory_authenticated_candidates(self):
        import json
        policy = json.loads((ROOT / "image-builder/builder/candidate-trust/policy.json").read_text())
        self.assertEqual(set(policy["support_packages"]), {
            "aurora-audio", "alsa-ucm-conf-aurora", "speakerguardd", "omarchy-aurora-firmware",
            "mesa-aurora", "vulkan-aurora", "aurora-bless",
        })
        self.assertNotIn("hardware_profiles", policy)
        self.assertIn("m1n1-aurora", policy["boot_packages"])
        self.assertIn("uboot-aurora", policy["boot_packages"])
        self.assertTrue({"m1n1", "aura", "m1n1-neo", "uboot-asahi"} <= set(policy["refused_packages"]))
        self.assertEqual(policy["minimum_versions"]["m1n1-aurora"], "1.6.1.aurora20-3")
        # Every supported Mac's kernel device tree, one per board of scripts/supported-models.json,
        # plus the developer boards a bring-up image may boot.
        manifest = json.loads((ROOT / "scripts/supported-models.json").read_text())
        expected = manifest["supported"] + list(manifest.get("developer", {}))
        self.assertEqual(sorted("apple," + tree.split("-", 1)[1] for tree in policy["device_trees"]), sorted(expected))
        self.assertFalse((ROOT / "image-builder/audio").exists())
        self.assertFalse((ROOT / "image-builder/firmware-ordering").exists())


if __name__ == "__main__":
    unittest.main()
