import copy
import importlib.util
from pathlib import Path
import subprocess
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "engine_pin.py"
SPEC = importlib.util.spec_from_file_location("engine_pin", MODULE_PATH)
PIN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PIN)


class EnginePinTests(unittest.TestCase):
    def setUp(self):
        self.lock = PIN.load_lock()

    def test_the_lock_names_its_engine_consistently(self):
        self.assertEqual(PIN.check(self.lock), [])

    def test_nothing_else_repeats_the_pin(self):
        # Every consumer reads the lock, so an engine change touches only the
        # lock and two branches that change the engine conflict only there.
        # Docs and evidence may record the history of past engines.
        digest = self.lock["validation_artifact"]["sha256"]
        result = subprocess.run(["git", "grep", "-l", "-F", digest, "--", ".",
                                 ":!Engine/source-lock.json", ":!docs", ":!evidence", ":!*.md"],
                                cwd=PIN.ROOT, capture_output=True, text=True)
        if result.returncode == 128:
            self.skipTest(f"not a git checkout: {result.stderr.strip()}")
        self.assertEqual(result.stdout.split(), [], "these files repeat the engine digest; read the lock instead")

    def test_the_content_id_ignores_the_name_and_the_built_artifact(self):
        other = copy.deepcopy(self.lock)
        other["downstream_overlay"]["version"] = "v0.9.2-omarchy.99"
        other["validation_artifact"]["filename"] = "installer-v0.9.2-omarchy.99.tar.gz"
        other["validation_artifact"]["sha256"] = "0" * 64
        other["validation_artifact"]["size_bytes"] = 1
        other["status"] = "prose changes do not rename the engine"
        self.assertEqual(PIN.content_id(other), PIN.content_id(self.lock))

    def test_any_build_input_change_gives_a_new_content_id(self):
        cases = {
            "an overlay source": lambda lock: lock["downstream_overlay"]["files"][0].update(sha256="1" * 64),
            "the runtime patch": lambda lock: lock["downstream_overlay"]["patch"].update(sha256="2" * 64),
            "the build recipe": lambda lock: lock["build_recipe"][0].update(sha256="3" * 64),
            "the base engine": lambda lock: lock["incremental_build"].update(base_sha256="4" * 64),
            "the engine metadata": lambda lock: lock["validation_artifact"].update(metadata_sha256="5" * 64),
        }
        for name, change in cases.items():
            with self.subTest(name):
                other = copy.deepcopy(self.lock)
                change(other)
                self.assertNotEqual(PIN.content_id(other), PIN.content_id(self.lock))

    def test_dated_names_carry_the_date_and_the_content_id(self):
        name = PIN.dated_name(self.lock, "20261014")
        self.assertEqual(name, f"v0.9.2-omarchy.20261014.{PIN.content_id(self.lock)}")
        self.assertEqual(PIN.archive_name(name), f"installer-{name}.tar.gz")

    def test_impossible_dates_are_refused(self):
        for date in ("2026101", "20261301", "2026-10-14"):
            with self.subTest(date):
                with self.assertRaises((PIN.PinError, ValueError)):
                    PIN.dated_name(self.lock, date)

    def test_a_dated_name_must_match_the_lock_inputs(self):
        other = copy.deepcopy(self.lock)
        stale = "v0.9.2-omarchy.20261014.000000000000"
        other["downstream_overlay"]["version"] = stale
        other["validation_artifact"]["filename"] = PIN.archive_name(stale)
        problems = PIN.check(other)
        self.assertTrue(any("names content 000000000000" in problem for problem in problems), problems)

    def test_the_archive_name_must_follow_the_version(self):
        other = copy.deepcopy(self.lock)
        other["validation_artifact"]["filename"] = "installer-something-else.tar.gz"
        self.assertTrue(any("expected installer-" in problem for problem in PIN.check(other)))

    def test_name_write_renames_the_engine_and_its_archive(self):
        other = copy.deepcopy(self.lock)
        name = PIN.dated_name(other, "20261014")
        other["downstream_overlay"]["version"] = name
        other["validation_artifact"]["filename"] = PIN.archive_name(name)
        self.assertEqual(PIN.check(other), [])


if __name__ == "__main__":
    unittest.main()
