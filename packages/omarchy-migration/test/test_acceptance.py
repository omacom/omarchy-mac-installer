"""The first end-to-end fixture: a synthetic Try home exported and restored onto a native-style home.

Exercises collection through the real Try policy, encryption, review and
restore together, then compares every source path with the destination.
"""

import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest
import uuid

from omarchy_migration import collection, contract, policy, probe, restore, review
from omarchy_migration.dependency import configured_age

SECRET = b"synthetic-only-otter-maple-window-cobalt"
MTIME = 1720000000123456789
POLICY = json.loads(policy.SHIPPED.read_text())

SOURCE_FILES = {
    # Appearance and personal configuration, copied byte for byte.
    ".config/omarchy/themes/dusk/colors.toml": b'background = "#1e1e2e"\n',
    ".config/omarchy/backgrounds/dusk/1-lake.png": b"\x89PNG synthetic background",
    ".config/ghostty/config": b"font-size = 13\ntheme = dusk\n",
    ".config/weird-tool/settings.ini": b"[mystery]\nkeep_me = exactly-as-is\n",
    ".bashrc": b"source ~/.local/share/omarchy/default/bash/rc\nalias gs='git status'\n",
    ".config/chromium-flags.conf": b"--ozone-platform=wayland\n--force-dark-mode\n",
    # Customized files that also carry Try's known additions.
    ".config/omarchy/shell.json": (
        b'{\n  "version": 1,\n  "idle": {\n    "screensaver": 0,\n    "lock": 0\n  },\n'
        b'  "bar": {\n    "position": "bottom"\n  }\n}\n'),
    ".config/omarchy/extensions/omarchy-menu.jsonc": (
        b'{\n  // my launcher\n  "launch.notes": {"label": "Notes", "action": "obsidian"},\n'
        b'  "setup.try-omarchy": {"label": "Try Omarchy Settings", "action": "omarchy-native-settings"},\n'
        b'  "setup.security.touch-id": {"label": "Touch ID", "action": "try-omarchy-touch-id"},\n'
        b'  "setup.move-to-native-install": {"label": "Move to Native Install"},\n'
        b'  "update.firmware": {"label": "Firmware", "when": "! omarchy-hw-vm"}\n}\n'),
    # Hardware, VM and transient state the policy excludes.
    ".config/hypr/monitors.lua": b"monitor = Virtual-1, preferred, auto, 2\n",
    ".local/state/omarchy/toggles/hypr/zz-try-omarchy.lua": b'pcall(dofile, "/usr/share/try-omarchy/hyprland.lua")\n',
    ".config/fcitx5/config": b"[Hotkey/ActivateKeys]\n0=Hangul\n",
    ".local/state/try-omarchy/menu-rows.json": b"{}",
    ".local/state/omarchy/clipboard-history.json": b'["a copied password"]',
    ".local/share/mise/installs/node/22/bin/node": b"ELF synthetic runtime",
    # Credential stores held back without an adapter.
    ".ssh/id_ed25519": b"FAKE-SSH-PRIVATE-KEY",
    ".config/chromium/Default/Cookies": b"FAKE-CHROMIUM-COOKIES",
}

# What every source path should look like afterwards.
EXPECTED = {
    ".config/omarchy/themes/dusk/colors.toml": "identical",
    ".config/omarchy/backgrounds/dusk/1-lake.png": "identical",
    ".config/ghostty/config": "identical",
    ".config/weird-tool/settings.ini": "identical",
    ".bashrc": "conflict-destination-kept",
    ".config/chromium-flags.conf": "identical",
    ".config/omarchy/shell.json": "transformed",
    ".config/omarchy/extensions/omarchy-menu.jsonc": "transformed",
    ".config/hypr/monitors.lua": "excluded-destination-kept",
    ".local/state/omarchy/toggles/hypr/zz-try-omarchy.lua": "excluded",
    ".config/fcitx5/config": "excluded",
    ".local/state/try-omarchy/menu-rows.json": "excluded",
    ".local/state/omarchy/clipboard-history.json": "excluded",
    ".local/share/mise/installs/node/22/bin/node": "excluded",
    ".ssh/id_ed25519": "held-out",
    ".config/chromium/Default/Cookies": "held-out",
    "Work": "inert-link",
    "Projects/app/README.md": "identical",
    "Projects/app/run.sh": "identical",
    "Projects/app/notes-untracked.txt": "identical",
}

TRANSFORMED = {
    ".config/omarchy/shell.json": b'{\n  "version": 1,\n  "bar": {\n    "position": "bottom"\n  }\n}\n',
}

DESTINATION_DEFAULTS = {
    ".config/hypr/monitors.lua": b"monitor = eDP-1, 2560x1664@60, auto, 1.6\n",
    ".bashrc": b"source ~/.local/share/omarchy/default/bash/rc\n",
}


