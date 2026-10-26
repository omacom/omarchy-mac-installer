"""The interactive import asks again after a wrong passphrase, imports only on confirmation, and records it."""

import json
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

from omarchy_migration import contract, fixture, importer
from omarchy_migration.dependency import configured_age

THEME = ".config/example-theme/selected"


class ScriptedTerminal(importer.Terminal):
    def __init__(self, secrets, choice, confirm=False):
        super().__init__(None)
        self.secrets = list(secrets)
        self.choice = choice
        self.answer = confirm
        self.lines = []
        self.prompts = []

    def say(self, text=""):
        self.lines.append(text)

    def heading(self, text):
        self.lines.append(text)

    def secret(self, prompt):
        self.prompts.append(prompt)
        return self.secrets.pop(0) if self.secrets else None

    def choose(self, prompt, options):
        self.lines.append(prompt)
        assert self.choice is None or self.choice in options
        return self.choice

    def confirm(self, prompt):
        self.lines.append(prompt)
        return self.answer

    def text(self):
        return "\n".join(self.lines)


class SummaryTests(unittest.TestCase):
    def test_summary_names_every_category_of_outcome(self):
        summary = {"categories": [{"id": "configuration", "create": 3, "replace": 2, "present": 1, "conflict": 1, "inert": 0},
                                  {"id": "files-and-projects", "create": 4, "replace": 0, "present": 0, "conflict": 0, "inert": 1}],
                   "replaces_default": [".config/a", ".config/b"],
                   "kept": [{"path": ".config/c", "reason": "native_file_changed"}],
                   "left_out": {"held-out": 2, "excluded": 1, "unsupported": 0}}
        lines = importer.summary_lines(summary)
        self.assertEqual(lines[0], "Comes across: 7 files (Settings 3, Files and projects 4)")
        self.assertIn("Replaces untouched Omarchy defaults: 2 files, each with a backup", lines)
        self.assertIn("    .config/a", lines)
        self.assertIn("Stays as it is here, because this Mac's file differs: 1", lines)
        self.assertIn("    .config/c", lines)
        self.assertIn("Already here: 1 files", lines)
        self.assertTrue(lines[-1].startswith("Left out by Try Omarchy's export policy: 4"))

    def test_control_characters_in_paths_are_shown_escaped(self):
        summary = {"categories": [], "replaces_default": ["clear\x1b[2J\x1b[H.conf"],
                   "kept": [{"path": "bell\x07\u202e.txt", "reason": "native_file_changed"}],
                   "left_out": {"held-out": 0, "excluded": 0, "unsupported": 0}}
        text = "\n".join(importer.summary_lines(summary))
        self.assertNotIn("\x1b", text)
        self.assertNotIn("\x07", text)
        self.assertNotIn("\u202e", text)
        self.assertIn("    clear\\x1b[2J\\x1b[H.conf", text)
        self.assertIn("    café/naïve.txt", "\n".join(importer.summary_lines({**summary, "kept": [
            {"path": "café/naïve.txt", "reason": "native_file_changed"}]})))

    def test_long_lists_are_shortened(self):
        summary = {"categories": [], "replaces_default": [f"p{index}" for index in range(30)], "kept": [],
                   "left_out": {"held-out": 0, "excluded": 0, "unsupported": 0}}
        lines = importer.summary_lines(summary)
        self.assertIn(f"    and {30 - importer.SHOWN} more", lines)


@unittest.skipUnless(sys.platform.startswith("linux"), "imports need Linux")
class ImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.age = configured_age()
        if cls.age is None or os.geteuid() == 0:
            raise unittest.SkipTest("set OMARCHY_TEST_AGE and OMARCHY_TEST_AGE_SHA256, and run unprivileged")

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="migration-import-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        policy = fixture.fixture_policy()
        files, links = fixture.examples(policy)
        self.files = files
        request = {"schema": contract.EXPORT_REQUEST, "request_id": str(uuid.uuid4()),
                   "inventory_id": fixture.inventory_id(policy, files, links), "policy_revision": policy["revision"],
                   "selection": {"categories": ["files-and-projects", "configuration"], "credential_stores": [],
                                 "mounts": [], "share_stores": []}}
        self.home = self.root / "home"
        self.home.mkdir(mode=0o700)
        incoming = self.home / ".local/share/omarchy-migration/incoming"
        incoming.mkdir(parents=True, mode=0o700)
        export = self.root / "export"
        receipt = fixture.export_fixture(request, export, self.age, lambda *_a, **_k: None, lambda: False)
        self.export_id = receipt["export_id"]
        self.folder = incoming / self.export_id
        export.rename(self.folder)
        (self.folder / "request.json").unlink()
        self.work = self.root / "work"
        self.work.mkdir(mode=0o700)
        self.skel = self.root / "skel"
        (self.skel / ".config/example-theme").mkdir(parents=True)
        (self.skel / THEME).write_bytes(b"tokyo-night\n")
        (self.home / ".config/example-theme").mkdir(parents=True, mode=0o700)
        (self.home / THEME).write_bytes(b"tokyo-night\n")
        self.handoff = [sys.executable, "-c", f"import json; print(json.dumps({{'work': {str(self.work)!r}}}))"]
        self.commands = self.root / "bin"
        self.commands.mkdir()
        self.calls = self.root / "calls"
        # Omarchy's theme command makes the theme's backgrounds, as the real one does.
        self.command("omarchy-theme-set", 'mkdir -p "$HOME_UNDER_TEST/.local/state/omarchy/current/theme/backgrounds"\n'
                     'touch "$HOME_UNDER_TEST/.local/state/omarchy/current/theme/backgrounds/1-on-pole.webp"\n')
        self.command("omarchy-theme-bg-set", "")
        environment = patch.dict(os.environ, {"PATH": f"{self.commands}{os.pathsep}{os.environ.get('PATH', '')}",
                                              "HOME_UNDER_TEST": str(self.home)})
        environment.start()
        self.addCleanup(environment.stop)
        self.reboots = []

    def command(self, name, body, status=0):
        script = self.commands / name
        script.write_text(f'#!/bin/sh\necho "{name} $*" >>"{self.calls}"\n{body}exit {status}\n')
        script.chmod(0o755)

    def called(self):
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def run_import(self, terminal, handoff=None):
        return importer.run(terminal, self.home, handoff=handoff or self.handoff, defaults=self.skel,
                            reboot=lambda: self.reboots.append(True) or True)

    def test_asks_again_after_a_wrong_passphrase_and_imports_after_confirmation(self):
        terminal = ScriptedTerminal([b"not-the-passphrase", fixture.SECRET], "Import")
        self.assertEqual(self.run_import(terminal), 0)
        text = terminal.text()
        self.assertEqual(len(terminal.prompts), 2)
        self.assertIn("That passphrase doesn't open this export. Try again.", text)
        self.assertIn("Replaces untouched Omarchy defaults: 1 files, each with a backup", text)
        self.assertIn(f"    {THEME}", text)
        created = re.search(r"^Comes across: (\d+) files", text, re.M).group(1)
        self.assertIn(f"Imported {created} files and replaced 1 untouched defaults.", text)
        self.assertIn("Theme: solitude, applied after the import", text)
        self.assertIn("Theme: solitude.", text)
        background = self.home / ".local/state/omarchy/current/theme/backgrounds/1-on-pole.webp"
        self.assertEqual(self.called(), ["omarchy-theme-set solitude", f"omarchy-theme-bg-set {background}"])
        self.assertIn("Reboot when you're ready", text)
        self.assertEqual(self.reboots, [])
        self.assertEqual((self.home / THEME).read_bytes(), self.files[THEME])
        self.assertEqual((self.home / "Projects/demo/untracked.txt").read_bytes(), self.files["Projects/demo/untracked.txt"])
        record = json.loads((self.home / ".local/state/omarchy-migration/imported" / f"{self.export_id}.json").read_text())
        self.assertEqual(record["export_id"], self.export_id)
        self.assertFalse(self.folder.exists())
        self.assertFalse((self.work / "scratch").exists())
        backups = [path for path in (self.work / "jobs" / self.export_id).iterdir() if path.name != "journal.json"
                   and not path.name.endswith(".displaced")]
        self.assertEqual([path.read_bytes() for path in backups], [b"tokyo-night\n"])
        again = ScriptedTerminal([], None)
        self.assertEqual(self.run_import(again), 0)
        self.assertIn("No export from Try Omarchy is waiting.", again.text())

    def test_yes_reboots_and_skips_the_closing_prompt(self):
        terminal = ScriptedTerminal([fixture.SECRET], "Import", confirm=True)
        self.assertEqual(self.run_import(terminal), 0)
        self.assertIn("Reboot now so every app picks up your settings?", terminal.text())
        self.assertEqual(self.reboots, [True])
        self.assertTrue(terminal.rebooting)

    def test_a_theme_omarchy_lacks_is_explained_and_the_import_still_finishes(self):
        self.command("omarchy-theme-set", "", status=1)
        terminal = ScriptedTerminal([fixture.SECRET], "Import")
        self.assertEqual(self.run_import(terminal), 0)
        self.assertIn("The theme solitude couldn't be applied. Choose it from the Omarchy menu.", terminal.text())
        self.assertEqual(self.called(), ["omarchy-theme-set solitude"])

    def test_the_current_theme_is_not_set_again(self):
        (self.home / ".local/state/omarchy/current").mkdir(parents=True)
        (self.home / ".local/state/omarchy/current/theme.name").write_text("solitude\n")
        terminal = ScriptedTerminal([fixture.SECRET], "Import")
        self.assertEqual(self.run_import(terminal), 0)
        self.assertNotIn("Theme: solitude", terminal.text())
        self.assertNotIn("omarchy-theme-set solitude", self.called())

    def test_not_now_leaves_everything_for_the_next_login(self):
        terminal = ScriptedTerminal([fixture.SECRET], "Not now")
        self.assertEqual(self.run_import(terminal), 0)
        self.assertIn("The offer comes back at your next login.", terminal.text())
        self.assertEqual((self.home / THEME).read_bytes(), b"tokyo-night\n")
        self.assertTrue((self.folder / "bundle.age").exists())
        self.assertFalse((self.home / ".local/state/omarchy-migration").exists())
        self.assertFalse((self.home / "Projects").exists())

    def test_an_empty_passphrase_stops_without_importing(self):
        terminal = ScriptedTerminal([b""], "Import")
        self.assertEqual(self.run_import(terminal), 0)
        self.assertFalse((self.home / "Projects").exists())

    def test_dont_offer_again_is_recorded(self):
        terminal = ScriptedTerminal([fixture.SECRET], "Don't offer again")
        self.assertEqual(self.run_import(terminal), 0)
        self.assertTrue((self.home / ".local/state/omarchy-migration/dismissed" / self.export_id).exists())
        self.assertFalse((self.home / "Projects").exists())
        self.assertFalse(self.folder.exists())
        self.assertFalse((self.work / "scratch").exists())
        self.assertIn("The export is still in Try Omarchy's Exports on the Mac.", terminal.text())
        self.assertEqual(importer.waiting_exports(self.home), [])

    def test_a_failed_handoff_is_explained(self):
        failing = [sys.executable, "-c", "import sys; print('{\"error\": \"user_refused\"}', file=sys.stderr); sys.exit(1)"]
        terminal = ScriptedTerminal([], None)
        self.assertEqual(self.run_import(terminal, failing), 1)
        self.assertIn(importer.MESSAGES["user_refused"], terminal.text())
        self.assertTrue((self.folder / "bundle.age").exists())


if __name__ == "__main__":
    unittest.main()