def git(*arguments, cwd):
    environment = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(cwd), "GIT_CONFIG_NOSYSTEM": "1",
                   "GIT_AUTHOR_NAME": "Synthetic", "GIT_AUTHOR_EMAIL": "synthetic@example.invalid",
                   "GIT_COMMITTER_NAME": "Synthetic", "GIT_COMMITTER_EMAIL": "synthetic@example.invalid",
                   "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z"}
    # No background maintenance: it would change the repository mid-test.
    return subprocess.run(["git", "-c", "init.defaultBranch=main", "-c", "maintenance.auto=false", "-c", "gc.auto=0",
                           *arguments], cwd=cwd, env=environment,
                          check=True, capture_output=True, text=True).stdout


class AcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.age = configured_age()
        if cls.age is None:
            raise unittest.SkipTest("set OMARCHY_TEST_AGE and OMARCHY_TEST_AGE_SHA256 for real age fixtures")
        if shutil.which("git") is None:
            raise unittest.SkipTest("git is required for the project fixture")
        if os.geteuid() == 0:
            raise unittest.SkipTest("unprivileged acceptance fixture")

    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix="migration-acceptance-")
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.home, self.target, self.job = self.root / "try-home", self.root / "native-home", self.root / "job"
        for directory in (self.home, self.target, self.job, self.root / "snapshots"):
            directory.mkdir(mode=0o700)
        for name, data in SOURCE_FILES.items():
            path = self.home / name
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            path.write_bytes(data)
            path.chmod(0o600)
        (self.home / "Work").symlink_to("/mnt/mac")
        project = self.home / "Projects/app"
        project.mkdir(parents=True, mode=0o755)
        (project / "README.md").write_bytes(b"# app\n")
        (project / "run.sh").write_bytes(b"#!/bin/bash\ntouch SHOULD-NOT-RUN\n")
        (project / "run.sh").chmod(0o755)
        git("init", "-q", cwd=project)
        git("add", "README.md", "run.sh", cwd=project)
        git("commit", "-q", "-m", "synthetic", cwd=project)
        (project / "README.md").write_bytes(b"# app\n\nuncommitted edit\n")
        (project / "notes-untracked.txt").write_bytes(b"draft\n")
        for path in [*self.home.rglob("*")]:
            try:
                os.utime(path, ns=(MTIME, MTIME), follow_symlinks=False)
            except FileNotFoundError:
                pass  # A transient Git lock file
        for name, data in DESTINATION_DEFAULTS.items():
            path = self.target / name
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            path.write_bytes(data)

    def export(self):
        request = {"schema": collection.REQUEST_SCHEMA, "request_id": str(uuid.uuid4()),
                   "selection": [{"source": "", "archive": ""}], "selected_adapters": [], "selected_mounts": [], "selected_share_stores": []}
        ciphertext = self.root / "bundle.age"
        with collection.collect_fixture(self.home, request, POLICY, snapshot_parent=self.root / "snapshots") as snapshot:
            manifest = snapshot.manifest
            encrypted = probe.encrypt(self.age, SECRET, ciphertext,
                                      lambda stream: probe.write_archive(stream, manifest, snapshot.paths))
        receipt = {"schema": contract.RECEIPT, "request_id": request["request_id"], "export_id": manifest["export_id"],
                   "policy_revision": POLICY["revision"],
                   "bundle": {"format": contract.BUNDLE_FORMAT, "schema": contract.BUNDLE,
                              "bytes": encrypted["bytes"], "sha256": encrypted["sha256"]},
                   "estimates": {"expanded_bytes": sum(entry.get("bytes", 0) for entry in manifest["entries"]),
                                 "entries": len(manifest["entries"])}}
        contract.validate(receipt)
        return request, manifest, ciphertext, receipt

    def restore(self, ciphertext, receipt):
        with restore.verified_bundle(self.age, SECRET, ciphertext) as bundle:
            with restore.Restorer(bundle, self.target, self.job) as importer:
                actions = importer.plan()
                plan = review.plan_document(bundle, actions, receipt, 1000, importer._binding)
                results = importer.apply(actions)
                report = review.report_document(plan, results, review.job_identity(importer._binding), bundle)
        return plan, report, {result.path: result for result in results}

    def comparison(self, manifest, results, originals):
        """One row per source path: what the destination holds afterwards."""
        rows = {}
        exceptions = {item["source"]: item for item in manifest["provenance"]["collection"]["exceptions"]}
        for name in EXPECTED:
            source, destination = self.home / name, self.target / name
            # Stores and tree exclusions are reported once, at their root.
            parts = name.split("/")
            exception = next((exceptions["/".join(parts[:depth])] for depth in range(len(parts), 0, -1)
                              if "/".join(parts[:depth]) in exceptions), None)
            if exception and exception["outcome"] in ("held-out", "excluded"):
                kept = name in DESTINATION_DEFAULTS and destination.read_bytes() == DESTINATION_DEFAULTS[name]
                rows[name] = f"{exception['outcome']}-destination-kept" if kept else (
                    exception["outcome"] if not os.path.lexists(destination) else "unexpected-destination")
            elif exception and exception["outcome"] == "inert-link":
                rows[name] = "inert-link" if not os.path.lexists(destination) else "unexpected-link"
            elif results.get(name) is not None and results[name].status == "conflict":
                kept = destination.read_bytes() == DESTINATION_DEFAULTS.get(name)
                rows[name] = "conflict-destination-kept" if kept else "conflict-destination-changed"
            elif exception and exception["outcome"] == "transformed":
                original = self.target / originals / name
                ok = (original.read_bytes() == source.read_bytes()
                      and (name not in TRANSFORMED or destination.read_bytes() == TRANSFORMED[name]))
                rows[name] = "transformed" if ok else "transformed-mismatch"
            else:
                same = (destination.read_bytes() == source.read_bytes()
                        and stat.S_IMODE(destination.stat().st_mode) == stat.S_IMODE(source.stat().st_mode)
                        and destination.stat().st_mtime_ns == source.stat().st_mtime_ns)
                rows[name] = "identical" if same else "changed"
        return rows

    def test_try_home_restores_exactly_except_enumerated_changes(self):
        request, manifest, ciphertext, receipt = self.export()
        archived = {entry["path"] for entry in manifest["entries"]}
        held = (".ssh", ".config/chromium")
        self.assertFalse(any(path == root or path.startswith(root + "/") for path in archived for root in held))
        self.assertIn(".config/chromium-flags.conf", archived)  # a sibling name, not the store
        plan, report, results = self.restore(ciphertext, receipt)
        originals = manifest["provenance"]["originals"]
        self.assertEqual(originals, f"{collection.ORIGINALS_ROOT}/{request['request_id']}")
        self.assertEqual(self.comparison(manifest, results, originals), EXPECTED)

        menu = json.loads((self.target / ".config/omarchy/extensions/omarchy-menu.jsonc").read_text().replace("// my launcher", ""))
        self.assertEqual(menu, {"launch.notes": {"label": "Notes", "action": "obsidian"}})
        self.assertIn("// my launcher", (self.target / ".config/omarchy/extensions/omarchy-menu.jsonc").read_text())

        source_project, restored_project = self.home / "Projects/app", self.target / "Projects/app"
        self.assertEqual(git("rev-parse", "HEAD", cwd=restored_project), git("rev-parse", "HEAD", cwd=source_project))
        self.assertEqual(git("status", "--porcelain", cwd=restored_project), git("status", "--porcelain", cwd=source_project))
        self.assertFalse((self.target / "Projects/app/SHOULD-NOT-RUN").exists())

        # Nothing else arrived: every regular file is accounted for.
        restored = {path.relative_to(self.target).as_posix() for path in self.target.rglob("*")
                    if path.is_file() and not path.is_symlink()}
        git_files = {path.relative_to(self.home).as_posix() for path in (self.home / "Projects/app/.git").rglob("*")
                     if path.is_file() and not path.is_symlink()}
        copies = {f"{originals}/{name}" for name, row in EXPECTED.items() if row == "transformed"}
        delivered = {name for name, row in EXPECTED.items() if row in ("identical", "transformed")}
        self.assertEqual(restored, delivered | set(DESTINATION_DEFAULTS) | copies | git_files)
        for copy in copies:
            path = self.target / copy
            self.assertEqual((stat.S_IMODE(path.stat().st_mode), path.stat().st_mtime_ns), (0o600, MTIME))
        for path in restored:
            data = (self.target / path).read_bytes()
            self.assertNotIn(b"FAKE-SSH-PRIVATE-KEY", data)
            self.assertNotIn(b"a copied password", data)

        self.assertEqual(plan["policy_revision"], POLICY["revision"])
        self.assertEqual(plan["actions"]["conflict"], 1)
        outcomes = {item["id"]: item for item in report["categories"]}
        self.assertEqual((outcomes["configuration"]["outcome"], outcomes["configuration"]["reasons"]),
                         ("partial", ["destination_exists"]))
        self.assertEqual(outcomes["files-and-projects"]["outcome"], "restored")
        self.assertEqual(outcomes["files-and-projects"]["omitted"], 1)

    def test_retry_after_a_user_edit_keeps_the_edit_and_reports_it(self):
        _, _, ciphertext, receipt = self.export()
        self.restore(ciphertext, receipt)
        edited = self.target / ".config/ghostty/config"
        edited.write_bytes(b"font-size = 15\n")
        plan, report, results = self.restore(ciphertext, receipt)
        self.assertEqual(edited.read_bytes(), b"font-size = 15\n")
        self.assertEqual(results[".config/ghostty/config"].status, "conflict")
        self.assertEqual(plan["actions"]["create"], 0)


if __name__ == "__main__":
    unittest.main()
